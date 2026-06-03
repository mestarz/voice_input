"""实时语音 WebSocket 服务。

协议：
- 客户端发送 binary frame：PCM s16le, 16 kHz, mono。
- 客户端可发送 text JSON：{"type": "flush"} 或 {"type": "end"}。
- 服务端发送 text JSON 事件：ready/speech_start/speech_end/transcript_final/error。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import traceback
import wave
from pathlib import Path
from typing import Any

from voice_paste import __version__
from voice_paste.config import Config
from voice_paste.realtime_vad import (
    SAMPLE_RATE,
    SAMPLE_WIDTH_BYTES,
    StreamingSileroVAD,
)


class RealtimeWebSocketServer:
    def __init__(self, host: str, port: int, runtime: Any, config: Config) -> None:
        self.host = host
        self.port = port
        self.runtime = runtime
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:  # noqa: BLE001
            print("[voice-asr] realtime server failed:", flush=True)
            traceback.print_exc()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        async with serve(
            self._handle,
            self.host,
            self.port,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
        ):
            print(
                f"[voice-asr] 实时语音服务已启动: ws://{self.host}:{self.port}/realtime",
                flush=True,
            )
            await asyncio.to_thread(self._stop.wait)
            print("[voice-asr] 实时语音服务已退出", flush=True)

    async def _handle(self, websocket: Any) -> None:
        path = _websocket_path(websocket)
        if path not in ("", "/", "/realtime"):
            await websocket.close(code=1008, reason="not found")
            return

        vad = StreamingSileroVAD(
            threshold=self.config.realtime_vad_threshold,
            min_speech_ms=self.config.vad_min_speech_ms,
            end_silence_ms=self.config.vad_min_silence_ms,
            speech_pad_ms=self.config.vad_speech_pad_ms,
            max_utterance_seconds=self.config.realtime_max_utterance_seconds,
        )
        await self._send(websocket, {
            "type": "ready",
            "version": __version__,
            "sample_rate": SAMPLE_RATE,
            "sample_width": SAMPLE_WIDTH_BYTES,
            "channels": 1,
        })

        async for message in websocket:
            if isinstance(message, bytes):
                await self._handle_pcm(websocket, vad, message)
            else:
                should_close = await self._handle_text(websocket, vad, str(message))
                if should_close:
                    return

    async def _handle_text(
        self,
        websocket: Any,
        vad: StreamingSileroVAD,
        message: str,
    ) -> bool:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            await self._send(websocket, {"type": "error", "error": "invalid json"})
            return False

        msg_type = payload.get("type")
        if msg_type == "flush":
            await self._flush(websocket, vad)
            return False
        if msg_type == "end":
            await self._flush(websocket, vad)
            await websocket.close()
            return True

        await self._send(websocket, {
            "type": "error",
            "error": f"unknown message type: {msg_type}",
        })
        return False

    async def _handle_pcm(
        self,
        websocket: Any,
        vad: StreamingSileroVAD,
        pcm: bytes,
    ) -> None:
        if len(pcm) % SAMPLE_WIDTH_BYTES:
            await self._send(websocket, {
                "type": "error",
                "error": "PCM chunk length must be aligned to 16-bit samples",
            })
            return

        for event in vad.process_pcm(pcm):
            if event.type == "speech_start":
                await self._send(websocket, {
                    "type": "speech_start",
                    "speech_prob": round(event.speech_prob, 4),
                })
            elif event.type == "speech_end":
                await self._send(websocket, {
                    "type": "speech_end",
                    "audio_ms": event.audio_ms,
                })
            elif event.type == "utterance":
                await self._transcribe_event(websocket, event.audio, event.audio_ms)

    async def _flush(self, websocket: Any, vad: StreamingSileroVAD) -> None:
        for event in vad.flush():
            if event.type == "utterance":
                await self._send(websocket, {
                    "type": "speech_end",
                    "audio_ms": event.audio_ms,
                    "reason": "flush",
                })
                await self._transcribe_event(websocket, event.audio, event.audio_ms)

    async def _transcribe_event(
        self,
        websocket: Any,
        pcm: bytes,
        audio_ms: int,
    ) -> None:
        wav_path = _write_wav(pcm)
        try:
            result = await asyncio.to_thread(self.runtime.transcribe, wav_path)
        except Exception as exc:  # noqa: BLE001
            await self._send(websocket, {
                "type": "error",
                "error": str(exc),
            })
            return
        finally:
            wav_path.unlink(missing_ok=True)

        await self._send(websocket, {
            "type": "transcript_final",
            "text": result.text,
            "raw": result.raw,
            "status": result.status,
            "audio_ms": audio_ms,
        })

    async def _send(self, websocket: Any, payload: dict) -> None:
        await websocket.send(json.dumps(payload, ensure_ascii=False))


def _write_wav(pcm: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix="voice-realtime-", suffix=".wav")
    path = Path(name)
    with wave.open(name, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(SAMPLE_WIDTH_BYTES)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(pcm)
    import os

    os.close(fd)
    return path


def _websocket_path(websocket: Any) -> str:
    request = getattr(websocket, "request", None)
    if request is not None and getattr(request, "path", None):
        return str(request.path).split("?", 1)[0]
    path = getattr(websocket, "path", "")
    return str(path).split("?", 1)[0]
