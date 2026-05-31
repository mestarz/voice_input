"""IPC：基于 Unix domain socket 的简单 JSON 行协议。

客户端发送一行 JSON 请求，服务端回复一行 JSON。
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base)
    return Path("/tmp")


def socket_path() -> Path:
    return runtime_dir() / "voice-paste.sock"


def pid_path() -> Path:
    return runtime_dir() / "voice-paste.pid"


def send_request(cmd: str, timeout: float = 30.0, **params) -> dict:
    """连接 daemon 并发送一条请求，返回解析后的响应。

    若 daemon 未运行，抛出 ConnectionError。
    """
    path = str(socket_path())
    payload = json.dumps({"cmd": cmd, **params}).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(payload)
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise ConnectionError("daemon 未运行") from exc
    line = buf.split(b"\n", 1)[0]
    if not line:
        return {}
    return json.loads(line.decode("utf-8"))


def daemon_running() -> bool:
    try:
        resp = send_request("ping", timeout=2.0)
        return resp.get("ok", False)
    except (ConnectionError, OSError, json.JSONDecodeError):
        return False
