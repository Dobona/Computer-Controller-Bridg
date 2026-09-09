"""步骤 5 真机验证：长按、拖拽（拖动窗口）、急停中断释放（全部在自建受控窗口上完成）。

运行方式：
    .venv/Scripts/python.exe scripts/step5_check.py
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import action  # noqa: E402
from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from safety import panic  # noqa: E402


def pump_until(root: tk.Tk, predicate, timeout: float = 8.0) -> None:
    """主线程泵 Tk 消息，直到 predicate() 为真或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    raise RuntimeError("等待超时")


def check_long_press(root: tk.Tk) -> None:
    """长按：受控按钮应收到按下与松开，间隔约等于 hold_ms。"""
    vx, vy, vw, vh = virtual_screen()
    win = tk.Toplevel(root)
    win.title("步骤5 长按测试")
    win.geometry(f"300x160+{vx + vw - 380}+{vy + 80}")
    win.attributes("-topmost", True)
    button = tk.Button(win, text="长按测试按钮", font=("Microsoft YaHei", 14))
    button.pack(fill="both", expand=True)
    events: list[tuple[str, float]] = []
    button.bind("<ButtonPress-1>", lambda e: events.append(("press", time.perf_counter())))
    button.bind("<ButtonRelease-1>", lambda e: events.append(("release", time.perf_counter())))
    win.update()
    bx = button.winfo_rootx() + button.winfo_width() // 2
    by = button.winfo_rooty() + button.winfo_height() // 2

    t = threading.Thread(
        target=lambda: action.mouse_long_press(x=bx, y=by, hold_ms=800), daemon=True
    )
    t.start()
    pump_until(root, lambda: not t.is_alive())
    t.join()
    win.destroy()

    assert len(events) == 2, f"长按事件缺失: {events}"
    held = events[1][1] - events[0][1]
    print(f"[1] 长按: 按下→松开间隔 {held * 1000:.0f}ms（目标 800ms）")
    assert 600 <= held * 1000 <= 1200, f"长按时长异常: {held * 1000:.0f}ms"


def check_drag_window(root: tk.Tk) -> None:
    """拖拽：应用内 B1-Motion 拖拽，窗口应随拖拽位移。

    说明：本机（虚拟/远程桌面环境）下，注入的鼠标事件无法驱动系统标题栏
    拖拽的模态循环（单击/移动/事件流均正常，实测标题栏拖拽不移动窗口），
    因此用应用内拖拽处理器验证：按住 → 移动 → 松开，窗口跟随光标移动。
    """
    vx, vy, vw, vh = virtual_screen()
    win = tk.Toplevel(root)
    win.title("步骤5 拖拽测试")
    w, h = 320, 180
    win.geometry(f"{w}x{h}+{vx + vw - w - 80}+{vy + 300}")
    win.attributes("-topmost", True)
    label = tk.Label(win, text="拖动此区域（应用内拖拽）", font=("Microsoft YaHei", 14), bg="#d0e8ff")
    label.pack(fill="both", expand=True)
    drag_anchor: list[tuple[int, int] | None] = [None]

    def on_press(event):
        drag_anchor[0] = (event.x_root - win.winfo_x(), event.y_root - win.winfo_y())

    def on_motion(event):
        if drag_anchor[0] is not None:
            dx0, dy0 = drag_anchor[0]
            win.geometry(f"+{event.x_root - dx0}+{event.y_root - dy0}")

    label.bind("<ButtonPress-1>", on_press)
    label.bind("<B1-Motion>", on_motion)
    win.update()
    old_x, old_y = win.winfo_x(), win.winfo_y()
    start_x = label.winfo_rootx() + label.winfo_width() // 2
    start_y = label.winfo_rooty() + label.winfo_height() // 2
    dx, dy = 160, 90
    target_x, target_y = start_x + dx, start_y + dy

    t = threading.Thread(
        target=lambda: action.mouse_drag(
            start_x, start_y, target_x, target_y, duration_ms=600, move_step_ms=10
        ),
        daemon=True,
    )
    t.start()
    pump_until(root, lambda: not t.is_alive())
    t.join()
    new_x, new_y = win.winfo_x(), win.winfo_y()
    win.destroy()

    moved_x, moved_y = new_x - old_x, new_y - old_y
    print(f"[2] 拖拽: 窗口位移 ({moved_x}, {moved_y})（目标约 {dx}, {dy}）")
    assert abs(moved_x - dx) <= 40 and abs(moved_y - dy) <= 40, (
        f"窗口位移异常: ({moved_x}, {moved_y}) vs 期望 ({dx}, {dy})"
    )


def check_interrupt_release(root: tk.Tk) -> None:
    """急停中断：长按进行中触发急停，应立即松开并抛出 ActionInterrupted。"""
    vx, vy, vw, vh = virtual_screen()
    win = tk.Toplevel(root)
    win.title("步骤5 中断测试")
    win.geometry(f"300x160+{vx + vw - 380}+{vy + 520}")
    win.attributes("-topmost", True)
    button = tk.Button(win, text="中断测试按钮", font=("Microsoft YaHei", 14))
    button.pack(fill="both", expand=True)
    events: list[tuple[str, float]] = []
    button.bind("<ButtonPress-1>", lambda e: events.append(("press", time.perf_counter())))
    button.bind("<ButtonRelease-1>", lambda e: events.append(("release", time.perf_counter())))
    win.update()
    bx = button.winfo_rootx() + button.winfo_width() // 2
    by = button.winfo_rooty() + button.winfo_height() // 2

    outcome: list[Exception | None] = [None]

    def run():
        try:
            action.mouse_long_press(x=bx, y=by, hold_ms=5000)
        except action.ActionInterrupted as exc:
            outcome[0] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.35)  # 等按下发生
    panic.request_interrupt()
    pump_until(root, lambda: not t.is_alive())
    t.join()
    win.destroy()
    panic.clear_interrupt()

    assert outcome[0] is not None, "中断后未抛出 ActionInterrupted"
    assert len(events) == 2, f"中断后按键未释放: {events}"
    print(f"[3] 急停中断: 动作中止并抛错（{outcome[0]}），按键已释放（事件: {[e[0] for e in events]}）")
    assert events[0][0] == "press" and events[1][0] == "release"


def main() -> None:
    set_dpi_awareness()
    panic.clear_interrupt()
    root = tk.Tk()
    root.withdraw()
    try:
        check_long_press(root)
        check_drag_window(root)
        check_interrupt_release(root)
        print("== 步骤5 真机验证全部通过 ==")
    finally:
        root.destroy()
        panic.clear_interrupt()


if __name__ == "__main__":
    main()
