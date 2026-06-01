"""语音识别：faster-whisper 封装。

模型懒加载并复用，由 daemon 常驻于内存（可选 GPU）。
"""

from __future__ import annotations

from pathlib import Path

from voice_paste.config import Config


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
    "与点点栏目",
    "请订阅",
    "字幕由",
)


def _normalize_text(text: str) -> str:
    return text.translate(_IGNORED_CHARS)


def _looks_like_hallucination(text: str) -> bool:
    normalized = _normalize_text(text)
    if normalized in _COMMON_EMPTY_AUDIO_HALLUCINATIONS:
        return True
    if any(sub in normalized for sub in _HALLUCINATION_SUBSTRINGS):
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

    def transcribe(self, wav_path: Path) -> str:
        self.load()
        assert self._model is not None
        language = self.config.language or None
        segments, _info = self._model.transcribe(
            str(wav_path),
            language=language,
            beam_size=self.config.beam_size,
            initial_prompt=self.config.initial_prompt or None,
            vad_filter=True,
        )
        text = "".join(seg.text for seg in segments)
        text = text.strip()
        if _looks_like_hallucination(text):
            return ""
        return text
