"""后台常驻服务：socket server + 录音/识别/粘贴状态机。

单线程事件循环处理 socket 请求；toggle 在 IDLE/RECORDING 间切换。
识别+粘贴可能耗时较长，期间通过状态机阻止并发 toggle。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

from voice_paste import __version__
from voice_paste.config import Config, load_config
from voice_paste.ipc import pid_path, socket_path
from voice_paste.notify import notify
from voice_paste.paste import paste_text
from voice_paste.recorder import Recorder, RecorderError
from voice_paste.transcriber import Transcriber

STATE_IDLE = "idle"
STATE_RECORDING = "recording"
STATE_BUSY = "busy"  # 识别/粘贴中


class Daemon:
    def __init__(self, config: Config, preload: bool = True) -> None:
        self.config = config
        self.recorder = Recorder(config.sample_rate, config.channels)
        self.transcriber = Transcriber(config)
        self.state = STATE_IDLE
        self.last_text = ""
        self.last_error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server: socket.socket | None = None
        self._record_started_at = 0.0
        self._preload = preload

    # ---- 生命周期 ----
    def run(self) -> int:
        path = socket_path()
        if self._is_stale_or_running(path):
            print("[voice-paste] daemon 已在运行（socket 被占用）", file=sys.stderr)
            return 1
        path.unlink(missing_ok=True)

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(8)
        self._server.settimeout(1.0)
        os.chmod(path, 0o600)
        pid_path().write_text(str(os.getpid()), encoding="utf-8")

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self._stop.set())

        notify(f"服务已启动 (v{__version__})", enabled=self.config.notifications)

        if self._preload:
            threading.Thread(target=self._preload_model, daemon=True).start()

        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()

        try:
            self._serve_loop()
        finally:
            self._cleanup()
        return 0

    def _preload_model(self) -> None:
        try:
            print("[voice-paste] 正在预加载语音识别模型…", flush=True)
            self.transcriber.load()
            print(f"[voice-paste] 模型已就绪 ({self.transcriber.device_info})", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[voice-paste] 模型预加载失败: {exc}", file=sys.stderr, flush=True)

    def _is_stale_or_running(self, path: Path) -> bool:
        if not path.exists():
            return False
        test = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            test.settimeout(1.0)
            test.connect(str(path))
            return True  # 有人在监听 → 真在运行
        except OSError:
            return False  # 残留 socket 文件
        finally:
            test.close()

    def _cleanup(self) -> None:
        if self.recorder.is_recording:
            self.recorder.abort()
        if self._server is not None:
            self._server.close()
        socket_path().unlink(missing_ok=True)
        pid_path().unlink(missing_ok=True)
        print("[voice-paste] daemon 已退出", flush=True)

    def _watchdog(self) -> None:
        """录音超过 max_seconds 自动停止，避免忘记停止。"""
        while not self._stop.is_set():
            time.sleep(0.5)
            if (self.state == STATE_RECORDING
                    and time.time() - self._record_started_at > self.config.max_seconds):
                print("[voice-paste] 录音超时，自动停止识别", flush=True)
                threading.Thread(target=self._finish_recording, daemon=True).start()

    # ---- socket 循环 ----
    def _serve_loop(self) -> None:
        assert self._server is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                self._handle_conn(conn)

    def _handle_conn(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        try:
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return
            req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self._reply(conn, {"ok": False, "error": "无效请求"})
            return

        resp = self._dispatch(req)
        self._reply(conn, resp)

    def _reply(self, conn: socket.socket, obj: dict) -> None:
        try:
            conn.sendall(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
        except OSError:
            pass

    def _dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd", "")
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "status":
            return {
                "ok": True,
                "state": self.state,
                "version": __version__,
                "model": self.config.model,
                "device": self.transcriber.device_info,
                "recording_seconds": (
                    round(time.time() - self._record_started_at, 1)
                    if self.state == STATE_RECORDING else 0
                ),
                "last_text": self.last_text,
                "last_error": self.last_error,
            }
        if cmd == "toggle":
            return self._toggle()
        if cmd == "stop_daemon":
            self._stop.set()
            return {"ok": True, "message": "daemon 正在退出"}
        return {"ok": False, "error": f"未知命令: {cmd}"}

    # ---- 状态机 ----
    def _toggle(self) -> dict:
        with self._lock:
            if self.state == STATE_BUSY:
                return {"ok": False, "state": self.state,
                        "error": "正在识别/粘贴，请稍候"}
            if self.state == STATE_IDLE:
                return self._start_recording()
            if self.state == STATE_RECORDING:
                # 切换到 BUSY 后在后台执行耗时识别
                self.state = STATE_BUSY
                threading.Thread(target=self._finish_recording_locked_done,
                                 daemon=True).start()
                return {"ok": True, "state": STATE_BUSY, "message": "停止录音，正在识别"}
            return {"ok": False, "error": "未知状态"}

    def _start_recording(self) -> dict:
        try:
            self.recorder.start()
        except RecorderError as exc:
            self.last_error = str(exc)
            notify(f"麦克风不可用: {exc}", urgency="critical",
                   enabled=self.config.notifications)
            return {"ok": False, "state": STATE_IDLE, "error": str(exc)}
        self.state = STATE_RECORDING
        self._record_started_at = time.time()
        self.last_error = ""
        notify("开始录音 🎙", enabled=self.config.notifications)
        return {"ok": True, "state": STATE_RECORDING, "message": "开始录音"}

    def _finish_recording_locked_done(self) -> None:
        """已持锁切到 BUSY 后调用，结束后回到 IDLE。"""
        self._do_transcribe_and_paste()

    def _finish_recording(self) -> None:
        """watchdog 路径：自行处理状态切换。"""
        with self._lock:
            if self.state != STATE_RECORDING:
                return
            self.state = STATE_BUSY
        self._do_transcribe_and_paste()

    def _do_transcribe_and_paste(self) -> None:
        try:
            wav = self.recorder.stop()
        except RecorderError as exc:
            self.last_error = str(exc)
            notify(f"没有识别到文本: {exc}", enabled=self.config.notifications)
            self.state = STATE_IDLE
            return

        try:
            notify("正在识别…", enabled=self.config.notifications)
            result = self.transcriber.transcribe(wav)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            notify(f"识别失败: {exc}", urgency="critical",
                   enabled=self.config.notifications)
            self.state = STATE_IDLE
            return
        finally:
            wav.unlink(missing_ok=True)

        if result.status == "hallucination":
            self.last_text = ""
            self.last_error = f"疑似空音频幻觉，已忽略：{result.raw}"
            notify("疑似空音频幻觉，已忽略（未检测到有效语音）",
                   enabled=self.config.notifications)
            self.state = STATE_IDLE
            return

        if result.status != "ok" or not result.text:
            self.last_text = ""
            notify("没听到声音，请靠近麦克风或说久一点再试",
                   enabled=self.config.notifications)
            self.state = STATE_IDLE
            return

        text = result.text
        self.last_text = text
        result = paste_text(
            text,
            method=self.config.paste_method,
            auto_paste=self.config.auto_paste,
            delay_ms=self.config.paste_delay_ms,
            paste_key=self.config.paste_key,
        )
        preview = text if len(text) <= 40 else text[:40] + "…"
        if result.pasted:
            notify(f"已粘贴：{preview}", enabled=self.config.notifications)
        elif result.clipboard_ok:
            notify(f"已复制到剪贴板（请手动粘贴）：{preview}",
                   enabled=self.config.notifications)
        else:
            self.last_error = result.detail
            notify(f"粘贴失败：{result.detail}", urgency="critical",
                   enabled=self.config.notifications)
        self.state = STATE_IDLE


def run_daemon(preload: bool = True) -> int:
    config = load_config()
    return Daemon(config, preload=preload).run()
