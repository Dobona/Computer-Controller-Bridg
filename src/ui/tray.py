"""托盘图标：绿色 = 运行中。

右键菜单：查看运行状态 / 打开日志 / 打开配置 / 立即停止。
托盘在独立线程运行（pystray run_detached），启动失败不影响服务本身。
"""

from __future__ import annotations

import ctypes
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_FILE = ROOT / "logs" / "server.log"
CONFIG_FILE = ROOT / "config.json"

_icon_ref = None
_status_provider = None
_stop_callback = None
_lock = threading.Lock()


def _make_green_icon():
    """生成绿色圆点图标（运行中）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(46, 160, 67, 255))
    draw.ellipse((14, 14, 50, 50), fill=(255, 255, 255, 255))
    return img


def _open_with_default_app(path: Path) -> None:
    try:
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
    except Exception:
        pass


def _show_message(title: str, text: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)  # MB_OK | MB_ICONINFORMATION
    except Exception:
        pass


def _show_status(icon=None, item=None) -> None:
    text = "运行中"
    if _status_provider is not None:
        try:
            text = _status_provider()
        except Exception:
            text = "运行中（状态读取失败）"
    _show_message("Computer Controller", text)


def _stop(icon=None, item=None) -> None:
    """托盘“立即停止”：退出托盘并调用停止回调（由服务收尾后退出进程）。"""
    stop()
    callback = _stop_callback
    if callback is not None:
        callback()


def start(status_provider=None, stop_callback=None) -> bool:
    """启动托盘图标（后台线程）。返回是否成功启动。"""
    global _icon_ref, _status_provider, _stop_callback
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception:
        return False
    with _lock:
        _status_provider = status_provider
        _stop_callback = stop_callback
        menu = Menu(
            MenuItem("查看运行状态…", _show_status, default=True),
            MenuItem("打开日志", lambda icon, item: _open_with_default_app(LOG_FILE)),
            MenuItem("打开配置", lambda icon, item: _open_with_default_app(CONFIG_FILE)),
            Menu.SEPARATOR,
            MenuItem("立即停止", _stop),
        )
        icon = pystray.Icon(
            "computer-controller",
            _make_green_icon(),
            "Computer Controller — 运行中",
            menu,
        )
        _icon_ref = icon
        try:
            icon.run_detached()
        except Exception:
            return False
    return True


def stop() -> None:
    """停止托盘图标（不触发停止回调）。"""
    with _lock:
        icon = _icon_ref
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass
