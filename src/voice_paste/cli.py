"""命令行入口。

子命令：
  daemon   启动后台服务
  toggle   开始/停止录音（绑定全局快捷键）
  status   查询服务状态
  doctor   环境自检
  config   打印/初始化配置
  stop     停止后台服务
"""

from __future__ import annotations

import argparse
import shutil
import sys

from voice_paste import __version__
from voice_paste import ipc
from voice_paste.config import config_path, load_config, write_default_config


def _cmd_daemon(args: argparse.Namespace) -> int:
    from voice_paste.daemon import run_daemon

    if ipc.daemon_running():
        print("daemon 已在运行")
        return 1
    return run_daemon(preload=not args.no_preload)


def _cmd_toggle(_args: argparse.Namespace) -> int:
    try:
        resp = ipc.send_request("toggle")
    except ConnectionError:
        print("服务未启动，请先运行: voice-paste daemon", file=sys.stderr)
        return 2
    if resp.get("ok"):
        print(resp.get("message", resp.get("state", "ok")))
        return 0
    print(f"错误: {resp.get('error', '未知错误')}", file=sys.stderr)
    return 1


def _cmd_status(_args: argparse.Namespace) -> int:
    try:
        resp = ipc.send_request("status")
    except ConnectionError:
        print("daemon: 未运行")
        return 2
    print(f"daemon  : 运行中 (v{resp.get('version')})")
    print(f"状态    : {resp.get('state')}")
    print(f"模型    : {resp.get('model')}  [{resp.get('device')}]")
    if resp.get("state") == "recording":
        print(f"已录音  : {resp.get('recording_seconds')} 秒")
    if resp.get("last_text"):
        print(f"上次文本: {resp.get('last_text')}")
    if resp.get("last_error"):
        print(f"上次错误: {resp.get('last_error')}")
    return 0


def _cmd_stop(_args: argparse.Namespace) -> int:
    try:
        resp = ipc.send_request("stop_daemon")
    except ConnectionError:
        print("daemon: 未运行")
        return 0
    print(resp.get("message", "已请求停止"))
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    if args.init:
        path = write_default_config()
        print(f"配置文件已写入: {path}")
        return 0
    path = config_path()
    cfg = load_config()
    print(f"配置文件: {path} ({'存在' if path.is_file() else '不存在，使用默认值'})")
    print("当前生效配置:")
    for field_name in cfg.__dataclass_fields__:  # type: ignore[attr-defined]
        print(f"  {field_name} = {getattr(cfg, field_name)!r}")
    return 0


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "✓" if ok else "✗"
    line = f"  [{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def _cmd_doctor(_args: argparse.Namespace) -> int:
    print("Voice Paste 环境自检")
    print(f"版本: {__version__}\n")

    print("音频录制:")
    rec = shutil.which("parecord") or shutil.which("arecord")
    _check("录音工具 (parecord/arecord)", bool(rec),
           rec or "请安装 pipewire-pulse 或 alsa-utils")

    print("\n剪贴板:")
    clip = shutil.which("wl-copy") or shutil.which("xclip") or shutil.which("xsel")
    _check("剪贴板工具 (wl-copy/xclip/xsel)", bool(clip),
           clip or "请安装 wl-clipboard")

    print("\n自动粘贴:")
    wt = shutil.which("wtype")
    yd = shutil.which("ydotool")
    if not wt and not yd:
        _check("wtype / ydotool", False, "未安装；将回退为仅复制到剪贴板")
    else:
        # 实测各方法能否真正工作（KDE/KWin 不支持 wtype 的 virtual-keyboard 协议）
        from voice_paste import paste as _paste
        working = []
        if wt:
            ok, err = _paste.probe_method("wtype")
            _check("wtype", ok, "可用" if ok else f"存在但不可用: {err}")
            if ok:
                working.append("wtype")
        if yd:
            ok, err = _paste.probe_method("ydotool")
            _check("ydotool", ok,
                   "可用" if ok else f"存在但不可用（ydotoold 未运行?）: {err}")
            if ok:
                working.append("ydotool")
        if not working:
            print("    ⚠ 已安装按键工具但均不可用；"
                  "KDE/KWin 请改用 ydotool 并启动 ydotoold（见 README）。")

    print("\n通知:")
    _check("notify-send", bool(shutil.which("notify-send")),
           shutil.which("notify-send") or "请安装 libnotify")

    print("\n语音识别:")
    try:
        import faster_whisper  # noqa: F401
        fw_ok = True
        detail = ""
    except Exception as exc:  # noqa: BLE001
        fw_ok = False
        detail = f"{exc}"
    _check("faster-whisper", fw_ok, detail or "已安装")

    cuda_detail = "未检测到（将使用 CPU）"
    cuda_ok = False
    try:
        import ctranslate2  # type: ignore
        n = ctranslate2.get_cuda_device_count()
        cuda_ok = n > 0
        cuda_detail = f"{n} 个 CUDA 设备" if cuda_ok else "无 CUDA 设备（CPU 模式）"
    except Exception as exc:  # noqa: BLE001
        cuda_detail = f"ctranslate2 不可用: {exc}"
    _check("CUDA GPU 加速", cuda_ok, cuda_detail)

    print("\n后台服务:")
    _check("daemon 运行中", ipc.daemon_running(),
           "已连接" if ipc.daemon_running() else "未运行，执行: voice-paste daemon")

    print("\nWayland:")
    import os
    sess = os.environ.get("XDG_SESSION_TYPE", "?")
    _check("Wayland 会话", sess == "wayland", f"XDG_SESSION_TYPE={sess}")

    # 关键链路是否齐全
    essential = bool(rec) and bool(clip) and fw_ok
    print()
    if essential:
        print("核心链路就绪：录音 + 剪贴板 + 识别可用。")
    else:
        print("核心链路缺少组件，请根据上面的提示安装。")
    return 0 if essential else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voice-paste",
        description="本地语音输入助手：快捷键录音 → 本地识别 → 自动粘贴。",
    )
    p.add_argument("--version", action="version", version=f"voice-paste {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pd = sub.add_parser("daemon", help="启动后台常驻服务")
    pd.add_argument("--no-preload", action="store_true",
                    help="不在启动时预加载模型（首次识别会变慢）")
    pd.set_defaults(func=_cmd_daemon)

    sub.add_parser("toggle", help="开始/停止录音（绑定全局快捷键）").set_defaults(func=_cmd_toggle)
    sub.add_parser("status", help="查询服务状态").set_defaults(func=_cmd_status)
    sub.add_parser("doctor", help="环境自检").set_defaults(func=_cmd_doctor)
    sub.add_parser("stop", help="停止后台服务").set_defaults(func=_cmd_stop)

    pc = sub.add_parser("config", help="查看或初始化配置")
    pc.add_argument("--init", action="store_true", help="写入默认配置文件")
    pc.set_defaults(func=_cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
