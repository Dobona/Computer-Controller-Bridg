"""步骤 6 真机验证：屏幕理解引擎（窗口枚举 + UIA 元素 + OCR 文本 + VLM 未配置提示）。

运行方式：
    .venv/Scripts/python.exe scripts/step6_check.py
"""

from __future__ import annotations

import asyncio
import ctypes
import subprocess
import sys
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.analyze import analyze  # noqa: E402
from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from core import windows  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
MARKER = "屏幕理解测试专用文本CC"
WIN_TITLE = "步骤6 屏幕理解测试"


def _rect_contains(outer: list[int], inner: list[int]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def check_controlled_window() -> None:
    """受控窗口：窗口枚举 + OCR 中文文本与坐标一致性。"""
    vx, vy, vw, vh = virtual_screen()
    root = tk.Tk()
    root.title(WIN_TITLE)
    w, h = 420, 200
    root.geometry(f"{w}x{h}+{vx + vw - w - 80}+{vy + 80}")
    root.attributes("-topmost", True)
    tk.Label(root, text=MARKER, font=("Microsoft YaHei", 18), bg="white").pack(pady=20)
    tk.Button(root, text="确定", font=("Microsoft YaHei", 14)).pack(pady=10)
    root.update()
    time.sleep(0.3)
    result = analyze(scope="full")
    root.destroy()

    wins = [x for x in result["windows"] if x["title"] == WIN_TITLE]
    assert wins, f"窗口枚举未找到自建窗口，现有标题: {[x['title'] for x in result['windows'][:10]]}"
    win_rect = wins[0]["rect"]
    print(f"[1] 窗口枚举: 找到「{WIN_TITLE}」 rect={win_rect}")

    texts = result["texts"]
    hit = [t for t in texts if MARKER in t["text"]]
    assert hit, f"OCR 未识别到标记文本，识别到的文本: {[t['text'] for t in texts[:10]]}"
    assert _rect_contains(win_rect, hit[0]["rect"]), (
        f"OCR 坐标与窗口不一致: 窗口 {win_rect} vs 文本 {hit[0]['rect']}"
    )
    print(f"[2] OCR 中文: 「{hit[0]['text']}」 坐标 {hit[0]['rect']}（在窗口内，与鼠标坐标系一致）")


def check_calculator_uia() -> None:
    """计算器：真实应用 UIA 元素树（按钮名称 + 坐标）。"""
    proc = subprocess.Popen(["calc.exe"])
    deadline = time.time() + 10
    calc_win = None
    while time.time() < deadline and calc_win is None:
        for w in windows.list_windows():
            if "计算器" in w["title"] or "Calculator" in w["title"]:
                calc_win = w
                break
        if calc_win is None:
            time.sleep(0.3)
    assert calc_win, "未找到计算器窗口"
    time.sleep(1.0)  # 等 UWP 界面加载

    result = analyze(scope="full")
    calc = [x for x in result["windows"] if x["hwnd"] == calc_win["hwnd"]]
    assert calc, "窗口枚举丢失计算器"
    win_rect = calc[0]["rect"]
    buttons = [
        e for e in result["elements"]
        if e.get("type") == "button" and _rect_contains(win_rect, e["rect"])
    ]
    names = [b["name"] for b in buttons]
    print(f"[3] UIA 元素: 计算器内按钮 {len(buttons)} 个，示例 {names[:8]}")
    assert len(buttons) >= 10, f"计算器 UIA 按钮过少: {names}"
    assert any("Backspace" in n or "清除" in n or any(ch.isdigit() for ch in n) for n in names), (
        f"缺少数字/操作按钮: {names[:20]}"
    )
    # 坐标一致性：取第一个按钮，中心应在窗口矩形内
    b = buttons[0]
    center = ((b["rect"][0] + b["rect"][2]) // 2, (b["rect"][1] + b["rect"][3]) // 2)
    assert win_rect[0] <= center[0] <= win_rect[2] and win_rect[1] <= center[1] <= win_rect[3]
    print(f"[4] 坐标一致: 按钮「{b['name']}」中心 {center} 在窗口 {win_rect} 内")

    # 关闭计算器（只结束该窗口进程，安全无数据）
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(calc_win["hwnd"], ctypes.byref(pid))
    subprocess.run(["taskkill", "/PID", str(pid.value), "/F"], capture_output=True)
    if proc.poll() is None:
        proc.kill()


async def check_mcp_tools() -> None:
    """MCP 注册：screen_analyze / vlm_describe 可用，未配置 VLM 返回明确提示。"""
    from fastmcp import Client

    from server.mcp_server import create_server

    server = create_server()
    async with Client(server) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        assert "screen_analyze" in names and "vlm_describe" in names, names
        print(f"[5] MCP 工具: 共 {len(names)} 个，screen_analyze / vlm_describe 已注册")

        res = await client.call_tool("vlm_describe", {})
        data = res.data
        print(f"[6] vlm_describe 未配置提示: {data['message'][:50]}...")
        assert data["ok"] is False
        assert "未配置 VLM 端点" in data["message"]


def main() -> None:
    set_dpi_awareness()
    check_controlled_window()
    check_calculator_uia()
    asyncio.run(check_mcp_tools())
    print("== 步骤6 真机验证全部通过 ==")


if __name__ == "__main__":
    main()
