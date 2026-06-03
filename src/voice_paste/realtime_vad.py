"""实时 Silero VAD 切句。

输入格式固定为 PCM signed 16-bit little-endian, 16 kHz, mono。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE = 16000
SAMPLE_WIDTH_BYTES = 2
FRAME_SAMPLES = 512
CONTEXT_SAMPLES = 64


@dataclass
class RealtimeVADEvent:
    type: str
    audio: bytes = b""
    audio_ms: int = 0
    speech_prob: float = 0.0


class StreamingSileroVAD:
    def __init__(
        self,
        *,
        threshold: float = 0.5,
        min_speech_ms: int = 200,
        end_silence_ms: int = 700,
        speech_pad_ms: int = 200,
        max_utterance_seconds: float = 30.0,
    ) -> None:
        import onnxruntime
        from faster_whisper.vad import get_assets_path

        path = Path(get_assets_path()) / "silero_vad_v6.onnx"
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            str(path),
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )

        self.threshold = threshold
        self.neg_threshold = max(threshold - 0.15, 0.01)
        self.min_speech_samples = int(SAMPLE_RATE * min_speech_ms / 1000)
        self.end_silence_samples = int(SAMPLE_RATE * end_silence_ms / 1000)
        self.speech_pad_samples = int(SAMPLE_RATE * speech_pad_ms / 1000)
        self.max_utterance_samples = int(SAMPLE_RATE * max_utterance_seconds)

        self._h = np.zeros((1, 1, 128), dtype="float32")
        self._c = np.zeros((1, 1, 128), dtype="float32")
        self._context = np.zeros((1, CONTEXT_SAMPLES), dtype="float32")
        self._pending = bytearray()
        self._prepad: deque[bytes] = deque()
        self._prepad_samples = 0
        self._candidate: list[bytes] = []
        self._candidate_samples = 0
        self._current = bytearray()
        self._current_samples = 0
        self._silence_samples = 0
        self._triggered = False

    @property
    def is_speaking(self) -> bool:
        return self._triggered

    def process_pcm(self, pcm: bytes) -> list[RealtimeVADEvent]:
        self._pending.extend(pcm)
        events: list[RealtimeVADEvent] = []
        frame_bytes = FRAME_SAMPLES * SAMPLE_WIDTH_BYTES
        while len(self._pending) >= frame_bytes:
            frame = bytes(self._pending[:frame_bytes])
            del self._pending[:frame_bytes]
            events.extend(self._process_frame(frame))
        return events

    def flush(self) -> list[RealtimeVADEvent]:
        if not self._triggered and not self._candidate:
            self._pending.clear()
            return []
        audio = bytes(self._current or b"".join(self._candidate))
        self._reset_utterance()
        self._pending.clear()
        if not audio:
            return []
        return [self._final_event(audio)]

    def _process_frame(self, frame: bytes) -> list[RealtimeVADEvent]:
        prob = self._speech_probability(frame)
        events: list[RealtimeVADEvent] = []

        if not self._triggered:
            if prob >= self.threshold:
                self._candidate.append(frame)
                self._candidate_samples += FRAME_SAMPLES
                if self._candidate_samples >= self.min_speech_samples:
                    self._triggered = True
                    self._current = bytearray(b"".join(self._prepad))
                    for candidate_frame in self._candidate:
                        self._current.extend(candidate_frame)
                    self._current_samples = (
                        self._prepad_samples + self._candidate_samples
                    )
                    self._candidate.clear()
                    self._candidate_samples = 0
                    self._silence_samples = 0
                    events.append(RealtimeVADEvent("speech_start", speech_prob=prob))
            else:
                self._candidate.clear()
                self._candidate_samples = 0
                self._push_prepad(frame)
            return events

        self._current.extend(frame)
        self._current_samples += FRAME_SAMPLES

        if prob < self.neg_threshold:
            self._silence_samples += FRAME_SAMPLES
        else:
            self._silence_samples = 0

        if (
            self._silence_samples >= self.end_silence_samples
            or self._current_samples >= self.max_utterance_samples
        ):
            audio = bytes(self._current)
            self._reset_utterance()
            events.append(RealtimeVADEvent("speech_end", audio_ms=_audio_ms(audio)))
            events.append(self._final_event(audio))

        return events

    def _speech_probability(self, frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype=np.int16).astype("float32") / 32768.0
        batched = np.concatenate([self._context.reshape(-1), samples]).reshape(
            1, FRAME_SAMPLES + CONTEXT_SAMPLES
        )
        output, self._h, self._c = self._session.run(
            None,
            {"input": batched, "h": self._h, "c": self._c},
        )
        self._context = samples[-CONTEXT_SAMPLES:].reshape(1, CONTEXT_SAMPLES)
        return float(np.asarray(output).reshape(-1)[0])

    def _push_prepad(self, frame: bytes) -> None:
        self._prepad.append(frame)
        self._prepad_samples += FRAME_SAMPLES
        while self._prepad_samples > self.speech_pad_samples and self._prepad:
            self._prepad.popleft()
            self._prepad_samples -= FRAME_SAMPLES

    def _reset_utterance(self) -> None:
        self._prepad.clear()
        self._prepad_samples = 0
        self._candidate.clear()
        self._candidate_samples = 0
        self._current = bytearray()
        self._current_samples = 0
        self._silence_samples = 0
        self._triggered = False

    def _final_event(self, audio: bytes) -> RealtimeVADEvent:
        return RealtimeVADEvent("utterance", audio=audio, audio_ms=_audio_ms(audio))


def _audio_ms(audio: bytes) -> int:
    samples = len(audio) // SAMPLE_WIDTH_BYTES
    return round(samples * 1000 / SAMPLE_RATE)
