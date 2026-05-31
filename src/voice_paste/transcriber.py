"""语音识别：faster-whisper 封装。

模型懒加载并复用，由 daemon 常驻于内存（可选 GPU）。
"""

from __future__ import annotations

from pathlib import Path

from voice_paste.config import Config


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
        return text.strip()
