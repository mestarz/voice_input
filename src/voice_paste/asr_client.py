"""ASR 服务客户端。

当前用于本机 voice-paste daemon 调用常驻的 faster-whisper 服务。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from voice_paste.transcriber import TranscribeResult


class ASRServiceError(RuntimeError):
    pass


def _request_json(url: str, payload: dict | None = None,
                  timeout: float = 30.0) -> dict:
    data = None
    method = "GET"
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            raw_error = exc.read().decode("utf-8")
            obj = json.loads(raw_error)
            if isinstance(obj, dict) and obj.get("error"):
                detail = str(obj["error"])
        except Exception:
            pass
        raise ASRServiceError(f"HTTP {exc.code}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ASRServiceError(str(exc)) from exc

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ASRServiceError(f"服务返回了无效 JSON: {raw[:200]}") from exc
    if not isinstance(obj, dict):
        raise ASRServiceError("服务返回结构不是 JSON object")
    return obj


def health(base_url: str, timeout: float = 5.0) -> dict:
    return _request_json(base_url.rstrip("/") + "/health", timeout=timeout)


def status(base_url: str, timeout: float = 5.0) -> dict:
    return _request_json(base_url.rstrip("/") + "/status", timeout=timeout)


def transcribe_path(base_url: str, wav_path: Path,
                    timeout: float = 120.0) -> TranscribeResult:
    obj = _request_json(
        base_url.rstrip("/") + "/transcribe",
        {"path": str(wav_path)},
        timeout=timeout,
    )
    if not obj.get("ok"):
        raise ASRServiceError(str(obj.get("error", "ASR 服务识别失败")))
    return TranscribeResult(
        text=str(obj.get("text", "")),
        raw=str(obj.get("raw", "")),
        status=str(obj.get("status", "empty")),
    )
