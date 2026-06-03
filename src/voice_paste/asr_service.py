"""常驻 ASR 服务：复用 faster-whisper 模型并提供本机 HTTP API。"""

from __future__ import annotations

import argparse
import base64
import json
import signal
import sys
import tempfile
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from voice_paste import __version__
from voice_paste.config import Config, load_config
from voice_paste.realtime_server import RealtimeWebSocketServer
from voice_paste.transcriber import Transcriber, TranscribeResult


class ASRRuntime:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.transcriber = Transcriber(config)
        self._lock = threading.Lock()
        self._last_error = ""
        self._last_text = ""
        self._loaded = False
        self._loading = False

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loading = True
            try:
                self.transcriber.load()
                self._loaded = True
                self._last_error = ""
            except Exception as exc:
                self._last_error = str(exc)
                raise
            finally:
                self._loading = False

    def transcribe(self, wav_path: Path) -> TranscribeResult:
        # ctranslate2/WhisperModel 的并发安全边界不在这里假设，服务内串行化识别。
        with self._lock:
            if not self._loaded:
                self._loading = True
                try:
                    self.transcriber.load()
                    self._loaded = True
                    self._last_error = ""
                except Exception as exc:
                    self._last_error = str(exc)
                    raise
                finally:
                    self._loading = False
            result = self.transcriber.transcribe(wav_path)
            self._last_text = result.text
            self._last_error = "" if result.status == "ok" else result.raw
            return result

    def status(self) -> dict[str, Any]:
        state = "ready" if self._loaded else "loading" if self._loading else "not_loaded"
        return {
            "ok": True,
            "version": __version__,
            "model": self.config.model,
            "device": self.transcriber.device_info,
            "state": state,
            "realtime": {
                "enabled": self.config.realtime_enabled,
                "url": (
                    f"ws://{self.config.realtime_host}:"
                    f"{self.config.realtime_port}/realtime"
                ),
                "sample_rate": 16000,
                "sample_width": 2,
                "channels": 1,
            },
            "last_text": self._last_text,
            "last_error": self._last_error,
        }


class ASRHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int],
                 handler_class: type[BaseHTTPRequestHandler],
                 runtime: ASRRuntime) -> None:
        super().__init__(server_address, handler_class)
        self.runtime = runtime


class ASRHandler(BaseHTTPRequestHandler):
    server: ASRHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[voice-asr] {self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route == "/health":
            self._send_json({"ok": True, "version": __version__})
            return
        if route == "/status":
            self._send_json(self.server.runtime.status())
            return
        self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route != "/transcribe":
            self._send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            wav_path, cleanup = self._read_audio_request()
            try:
                result = self.server.runtime.transcribe(wav_path)
            finally:
                if cleanup:
                    wav_path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            print("[voice-asr] /transcribe failed:", file=sys.stderr, flush=True)
            traceback.print_exc()
            self._send_json({"ok": False, "error": str(exc)},
                            HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json({
            "ok": True,
            "text": result.text,
            "raw": result.raw,
            "status": result.status,
        })

    def _read_audio_request(self) -> tuple[Path, bool]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            raise ValueError("请求体为空")
        body = self.rfile.read(content_length)
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip()

        if ctype == "application/json":
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON 请求体必须是 object")
            if payload.get("path"):
                path = Path(str(payload["path"])).expanduser()
                if not path.is_file():
                    raise FileNotFoundError(f"音频文件不存在: {path}")
                return path, False
            if payload.get("audio_base64"):
                raw = base64.b64decode(str(payload["audio_base64"]))
                return _write_temp_audio(raw), True
            raise ValueError("JSON 请求需包含 path 或 audio_base64")

        return _write_temp_audio(body), True

    def _send_json(self, obj: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def _write_temp_audio(raw: bytes) -> Path:
    if not raw:
        raise ValueError("音频内容为空")
    fd, name = tempfile.mkstemp(prefix="voice-asr-", suffix=".wav")
    path = Path(name)
    with open(fd, "wb") as fh:
        fh.write(raw)
    return path


def serve(
    host: str,
    port: int,
    preload: bool = True,
    realtime_enabled: bool | None = None,
    realtime_host: str | None = None,
    realtime_port: int | None = None,
) -> int:
    config = load_config()
    runtime = ASRRuntime(config)
    server = ASRHTTPServer((host, port), ASRHandler, runtime)
    realtime_server: RealtimeWebSocketServer | None = None

    if realtime_enabled is None:
        realtime_enabled = config.realtime_enabled
    if realtime_host is None:
        realtime_host = config.realtime_host
    if realtime_port is None:
        realtime_port = config.realtime_port

    if realtime_enabled:
        realtime_server = RealtimeWebSocketServer(
            realtime_host,
            realtime_port,
            runtime,
            config,
        )
        realtime_server.start()

    if preload:
        def _preload() -> None:
            try:
                print("[voice-asr] 正在预加载语音识别模型...", flush=True)
                runtime.load()
                print(
                    f"[voice-asr] 模型已就绪 ({runtime.transcriber.device_info})",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[voice-asr] 模型预加载失败: {exc}",
                      file=sys.stderr, flush=True)

        threading.Thread(target=_preload, daemon=True).start()

    def _stop(*_args: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _stop)

    print(f"[voice-asr] 服务已启动: http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        if realtime_server is not None:
            realtime_server.stop()
        server.server_close()
        print("[voice-asr] 服务已退出", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-asr",
        description="本机 faster-whisper ASR 服务。",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址，默认只监听本机 127.0.0.1")
    parser.add_argument("--port", default=8765, type=int,
                        help="监听端口，默认 8765")
    parser.add_argument("--no-preload", action="store_true",
                        help="启动时不预加载模型，首次识别时再加载")
    parser.add_argument("--no-realtime", action="store_true",
                        help="不启动实时语音 WebSocket 服务")
    parser.add_argument("--realtime-host", default=None,
                        help="实时语音 WebSocket 监听地址，默认读取配置")
    parser.add_argument("--realtime-port", default=None, type=int,
                        help="实时语音 WebSocket 监听端口，默认读取配置")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return serve(
            args.host,
            args.port,
            preload=not args.no_preload,
            realtime_enabled=not args.no_realtime,
            realtime_host=args.realtime_host,
            realtime_port=args.realtime_port,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[voice-asr] 启动失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
