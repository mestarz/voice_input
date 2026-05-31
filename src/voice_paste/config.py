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
    download_root: str | None = None  # 模型缓存目录，None 用 HF 默认

    # 录音
    sample_rate: int = 16000
    channels: int = 1
    max_seconds: int = 300  # 录音安全上限，超时自动停止

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

# ---- 录音 ----
sample_rate = 16000
channels = 1
max_seconds = 300

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
