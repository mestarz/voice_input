"""桌面通知（notify-send）。"""

from __future__ import annotations

import shutil
import subprocess

APP_NAME = "Voice Paste"
_HAS_NOTIFY = shutil.which("notify-send") is not None


def notify(message: str, *, urgency: str = "normal", enabled: bool = True) -> None:
    """发送桌面通知。失败时静默退回到 stdout。"""
    line = f"[voice-paste] {message}"
    print(line, flush=True)
    if not enabled or not _HAS_NOTIFY:
        return
    try:
        subprocess.run(
            [
                "notify-send",
                "--app-name", APP_NAME,
                "--urgency", urgency,
                "--expire-time", "2500",
                APP_NAME,
                message,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass
