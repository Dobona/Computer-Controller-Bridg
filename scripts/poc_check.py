"""POC 验证脚本：验证 DPI/坐标、鼠标移动与单击、截图对齐、MCP 工具发现与调用。

运行方式：
    .venv\\Scripts\\python.exe scripts\\poc_check.py
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.coords import get_cursor_pos, screen_info, set_dpi_awareness, virtual_screen  # noqa: E402
from core.mouse import click, move_to  # noqa: E402
from core.screen import grab  # noqa: E402


def check_screen_and_coords() -> dict:
    set_dpi_awareness()
    info = screen_info()
    vx, vy, vw, vh = virtual_screen()
    print(f"[1] 屏幕信息: {info}")
    assert vw == info["primary"]["width"] and vh == info["primary"]["height"], "单屏下虚拟桌面应等于主屏"
    return info


def check_mouse_move() -> None:
    vx, vy, vw, vh = virtual_screen()
    target = (vx + vw // 2, vy + vh // 2)
    move_to(*target, duration_ms=300)
    got = get_cursor_pos()
    print(f"[2] 鼠标移动: 目标 {target} → 实际 {got}")
    assert got == target, f"移动后位置不符: {got} != {target}"


def check_screenshot_size() -> None:
    vx, vy, vw, vh = virtual_screen()
    img = grab()
    print(f"[3] 截图尺寸: {img.size}（虚拟桌面 {vw}x{vh}）")
    assert img.size == (vw, vh), f"截图尺寸 {img.size} 与虚拟桌面 {vw}x{vh} 不一致"


def check_click_window() -> None:
    """在受控的测试窗口中验证单击：点击窗口中心，窗口应收到点击并记录坐标。"""
    vx, vy, vw, vh = virtual_screen()
    win_w, win_h = 220, 120
    wx, wy = vx + vw - win_w - 60, vy + 60
    click_center = (wx + win_w // 2, wy + win_h // 2)
    recorded: list[tuple[int, int]] = []

    root = tk.Tk()
    root.title("POC 点击测试")
    root.geometry(f"{win_w}x{win_h}+{wx}+{wy}")
    label = tk.Label(root, text="POC 单击测试：点击此窗口", font=("Microsoft YaHei", 12))
    label.pack(expand=True)

    def on_click(event: tk.Event) -> None:
        recorded.append((event.x_root, event.y_root))
        root.after(150, root.destroy)

    root.bind("<Button-1>", on_click)
    root.update()
    time.sleep(0.3)

    move_to(*click_center, duration_ms=200)
    click(button="left")
    deadline = time.time() + 3
    while time.time() < deadline and not recorded:
        root.update()
        time.sleep(0.02)
    root.destroy()

    print(f"[4] 受控窗口点击: 目标 {click_center} → 窗口记录 {recorded}")
    assert recorded, "窗口未收到点击事件，SendInput 单击失败"
    rx, ry = recorded[0]
    assert abs(rx - click_center[0]) <= 3 and abs(ry - click_center[1]) <= 3, (
        f"点击落点偏差过大: {recorded[0]} vs {click_center}"
    )


async def check_mcp_server() -> None:
    """进程内 Client 验证工具发现与调用（等价于 Codex 的 MCP 连接）。"""
    from fastmcp import Client

    from server.mcp_server import create_server

    server = create_server()
    async with Client(server) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        print(f"[5] MCP 发现工具: {names}")
        assert "cursor_position" in names and "mouse_move" in names and "mouse_click" in names and "screenshot" in names

        res = await client.call_tool("cursor_position", {})
        print(f"[5] cursor_position 返回: {res.data}")
        assert res.data["ok"] is True

        vx, vy, vw, vh = virtual_screen()
        res = await client.call_tool("mouse_move", {"x": vx + 10, "y": vy + 10, "duration_ms": 100})
        print(f"[5] mouse_move 返回: {res.data}")
        assert res.data["ok"] is True

        res = await client.call_tool("mouse_click", {"x": vx + 10, "y": vy + 10, "button": "left", "times": 1})
        print(f"[5] mouse_click 返回: {res.data}")
        assert res.data["ok"] is True

        res = await client.call_tool("screenshot", {})
        img_content = res.content[0]
        png = base64.b64decode(img_content.data)
        print(
            f"[5] screenshot 返回: {type(img_content).__name__}, "
            f"mime={img_content.mimeType}, PNG 大小={len(png)} 字节"
        )
        assert getattr(img_content, "mimeType", None) == "image/png"
        assert png[:8] == b"\x89PNG\r\n\x1a\n", "返回内容不是有效 PNG"


def main() -> None:
    print("== POC 验证开始 ==")
    check_screen_and_coords()
    check_mouse_move()
    check_screenshot_size()
    check_click_window()
    asyncio.run(check_mcp_server())
    print("== POC 验证全部通过 ==")


if __name__ == "__main__":
    main()
