# Voice Paste — 本地语音输入助手

按一下快捷键开始录音，再按一下停止，程序在**本地**把语音转成中文文本并**自动粘贴**到当前光标所在的输入框。

不是输入法，不接 Fcitx/IBus，不上云。只做一件事：

```
录音 → 本地识别 → 粘贴到当前输入框
```

目标平台：Arch Linux + Wayland + NVIDIA GPU（也兼容 CPU / X11）。

## 工作原理

```
全局快捷键  ──>  voice-paste toggle  ──(unix socket)──>  voice-paste daemon
                                                          ├─ 录音 (parecord)
                                                          ├─ 识别 (faster-whisper, GPU)
                                                          └─ 粘贴 (wl-copy + ydotool/wtype)
```

后台服务常驻内存、复用已加载的识别模型；快捷键命令只发送一个 toggle 请求。

## 安装

### 1. 系统依赖

| 用途 | 工具 | Arch 安装命令 |
| --- | --- | --- |
| 录音 | `parecord` | `sudo pacman -S libpulse`（PipeWire 用户通常已装 `pipewire-pulse`） |
| 剪贴板 | `wl-copy` | `sudo pacman -S wl-clipboard` |
| 自动粘贴 (wlroots) | `wtype` | `sudo pacman -S wtype` |
| 自动粘贴 (KDE/GNOME) | `ydotool` | `sudo pacman -S ydotool` |
| 桌面通知 | `notify-send` | `sudo pacman -S libnotify` |

> **按桌面环境选择粘贴工具：**
> - **Sway / Hyprland 等 wlroots 合成器** → `wtype`（开箱即用）。
> - **KDE Plasma / GNOME** → 它们的合成器（KWin/Mutter）**不支持** `wtype` 依赖的
>   virtual-keyboard 协议，必须用 **`ydotool`**（走内核 `/dev/uinput`）。
>
> **ydotool 配置**（KDE/GNOME 必读）：
> ```bash
> sudo pacman -S ydotool
> # 让普通用户可访问 /dev/uinput：
> echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
>   | sudo tee /etc/udev/rules.d/80-uinput.rules
> sudo udevadm control --reload-rules && sudo udevadm trigger
> sudo usermod -aG input "$USER"   # 加入 input 组后需重新登录
> # 启动守护进程（用户级 systemd）：
> systemctl --user enable --now ydotool.service   # 若包未带 unit，可手动 `ydotoold &`
> ```
> 程序在 `auto` 模式下会自动探测：优先 `wtype`，失败则回退到 `ydotool`，
> 仍失败则保留剪贴板供手动粘贴。也可在配置里固定 `paste_method = "ydotool"`。
>
> X11 环境可用 `xclip`/`xsel` 作为剪贴板工具。

> **粘贴快捷键（`paste_key`）**：识别完成后程序写入剪贴板（同时写 `clipboard`
> 与 `primary` 选区），再模拟一次粘贴快捷键。默认 **`shift+insert`**——这是
> Linux 通用粘贴键，**终端（Konsole 等）和大多数 GUI 都认**，比 `ctrl+v` 兼容
> 面更广（`ctrl+v` 在终端是字面输入、在 vim 普通模式是块选择）。若个别应用不认，
> 可在配置里改成 `ctrl+v` 或 `ctrl+shift+v`。

### 2. Python 包

```bash
cd voice_input
uv sync           # 或：pip install -e .
```

> **NVIDIA GPU 加速**：`faster-whisper`(ctranslate2) 需要 CUDA 12 运行库
> （`libcublas.so.12`、`libcudnn.so.9`）。若系统 CUDA 版本不匹配（如仅有 CUDA 13），
> 安装可选组 `cuda12` 把运行库装进虚拟环境：
> ```bash
> # 全局安装为命令时：
> uv tool install . --with nvidia-cublas-cu12 --with "nvidia-cudnn-cu12>=9,<10"
> # 或开发模式：
> uv sync --extra cuda12
> ```
> 并确保运行进程能找到这些库（systemd 服务里用 `Environment=LD_LIBRARY_PATH=...`
> 指向 venv 内 `nvidia/cublas/lib` 与 `nvidia/cudnn/lib`，见下文 systemd 小节）。

首次运行 daemon 时会自动下载 `large-v3` 模型（约 3GB）。

> **网络受限（如国内）**：模型从 `huggingface.co` 下载。若无法直连，二选一：
> - 走代理：`export https_proxy=http://127.0.0.1:7890 http_proxy=http://127.0.0.1:7890`
> - 用镜像：`export HF_ENDPOINT=https://hf-mirror.com`
>
> 作为 systemd 服务运行时，这些变量不会自动继承，需在 unit 的 `[Service]` 段用
> `Environment=` 显式设置（见 `voice-paste.service` 中的注释示例）。
> 模型下载一次后会缓存到 `~/.cache/huggingface`，之后启动无需联网。

### 3. 自检

```bash
uv run voice-paste doctor
```

## 使用

### 1. 启动后台服务

```bash
uv run voice-paste daemon
```

或安装为 systemd 用户服务（开机自启）：

```bash
mkdir -p ~/.config/systemd/user
cp src/voice_paste/systemd/voice-paste.service ~/.config/systemd/user/
systemctl --user enable --now voice-paste.service
```

### 2. 绑定全局快捷键

在桌面环境（GNOME / KDE / Hyprland / Sway 等）把一个快捷键（如 `Super+Space`）绑定到：

```bash
voice-paste toggle
```

Hyprland 示例（`~/.config/hypr/hyprland.conf`）：

```
bind = SUPER, SPACE, exec, voice-paste toggle
```

### 3. 日常使用

1. 点进任意输入框
2. 按快捷键 → 开始录音（出现“开始录音 🎙”通知）
3. 说话
4. 再按快捷键 → 停止、识别、自动粘贴

## 命令

| 命令 | 说明 |
| --- | --- |
| `voice-paste daemon` | 启动后台服务（`--no-preload` 跳过启动时预加载模型） |
| `voice-paste toggle` | 开始/停止录音（绑定到快捷键） |
| `voice-paste status` | 查看服务与录音状态 |
| `voice-paste doctor` | 环境自检 |
| `voice-paste config [--init]` | 查看配置 /（`--init`）生成默认配置文件 |
| `voice-paste stop` | 停止后台服务 |

## 配置

配置文件：`~/.config/voice-paste/config.toml`（可选，所有项有默认值）。
生成模板：`voice-paste config --init`。

```toml
model = "large-v3"      # tiny/base/small/medium/large-v3
device = "auto"          # auto / cuda / cpu
compute_type = "default" # default / float16 / int8_float16 / int8
language = "zh"          # 留空字符串表示自动检测；短句听写建议固定 zh
beam_size = 5
hotwords = ""            # 热词提示，如 "GitHub OpenAI systemd"
initial_prompt = ""      # 默认关闭；它是转写上下文，不是命令提示词

sample_rate = 16000
channels = 1
max_seconds = 300        # 录音安全上限，超时自动停止识别

paste_method = "auto"    # auto / wtype / ydotool / clipboard
paste_key = "shift+insert"  # shift+insert / ctrl+v / ctrl+shift+v
auto_paste = true
paste_delay_ms = 120

notifications = true
```

## 行为与可靠性

- **自动粘贴失败**（无 wtype/ydotool 或模拟失败）时，文本仍保留在剪贴板，可手动 `Ctrl+V` / `Shift+Insert`。
- **粘贴键可配置**：默认 `shift+insert`（终端与多数 GUI 通用）；写剪贴板时同时写入 `clipboard` 与 `primary` 选区以最大化兼容性。
- **没识别到文本** / **麦克风不可用** / **服务未启动** 等都会给出桌面通知与非零退出码。
- 录音超过 `max_seconds` 会自动停止并识别，避免忘记停止。
- 全程本地完成，不依赖任何云服务。

## 非目标（第一版）

不做输入法、不接 Fcitx/IBus、不做 GUI/托盘、不做实时逐字转写、不做语音唤醒、不做云端识别、不做自动发送、不做文本润色。
