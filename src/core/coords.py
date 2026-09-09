"""坐标系与屏幕信息：DPI 感知、虚拟桌面坐标、坐标校验。

约定：所有坐标均为物理像素，使用虚拟桌面坐标系
（主屏左上角为原点，副屏可为负坐标，与 Windows 一致）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

# GetSystemMetrics 索引
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1

PROCESS_PER_MONITOR_DPI_AWARE = 2
E_ACCESSDENIED = 0x80070005

_dpi_aware = False


def set_dpi_awareness() -> None:
    """设置进程 DPI 感知（每显示器感知），保证坐标使用物理像素。

    若已被系统或其它库设置过，则视为成功（E_ACCESSDENIED）。
    """
    global _dpi_aware
    if _dpi_aware:
        return
    try:
        hr = shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
        if hr != 0 and hr != E_ACCESSDENIED:
            raise ctypes.WinError(hr)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
    _dpi_aware = True


def virtual_screen() -> tuple[int, int, int, int]:
    """返回虚拟桌面 (left, top, width, height)，物理像素。"""
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return left, top, width, height


def screen_info() -> dict:
    """返回屏幕布局信息（虚拟桌面 + 主屏 + 系统 DPI 缩放）。"""
    vx, vy, vw, vh = virtual_screen()
    dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
    return {
        "virtual": {"left": vx, "top": vy, "width": vw, "height": vh},
        "primary": {
            "width": user32.GetSystemMetrics(SM_CXSCREEN),
            "height": user32.GetSystemMetrics(SM_CYSCREEN),
        },
        "dpi_scale": round(dpi / 96.0, 2),
    }


def validate_coords(x: int, y: int) -> None:
    """校验坐标是否在虚拟桌面范围内，越界抛出 ValueError。"""
    vx, vy, vw, vh = virtual_screen()
    if not (vx <= x < vx + vw and vy <= y < vy + vh):
        raise ValueError(
            f"坐标越界: ({x}, {y}) 不在虚拟桌面范围 "
            f"({vx},{vy})~({vx + vw - 1},{vy + vh - 1})"
        )


def normalize_abs(x: int, y: int) -> tuple[int, int]:
    """物理像素坐标 → SendInput 绝对坐标（0~65535，虚拟桌面范围）。"""
    vx, vy, vw, vh = virtual_screen()
    nx = int((x - vx) * 65535 / (vw - 1))
    ny = int((y - vy) * 65535 / (vh - 1))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def get_cursor_pos() -> tuple[int, int]:
    """查询当前鼠标位置（物理像素）。"""
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError(ctypes.get_last_error())
    return point.x, point.y
