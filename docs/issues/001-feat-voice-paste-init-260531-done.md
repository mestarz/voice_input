# 001 - feat - Voice Paste 初始实现

- 状态：done
- 日期：2026-05-31

## 背景

实现本地语音输入助手 Voice Paste：按快捷键开始录音，再按一次停止，本地语音
识别后自动粘贴到当前光标所在的输入框。不做输入法，不上云。

目标平台：Arch Linux + KDE Plasma 6 Wayland + NVIDIA GPU。

## 实现逻辑

- 架构：后台 daemon 常驻（复用模型）+ CLI `toggle` 命令（绑定快捷键），经 Unix
  socket + JSON 行协议通信。
- 模块：
  - `config.py`：TOML 配置加载（`~/.config/voice-paste/config.toml`）。
  - `ipc.py`：Unix socket 协议与路径。
  - `recorder.py`：`parecord` 录音（`--latency-msec=50` 修复短录音不 flush）。
  - `transcriber.py`：`faster-whisper`(large-v3) 封装，GPU/CPU 自动检测。
  - `paste.py`：剪贴板写入 + 模拟粘贴 + 方法探测/回退。
  - `notify.py`：`notify-send` 桌面通知。
  - `daemon.py`：状态机 idle→recording→busy→idle + socket server。
  - `cli.py`：子命令 daemon/toggle/status/doctor/config/stop。

## 关键问题与修复

- 剪贴板工具（wl-copy/xclip/xsel）fork 持有选区导致 `subprocess.run` 超时
  → 改为分离 spawn、写 stdin 后立即返回。
- KDE/KWin 不支持 `wtype` 的 virtual-keyboard 协议 → 回退用 `ydotool`(/dev/uinput)。
- 系统 CUDA 13 但 ctranslate2 需 CUDA 12 → 通过 `nvidia-cublas-cu12`/
  `nvidia-cudnn-cu12` pip 包 + `LD_LIBRARY_PATH` 解决。
- HF 模型下载需代理/镜像；systemd 服务需显式 `Environment=` 注入。
- 终端/vim 不响应 `Ctrl+V` 粘贴 → 模拟键改为可配置 `paste_key`，默认
  `shift+insert`（终端与多数 GUI 通用），并同时写入 clipboard 与 primary 选区。

## 验收

对照设计文档第 9 节 10 条全部满足，真实环境端到端可用（含终端）。
