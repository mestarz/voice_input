"""配置加载。

配置文件路径： ~/.config/voice-paste/config.toml （遵循 XDG_CONFIG_HOME）。
所有配置项均有默认值，配置文件可选。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path


def config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "voice-paste"


def config_path() -> Path:
    return config_home() / "config.toml"


@dataclass
class Config:
    # 语音识别（faster-whisper）
    model: str = "large-v3"
    device: str = "auto"  # auto / cuda / cpu
    compute_type: str = "default"  # default / float16 / int8_float16 / int8
    language: str = "zh"  # 识别语言，None 表示自动检测
    beam_size: int = 5
    # 引导提示：给一段带标点的中文示例可显著提升标点/数字格式输出。
    # 留空字符串表示不使用。
    initial_prompt: str = "以下是普通话的句子，请根据语气正确使用逗号、句号、问号、感叹号等标点符号。"
    download_root: str | None = None  # 模型缓存目录，None 用 HF 默认

    # 录音
    sample_rate: int = 16000
    channels: int = 1
    max_seconds: int = 300  # 录音安全上限，超时自动停止

    # VAD（语音活动检测）：过滤静音段，避免空音频幻觉。
    # 默认值较默认 VAD 更宽松，减少短句/轻声被整段切掉导致的“识别失败”。
    vad_filter: bool = True
    vad_min_silence_ms: int = 300  # 判定为静音切分的最短时长（越大越不易切断）
    vad_speech_pad_ms: int = 200   # 语音段前后保留的填充，避免吃掉首尾字

    # 额外的幻觉短语（子串匹配，归一化后比较）。与内置默认列表合并；
    # 遇到新的空音频幻觉时在此追加即可，无需改代码。
    hallucination_substrings: list[str] = field(default_factory=list)

    # 粘贴
    paste_method: str = "auto"  # auto / wtype / ydotool / clipboard
    paste_key: str = "shift+insert"  # shift+insert / ctrl+v / ctrl+shift+v
    auto_paste: bool = True
    paste_delay_ms: int = 120  # 写剪贴板后到模拟粘贴的等待

    # 通知
    notifications: bool = True

    def merged(self, data: dict) -> "Config":
        valid = {f for f in self.__dataclass_fields__}  # type: ignore[attr-defined]
        updates = {k: v for k, v in data.items() if k in valid}
        return Config(**{**asdict(self), **updates})


def load_config() -> Config:
    cfg = Config()
    path = config_path()
    if path.is_file():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            cfg = cfg.merged(data)
        except (OSError, tomllib.TOMLDecodeError) as exc:  # pragma: no cover
            print(f"[voice-paste] 配置文件解析失败，使用默认配置: {exc}")
    return cfg


DEFAULT_CONFIG_TEMPLATE = """\
# Voice Paste 配置文件
# 所有项均可省略，省略时使用默认值。

# ---- 语音识别 ----
model = "large-v3"      # tiny/base/small/medium/large-v3 等
device = "auto"          # auto / cuda / cpu
compute_type = "default" # default / float16 / int8_float16 / int8
language = "zh"          # 识别语言；留空字符串表示自动检测
beam_size = 5
# 引导提示：带标点的中文示例可提升标点输出。留空字符串则不使用。
initial_prompt = "以下是普通话的句子，请根据语气正确使用逗号、句号、问号、感叹号等标点符号。"

# ---- 录音 ----
sample_rate = 16000
channels = 1
max_seconds = 300

# ---- VAD（语音活动检测）----
# 过滤静音以减少空音频幻觉；放宽参数可减少短句被误切导致的“识别失败”。
vad_filter = true
vad_min_silence_ms = 300   # 静音切分最短时长，越大越不易切断语音
vad_speech_pad_ms = 200    # 语音段前后填充，避免吃掉首尾字

# 额外幻觉短语（子串匹配）。与内置列表合并，遇到新幻觉在此追加即可。
# 例：hallucination_substrings = ["关注我的频道", "下期再见"]
hallucination_substrings = []

# ---- 粘贴 ----
paste_method = "auto"    # auto / wtype / ydotool / clipboard
# 模拟的粘贴快捷键。shift+insert 通用性最好（终端与多数 GUI 都支持）；
# 若某些应用不认，可改成 ctrl+v 或 ctrl+shift+v。
paste_key = "shift+insert"
auto_paste = true
paste_delay_ms = 120

# ---- 通知 ----
notifications = true
"""


def write_default_config() -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return path
