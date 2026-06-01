"""语音识别：faster-whisper 封装。

模型懒加载并复用，由 daemon 常驻于内存（可选 GPU）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from voice_paste.config import Config


@dataclass
class TranscribeResult:
    """识别结果。

    status:
      - "ok"            正常识别到文本
      - "empty"         没有有效语音（真·空音频 / VAD 切空 / 太短）
      - "hallucination" 识别出空音频幻觉短语，已忽略
    """

    text: str
    raw: str
    status: str


_IGNORED_CHARS = str.maketrans(
    "",
    "",
    " \t\r\n，。？！、,.!?;；:：\"'“”‘’（）()[]【】《》<>-—_…",
)

_COMMON_EMPTY_AUDIO_HALLUCINATIONS = {
    "点点关注",
    "点赞关注",
    "点赞加关注",
    "谢谢观看",
    "感谢观看",
    "欢迎收看",
}

_PROMPT_LEAK_MARKERS = (
    "以下是普通话的句子",
    "请根据语气正确使用",
    "逗号句号问号感叹号等标点符号",
)

_HALLUCINATION_SUBSTRINGS = (
    "请不吝点赞",
    "点赞订阅转发打赏",
    "打赏支持明镜",
    "明镜与点点栏目",
    "明镜与点点",
)


def _normalize_text(text: str) -> str:
    return text.translate(_IGNORED_CHARS)


def _looks_like_hallucination(text: str, extra_substrings: tuple[str, ...] = ()) -> bool:
    normalized = _normalize_text(text)
    if normalized in _COMMON_EMPTY_AUDIO_HALLUCINATIONS:
        return True
    substrings = _HALLUCINATION_SUBSTRINGS + tuple(
        _normalize_text(s) for s in extra_substrings if s.strip()
    )
    if any(sub and sub in normalized for sub in substrings):
        return True
    return (
        len(normalized) <= 60
        and any(marker in normalized for marker in _PROMPT_LEAK_MARKERS)
    )


class Transcriber:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._model = None  # 懒加载

    def _resolve_device(self) -> tuple[str, str]:
        device = self.config.device
        compute = self.config.compute_type
        if device == "auto":
            try:
                import ctranslate2  # type: ignore

                if ctranslate2.get_cuda_device_count() > 0:
                    device = "cuda"
                else:
                    device = "cpu"
            except Exception:
                device = "cpu"
        if compute == "default":
            compute = "float16" if device == "cuda" else "int8"
        return device, compute

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        device, compute = self._resolve_device()
        kwargs = {"device": device, "compute_type": compute}
        if self.config.download_root:
            kwargs["download_root"] = self.config.download_root
        self._model = WhisperModel(self.config.model, **kwargs)
        self._device = device
        self._compute = compute

    @property
    def device_info(self) -> str:
        device, compute = self._resolve_device()
        return f"{device}/{compute}"

    def transcribe(self, wav_path: Path) -> TranscribeResult:
        self.load()
        assert self._model is not None
        language = self.config.language or None
        transcribe_kwargs = {
            "language": language,
            "beam_size": self.config.beam_size,
            "initial_prompt": self.config.initial_prompt or None,
            "vad_filter": self.config.vad_filter,
        }
        if self.config.vad_filter:
            transcribe_kwargs["vad_parameters"] = {
                "min_silence_duration_ms": self.config.vad_min_silence_ms,
                "speech_pad_ms": self.config.vad_speech_pad_ms,
            }
        segments, _info = self._model.transcribe(str(wav_path), **transcribe_kwargs)
        raw = "".join(seg.text for seg in segments).strip()
        if not raw:
            return TranscribeResult(text="", raw=raw, status="empty")
        extra = tuple(self.config.hallucination_substrings or ())
        if _looks_like_hallucination(raw, extra):
            return TranscribeResult(text="", raw=raw, status="hallucination")
        return TranscribeResult(text=raw, raw=raw, status="ok")
