"""窗口枚举：标题、类名、矩形、前后台顺序（纯 ctypes、零依赖）。

坐标使用物理像素（虚拟桌面坐标系），与鼠标/截图坐标系一致。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_GETTEXTLENGTH = 0x000E
WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002
_PID = os.getpid()


user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(wintypes.DWORD),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM


def _send_message_timeout(hwnd: int, msg: int, wparam: int, lparam: int, timeout_ms: int = 300):
    result = wintypes.DWORD()
    ok = user32.SendMessageTimeoutW(
        hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result)
    )
    return bool(ok), result.value


def _get_window_text(hwnd: int) -> str:
    if _get_window_pid(hwnd) == _PID:
        # 同进程窗口：GetWindowText 会向目标窗口发送 WM_GETTEXT，若目标线程
        # 忙于其他调用（如 MCP 工具在工作线程执行）会死锁；改用限时消息。
        ok, length = _send_message_timeout(hwnd, WM_GETTEXTLENGTH, 0, 0)
        if not ok or length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ok, _ = _send_message_timeout(
            hwnd, WM_GETTEXT, length + 1, ctypes.cast(buf, ctypes.c_void_p).value
        )
        return buf.value if ok else ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0, 0, 0)
    return rect.left, rect.top, rect.right, rect.bottom


def _get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _window_info(hwnd: int, active: bool) -> dict:
    left, top, right, bottom = _get_window_rect(hwnd)
    return {
        "hwnd": hwnd,
        "title": _get_window_text(hwnd),
        "class": _get_window_class(hwnd),
        "rect": [left, top, right, bottom],
        "pid": _get_window_pid(hwnd),
        "active": active,
    }


def _enum_windows(callback) -> None:
    """枚举所有顶层窗口，按 Z 序（前台在前）。"""
    user32.EnumWindows(callback, 0)


def _is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def _foreground_window() -> int:
    return user32.GetForegroundWindow()


def list_windows(with_title_only: bool = True, visible_only: bool = True) -> list[dict]:
    """返回顶层窗口列表（前台在前）。"""
    result: list[dict] = []
    foreground = _foreground_window()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if visible_only and not _is_visible(hwnd):
            return True
        info = _window_info(hwnd, active=(hwnd == foreground))
        if with_title_only and not info["title"].strip():
            return True
        result.append(info)
        return True

    _enum_windows(callback)
    return result


def active_window() -> dict | None:
    """返回当前前台窗口信息（无前台窗口时返回 None）。"""
    hwnd = _foreground_window()
    if not hwnd:
        return None
    return _window_info(hwnd, active=True)
