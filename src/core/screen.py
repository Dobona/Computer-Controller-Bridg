"""屏幕引擎：Pillow 截图（POC 版）。

截图与鼠标使用同一坐标系（物理像素、虚拟桌面），
保证“截图上的位置 = 鼠标落点”。
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageGrab


def grab(monitor=None) -> Image.Image:
    """截取屏幕，返回 PIL Image。monitor=None 时截取全部屏幕。"""
    if monitor is None:
        return ImageGrab.grab(all_screens=True)
    return ImageGrab.grab(monitor)


def grab_region(bbox: tuple[int, int, int, int]) -> Image.Image:
    """截取指定区域（虚拟桌面物理像素坐标），越界自动裁剪。"""
    img = grab()
    left, top, right, bottom = bbox
    img_w, img_h = img.size
    return img.crop(
        (
            max(0, left),
            max(0, top),
            min(img_w, right),
            min(img_h, bottom),
        )
    )


def screenshot_png(monitor=None) -> bytes:
    """截取屏幕并返回 PNG 字节。"""
    img = grab(monitor).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
