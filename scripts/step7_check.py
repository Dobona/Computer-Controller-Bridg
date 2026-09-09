"""步骤 7 真机验证：MCP 全量工具发现与调用、受控窗口真实输入、急停流程、非法参数。

运行方式：
    .venv/Scripts/python.exe scripts/step7_check.py
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastmcp import Client  # noqa: E402

from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from safety import panic  # noqa: E402
from server.mcp_server import create_server  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
EXPECTED_TOOLS = {
    "cursor_position",
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_right_click",
    "mouse_down",
    "mouse_up",
    "mouse_scroll",
    "mouse_long_press",
    "mouse_drag",
    "keyboard_type",
    "keyboard_hotkey",
    "keyboard_key",
    "wait",
    "emergency_stop",
    "self_test",
    "screenshot",
    "screen_info",
    "screen_analyze",
    "vlm_describe",
}
TEST_TEXT = "步骤7 验证文本"


def find_window_hwnd(root: tk.Tk, title: str) -> int:
    result: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == title:
                    result.append(hwnd)
        return True

    user32.EnumWindows(callback, None)
    return result[0]


def ensure_focused(root: tk.Tk, hwnd: int) -> None:
    deadline = time.time() + 6
    while time.time() < deadline:
        root.lift()
        root.attributes("-topmost", True)
        root.focus_force()
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        cur_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        if fg_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fg_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if fg_thread != cur_thread:
                user32.AttachThreadInput(cur_thread, fg_thread, False)
        root.update()
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.2)
    raise RuntimeError("无法取得前台焦点")


def pump(root: tk.Tk, duration: float) -> None:
    end = time.time() + duration
    while time.time() < end:
        root.update()
        time.sleep(0.005)


async def check_discovery(client) -> None:
    tools = await client.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names and len(names) == len(EXPECTED_TOOLS), names
    print(f"[1] 工具发现: 共 {len(names)} 个，完整工具表已注册")


async def check_basic_tools(client) -> None:
    res = await client.call_tool("cursor_position", {})
    assert res.data["ok"] is True
    res = await client.call_tool("screen_info", {})
    assert res.data["ok"] is True
    print(f"[2] 基础工具: cursor_position / screen_info / 屏幕 {res.data['data']['virtual']}")
    res = await client.call_tool("wait", {"ms": 50})
    assert res.data["ok"] is True and res.data["duration_ms"] >= 40
    print("[3] wait: 50ms 等待生效")


async def check_invalid_params(client) -> None:
    cases = [
        ("mouse_move", {"x": 999999, "y": 0}),
        ("mouse_click", {"button": "side"}),
        ("keyboard_key", {"key": "??"}),
        ("wait", {"ms": -1}),
        ("screen_analyze", {"scope": "bogus"}),
        ("vlm_describe", {"region": [1, 2]}),
    ]
    for name, args in cases:
        res = await client.call_tool(name, args)
        assert res.data["ok"] is False, f"{name} 应失败"
        assert "参数错误" in res.data["message"] or "越界" in res.data["message"], res.data["message"]
    print(f"[4] 非法参数: {len(cases)} 组全部返回明确错误")


async def check_controlled_window(client, root, win, button, text, save_file) -> None:
    hwnd = find_window_hwnd(root, "步骤7 验证窗口")
    ensure_focused(root, hwnd)
    bx = button.winfo_rootx() + button.winfo_width() // 2
    by = button.winfo_rooty() + button.winfo_height() // 2
    tx = text.winfo_rootx() + 120
    ty = text.winfo_rooty() + 30
    events = {"right": 0, "double": 0, "wheel": 0}
    button.bind("<Button-3>", lambda e: events.__setitem__("right", events["right"] + 1))
    button.bind("<Double-Button-1>", lambda e: events.__setitem__("double", events["double"] + 1))
    text.bind("<MouseWheel>", lambda e: events.__setitem__("wheel", events["wheel"] + e.delta))

    res = await client.call_tool("mouse_click", {"x": bx, "y": by, "button": "left"})
    assert res.data["ok"] is True
    res = await client.call_tool("mouse_double_click", {"x": bx, "y": by})
    assert res.data["ok"] is True
    res = await client.call_tool("mouse_right_click", {"x": bx, "y": by})
    assert res.data["ok"] is True
    pump(root, 0.3)
    assert events["double"] >= 1 and events["right"] >= 1, events
    print("[5] 鼠标工具: 单击/双击/右键均被窗口接收")

    res = await client.call_tool("mouse_down", {"button": "left", "x": bx, "y": by})
    assert res.data["ok"] is True and "left" in res.data["data"]["held"]
    res = await client.call_tool("mouse_up", {"button": "left"})
    assert res.data["ok"] is True and res.data["data"]["held"] == []
    print("[6] mouse_down/mouse_up: 按住状态登记与释放正确")

    res = await client.call_tool("mouse_scroll", {"delta": 120, "x": tx, "y": ty})
    assert res.data["ok"] is True
    pump(root, 0.3)
    assert events["wheel"] != 0, "滚轮事件未到达"
    print(f"[7] mouse_scroll: 滚轮事件到达（delta={events['wheel']}）")

    ensure_focused(root, hwnd)
    text.focus_set()
    pump(root, 0.2)
    res = await client.call_tool("keyboard_type", {"text": TEST_TEXT, "interval_ms": 20})
    assert res.data["ok"] is True
    res = await client.call_tool("keyboard_key", {"key": "end"})
    assert res.data["ok"] is True
    res = await client.call_tool("keyboard_key", {"key": "enter", "hold_ms": 150})
    assert res.data["ok"] is True
    pump(root, 0.5)
    got = text.get("1.0", "end-1c")
    assert got == TEST_TEXT + "\n", f"键盘输入异常: {got!r}"
    res = await client.call_tool("keyboard_hotkey", {"keys": ["ctrl", "s"]})
    assert res.data["ok"] is True
    pump(root, 0.5)
    saved = save_file.read_text(encoding="utf-8")
    assert saved == TEST_TEXT + "\n", f"保存异常: {saved!r}"
    print("[8] 键盘工具: 中文输入 / 单键 / 长按 / Ctrl+S 保存全部生效")


async def check_screen_tools(client) -> None:
    res = await client.call_tool("screen_analyze", {"scope": "full", "with_uia": False, "with_ocr": False})
    assert res.data["ok"] is True and res.data["data"]["windows"]
    print("[9] screen_analyze: 返回窗口列表成功")
    res = await client.call_tool("screenshot", {})
    assert res.data is None and res.content and res.content[0].mimeType == "image/png"
    print("[10] screenshot: 返回 PNG 图片")
    res = await client.call_tool("vlm_describe", {})
    assert res.data["ok"] is False and "未配置 VLM 端点" in res.data["message"]
    print("[11] vlm_describe: 未配置提示明确")


async def check_emergency_stop(client) -> None:
    res = await client.call_tool("mouse_down", {"button": "left"})
    assert res.data["ok"] is True
    res = await client.call_tool("emergency_stop", {})
    assert res.data["ok"] is True
    assert res.data["data"]["released_buttons"] == ["left"]
    assert panic.interrupted() is True
    res = await client.call_tool("cursor_position", {})
    assert res.data["ok"] is False and "急停状态" in res.data["message"]
    panic.clear_interrupt()
    res = await client.call_tool("cursor_position", {})
    assert res.data["ok"] is True
    print("[12] emergency_stop: 释放按键、拒绝新指令、复位后恢复")


async def main_async(client) -> None:
    await check_discovery(client)
    await check_basic_tools(client)
    await check_invalid_params(client)
    res = await client.call_tool("self_test", {"dry_run": True})
    assert res.data["ok"] is True, res.data
    print(f"[13] self_test: 引擎自检通过（uia={res.data['data']['uia']}, ocr={res.data['data']['ocr']}）")


def main() -> None:
    set_dpi_awareness()
    panic.clear_interrupt()
    server = create_server()

    async def run_with_window():
        save_file = Path(tempfile.gettempdir()) / "cc_step7_save.txt"
        save_file.unlink(missing_ok=True)
        vx, vy, vw, vh = virtual_screen()
        root = tk.Tk()
        root.title("步骤7 验证窗口")
        root.geometry(f"520x300+{vx + vw - 600}+{vy + 80}")
        root.attributes("-topmost", True)
        button = tk.Button(root, text="验证按钮", font=("Microsoft YaHei", 13))
        button.pack(side="bottom", pady=8)
        text = tk.Text(root, font=("Microsoft YaHei", 12))
        text.pack(fill="both", expand=True)

        def on_ctrl_s(_event=None):
            save_file.write_text(text.get("1.0", "end-1c"), encoding="utf-8")
            return "break"

        text.bind("<Control-s>", on_ctrl_s)
        for _ in range(5):
            root.update()
            time.sleep(0.15)
        try:
            async with Client(server) as client:
                await main_async(client)
                await check_controlled_window(client, root, root, button, text, save_file)
                await check_screen_tools(client)
                await check_emergency_stop(client)
        finally:
            root.destroy()
            if save_file.exists():
                save_file.unlink()

    asyncio.run(run_with_window())
    print("== 步骤7 真机验证全部通过 ==")


if __name__ == "__main__":
    main()
