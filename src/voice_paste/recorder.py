"""录音：使用 parecord（PipeWire/PulseAudio）录制到临时 WAV 文件。

parecord 在 Arch + PipeWire 环境默认可用（pipewire-pulse 提供）。
录音以子进程方式运行，停止时发送 SIGINT 让其正常收尾写入文件尾。
"""

from __future__ import annotations

import signal
import shutil
import subprocess
import tempfile
from pathlib import Path


class RecorderError(RuntimeError):
    pass


class Recorder:
    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._proc: subprocess.Popen | None = None
        self._wav_path: Path | None = None

    @staticmethod
    def backend() -> str | None:
        for tool in ("parecord", "arecord"):
            if shutil.which(tool):
                return tool
        return None

    @property
    def is_recording(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> Path:
        if self.is_recording:
            raise RecorderError("已经在录音中")
        backend = self.backend()
        if backend is None:
            raise RecorderError("未找到录音工具（parecord 或 arecord）")

        fd, name = tempfile.mkstemp(prefix="voice-paste-", suffix=".wav")
        Path(name).unlink(missing_ok=True)  # 让录音工具自己创建文件
        import os
        os.close(fd)
        self._wav_path = Path(name)

        if backend == "parecord":
            cmd = [
                "parecord",
                "--file-format=wav",
                f"--rate={self.sample_rate}",
                f"--channels={self.channels}",
                "--format=s16le",
                # 低延迟缓冲，确保短录音也能及时写入并在停止时完整 flush
                "--latency-msec=50",
                str(self._wav_path),
            ]
        else:  # arecord
            cmd = [
                "arecord",
                "-q",
                "-f", "S16_LE",
                "-r", str(self.sample_rate),
                "-c", str(self.channels),
                "-t", "wav",
                str(self._wav_path),
            ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise RecorderError(f"启动录音失败: {exc}") from exc

        # 立即检查是否秒退（如设备不可用）
        try:
            self._proc.wait(timeout=0.3)
            err = (self._proc.stderr.read() or b"").decode(errors="replace") if self._proc.stderr else ""
            self._proc = None
            raise RecorderError(f"录音进程启动后立即退出：{err.strip() or '麦克风不可用'}")
        except subprocess.TimeoutExpired:
            pass  # 正常：仍在运行

        return self._wav_path

    def stop(self) -> Path:
        """停止录音，返回写好的 WAV 路径。"""
        if self._proc is None or self._wav_path is None:
            raise RecorderError("当前没有录音")
        proc = self._proc
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        path = self._wav_path
        self._proc = None
        self._wav_path = None
        if not path.exists() or path.stat().st_size <= 44:  # 44 = wav 头
            raise RecorderError("录音文件为空，未捕获到音频")
        return path

    def abort(self) -> None:
        """异常情况下放弃录音并清理。"""
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        self._proc = None
        if self._wav_path is not None:
            self._wav_path.unlink(missing_ok=True)
            self._wav_path = None
