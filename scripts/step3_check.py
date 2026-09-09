"""步骤 3 真机验证：滚轮、按下/松开、坐标查询（全部在受控窗口中完成，不影响用户桌面）。

运行方式：
    .venv/Scripts/python.exe scripts/step3_check.py
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.coords import get_cursor_pos, set_dpi_awareness, virtual_screen  # noqa: E402
from core.mouse import button_down, button_up, move_to, scroll  # noqa: E402


def _place_window(root: tk.Tk, w: int, h: int, offset_y: int) -> tuple[int, int]:
    """把窗口放在屏幕右侧（避开任务栏），返回窗口左上角坐标。"""
    vx, vy, vw, vh = virtual_screen()
    wx, wy = vx + vw - w - 80, vy + offset_y
    root.geometry(f"{w}x{h}+{wx}+{wy}")
    root.attributes("-topmost", True)
    root.update()
    time.sleep(0.3)
    return wx, wy


def check_scroll() -> None:
    """滚轮：文本视口应随滚轮上下移动。"""
    root = tk.Tk()
    root.title("步骤3 滚轮测试")
    wx, wy = _place_window(root, 360, 260, 80)
    text = tk.Text(root, font=("Microsoft YaHei", 12))
    text.pack(fill="both", expand=True)
    for i in range(100):
        text.insert("end", f"第 {i} 行内容\n")
    root.update()

    # 鼠标移到文本区域中央（窗口上半部，避开可能的滚动条区域）
    cx, cy = wx + 180, wy + 60
    move_to(cx, cy, duration_ms=200)
    time.sleep(0.2)

    before = text.yview()[0]
    scroll(-120)  # 向下滚动一格
    root.update()
    time.sleep(0.2)
    after_down = text.yview()[0]
    scroll(120)  # 向上滚动一格
    root.update()
    time.sleep(0.2)
    after_up = text.yview()[0]
    root.destroy()

    print(f"[1] 滚轮: 视口 {before:.3f} → 向下 {after_down:.3f} → 向上 {after_up:.3f}")
    assert after_down > before, f"向下滚动未生效: {before} → {after_down}"
    assert after_up < after_down, f"向上滚动未生效: {after_down} → {after_up}"


def check_button_down_up() -> None:
    """按下/松开：受控窗口应依次收到 ButtonPress 与 ButtonRelease。"""
    root = tk.Tk()
    root.title("步骤3 按下/松开测试")
    wx, wy = _place_window(root, 260, 140, 380)
    label = tk.Label(root, text="按下/松开测试：按住左键", font=("Microsoft YaHei", 12))
    label.pack(expand=True)
    recorded: list[str] = []
    root.bind("<ButtonPress-1>", lambda e: recorded.append("down"))
    root.bind("<ButtonRelease-1>", lambda e: recorded.append("up"))
    root.update()

    center = (wx + 130, wy + 70)
    move_to(*center, duration_ms=200)
    time.sleep(0.2)
    button_down("left")
    time.sleep(0.15)
    button_up("left")
    deadline = time.time() + 3
    while time.time() < deadline and len(recorded) < 2:
        root.update()
        time.sleep(0.02)
    root.destroy()

    print(f"[2] 按下/松开: 窗口收到 {recorded}")
    assert recorded == ["down", "up"], f"按下/松开事件顺序不符: {recorded}"


def check_cursor_query() -> None:
    """坐标查询：移动后 GetCursorPos 精确命中目标。"""
    vx, vy, vw, vh = virtual_screen()
    target = (vx + vw // 2, vy + vh // 2)
    move_to(*target, duration_ms=300)
    got = get_cursor_pos()
    print(f"[3] 坐标查询: 目标 {target} → 实际 {got}")
    assert got == target, f"光标位置不符: {got} != {target}"


def main() -> None:
    set_dpi_awareness()
    print("== 步骤3 真机验证开始 ==")
    check_scroll()
    check_button_down_up()
    check_cursor_query()
    print("== 步骤3 真机验证全部通过 ==")


if __name__ == "__main__":
    main()
