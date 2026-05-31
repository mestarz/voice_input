"""粘贴：写入剪贴板并模拟粘贴。

策略：
1. 用 wl-copy（Wayland）/ xclip / xsel 写入剪贴板。
2. 模拟 Ctrl+V：wtype 或 ydotool。
3. 任一步失败时，至少保证文本已在剪贴板，返回部分成功。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class PasteResult:
    clipboard_ok: bool
    pasted: bool
    method: str
    detail: str = ""


def _run(cmd: list[str], text: str | None = None, timeout: float = 5.0,
         env: dict | None = None) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            input=text.encode("utf-8") if text is not None else None,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr or b"").decode(errors="replace").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def copy_to_clipboard(text: str) -> tuple[bool, str]:
    # wl-copy 与 xclip/xsel 都会 fork/保持一个后台进程持有剪贴板选区。
    # 若用 subprocess.run 等待并捕获其 stderr，后台进程会继承管道写端、
    # 永不关闭，导致读取阻塞直到超时。因此统一用分离 spawn、丢弃输出、
    # 写入 stdin 后立即返回。
    #
    # 同时写入 clipboard 和 primary 选区：Shift+Insert 在不同应用里有的粘贴
    # clipboard、有的粘贴 primary，两者都写可最大化兼容性。primary 写入失败
    # 不影响主结果。
    if shutil.which("wl-copy"):
        ok, err = _spawn_detached_with_input(["wl-copy"], text)
        _spawn_detached_with_input(["wl-copy", "--primary"], text)
        return ok, err
    if shutil.which("xclip"):
        ok, err = _spawn_detached_with_input(
            ["xclip", "-selection", "clipboard"], text)
        _spawn_detached_with_input(["xclip", "-selection", "primary"], text)
        return ok, err
    if shutil.which("xsel"):
        ok, err = _spawn_detached_with_input(
            ["xsel", "--clipboard", "--input"], text)
        _spawn_detached_with_input(["xsel", "--primary", "--input"], text)
        return ok, err
    return False, "未找到剪贴板工具（wl-copy / xclip / xsel）"


def _spawn_detached_with_input(cmd: list[str], text: str) -> tuple[bool, str]:
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert proc.stdin is not None
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
        # 进程会持续存活以持有选区，这是剪贴板工具的预期行为
        return True, ""
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def _ydotool_env() -> dict:
    """ydotool 需要 YDOTOOL_SOCKET 指向 ydotoold 的 socket。

    默认探测常见位置；用户也可在环境里预设 YDOTOOL_SOCKET。
    """
    import os
    env = dict(os.environ)
    if env.get("YDOTOOL_SOCKET"):
        return env
    runtime = env.get("XDG_RUNTIME_DIR", "/run/user/1000")
    for cand in (f"{runtime}/.ydotool_socket", "/tmp/.ydotool_socket"):
        if os.path.exists(cand):
            env["YDOTOOL_SOCKET"] = cand
            break
    return env


def available_methods() -> list[str]:
    """按优先级返回可用的按键模拟方法。

    KDE/KWin 不支持 wtype 的 virtual-keyboard 协议，因此 wtype 可能存在却失败；
    auto 模式会依次尝试，失败自动回退到下一个。
    """
    methods: list[str] = []
    if shutil.which("wtype"):
        methods.append("wtype")
    if shutil.which("ydotool"):
        methods.append("ydotool")
    return methods


def detect_paste_method() -> str:
    methods = available_methods()
    return methods[0] if methods else "clipboard"


def probe_method(method: str) -> tuple[bool, str]:
    """无副作用地探测某方法是否可用（仅按下并松开 Ctrl，不产生输入）。"""
    if method == "wtype":
        return _run(["wtype", "-M", "ctrl", "-m", "ctrl"])
    if method == "ydotool":
        return _run(["ydotool", "key", "29:1", "29:0"], env=_ydotool_env())
    return False, "未知方法"


# 各粘贴快捷键对应的 ydotool 键码序列与 wtype 参数。
# ydotool 键码（Linux input-event-codes）：LEFTCTRL=29 LEFTSHIFT=42 V=47 INSERT=110
# shift+insert 是 Linux 通用粘贴键：Konsole 等终端与多数 GUI 默认都绑定为粘贴，
# 比 ctrl+v 兼容面更广（ctrl+v 在终端是字面输入、在 vim 普通模式是块选择）。
_PASTE_KEYS: dict[str, dict[str, list[str]]] = {
    "shift+insert": {
        "ydotool": ["42:1", "110:1", "110:0", "42:0"],
        "wtype": ["-M", "shift", "-k", "Insert", "-m", "shift"],
    },
    "ctrl+v": {
        "ydotool": ["29:1", "47:1", "47:0", "29:0"],
        "wtype": ["-M", "ctrl", "v", "-m", "ctrl"],
    },
    "ctrl+shift+v": {
        "ydotool": ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
        "wtype": ["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"],
    },
}

DEFAULT_PASTE_KEY = "shift+insert"


def simulate_paste(method: str, paste_key: str = DEFAULT_PASTE_KEY) -> tuple[bool, str]:
    keyseq = _PASTE_KEYS.get(paste_key) or _PASTE_KEYS[DEFAULT_PASTE_KEY]
    if method == "wtype":
        return _run(["wtype", *keyseq["wtype"]])
    if method == "ydotool":
        return _run(["ydotool", "key", *keyseq["ydotool"]],
                    env=_ydotool_env())
    return False, "无可用的粘贴模拟工具"


def paste_text(text: str, *, method: str = "auto", auto_paste: bool = True,
               delay_ms: int = 120,
               paste_key: str = DEFAULT_PASTE_KEY) -> PasteResult:
    clip_ok, clip_err = copy_to_clipboard(text)

    if not auto_paste:
        return PasteResult(clip_ok, False, "clipboard",
                           clip_err if not clip_ok else "auto_paste 关闭，已复制到剪贴板")

    # 确定要尝试的方法链：auto 模式按优先级全部尝试，显式指定则只试该方法。
    if method == "auto":
        chain = available_methods()
    elif method == "clipboard":
        chain = []
    else:
        chain = [method]

    if not chain:
        return PasteResult(clip_ok, False, "clipboard",
                           "无可用粘贴工具，已复制到剪贴板" if clip_ok else clip_err)

    if not clip_ok:
        return PasteResult(False, False, chain[0], f"剪贴板写入失败: {clip_err}")

    if delay_ms > 0:
        import time
        time.sleep(delay_ms / 1000.0)

    errors: list[str] = []
    for m in chain:
        pasted, perr = simulate_paste(m, paste_key)
        if pasted:
            return PasteResult(True, True, m)
        errors.append(f"{m}: {perr}")

    return PasteResult(True, False, chain[-1],
                       f"模拟粘贴失败（{'; '.join(errors)}），文本已在剪贴板，可手动粘贴")
