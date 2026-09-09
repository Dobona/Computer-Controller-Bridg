"""步骤 8 真机验证：急停热键、看门狗子进程、审计日志、动作超时。

运行方式：
    .venv/Scripts/python.exe scripts/step8_check.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastmcp import Client  # noqa: E402

from core import action  # noqa: E402
from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from core.keyboard import hotkey  # noqa: E402
from core.mouse import button_down, held_buttons  # noqa: E402
from safety import audit, panic, watchdog  # noqa: E402
from server.mcp_server import create_server  # noqa: E402


def check_panic_hotkey(root: tk.Tk) -> None:
    """急停热键：按住鼠标时按下 Ctrl+Alt+Shift+Esc → 立即释放；再按一次 → 复位。"""
    vx, vy, vw, vh = virtual_screen()
    win = tk.Toplevel(root)
    win.title("步骤8 热键测试")
    win.geometry(f"300x160+{vx + vw - 380}+{vy + 80}")
    win.attributes("-topmost", True)
    button = tk.Button(win, text="按住测试", font=("Microsoft YaHei", 14))
    button.pack(fill="both", expand=True)
    events: list[str] = []
    button.bind("<ButtonPress-1>", lambda e: events.append("down"))
    button.bind("<ButtonRelease-1>", lambda e: events.append("up"))
    for _ in range(5):
        win.update()
        time.sleep(0.1)
    bx = button.winfo_rootx() + button.winfo_width() // 2
    by = button.winfo_rooty() + button.winfo_height() // 2

    panic.clear_interrupt()
    panic.start()
    button_down("left", x=bx, y=by)
    win.update()
    time.sleep(0.3)
    assert events == ["down"], f"按下未生效: {events}"
    hotkey(["ctrl", "alt", "shift", "esc"])
    deadline = time.time() + 2
    while time.time() < deadline and "up" not in events:
        win.update()
        time.sleep(0.05)
    assert panic.interrupted() is True, "热键未触发急停"
    assert events == ["down", "up"], f"急停后按键未释放: {events}"
    print("[1] 急停热键: 按下后立即中止并释放鼠标按键")

    hotkey(["ctrl", "alt", "shift", "esc"])
    deadline = time.time() + 2
    while time.time() < deadline and panic.interrupted():
        time.sleep(0.05)
    assert not panic.interrupted(), "再次按下热键未复位"
    print("[2] 急停热键: 再次按下 = 复位，恢复接受指令")
    win.destroy()


def check_watchdog() -> None:
    """看门狗：主进程被强杀后，按状态文件释放按键并写标记。"""
    tmp = Path(tempfile.gettempdir())
    state_file = tmp / "cc_watchdog_state.json"
    watchdog.write_state(state_file, {"held_keys": ["ctrl"], "held_buttons": ["left"]})
    stub = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    dog = watchdog.spawn(state_file, main_pid=stub.pid)
    time.sleep(1.0)
    subprocess.run(["taskkill", "/PID", str(stub.pid), "/F"], capture_output=True)
    marker = state_file.with_suffix(".released")
    deadline = time.time() + 10
    while time.time() < deadline and not marker.exists():
        time.sleep(0.2)
    assert marker.exists(), "看门狗未在预期时间内写出释放标记"
    result = json.loads(marker.read_text(encoding="utf-8"))
    assert result["released_keys"] == ["ctrl"], result
    assert result["released_buttons"] == ["left"], result
    dog.wait(timeout=10)
    state_file.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    print("[3] 看门狗: 主进程被强杀后自动释放按键与鼠标按钮")


async def check_audit() -> None:
    """审计：工具调用写入 JSONL，含时间/工具/参数/结果/耗时。"""
    audit.set_enabled(True)
    audit.set_log_dir(Path(__file__).resolve().parents[1] / "logs")
    today = time.strftime("%Y%m%d")
    audit_file = Path(__file__).resolve().parents[1] / "logs" / f"audit-{today}.jsonl"
    before = audit_file.read_text(encoding="utf-8").count("\n") if audit_file.exists() else 0

    server = create_server()
    async with Client(server) as client:
        await client.call_tool("cursor_position", {})
        await client.call_tool("wait", {"ms": 10})
        await client.call_tool("mouse_move", {"x": 100, "y": 100})
        await client.call_tool("vlm_describe", {})
    time.sleep(0.2)
    lines = audit_file.read_text(encoding="utf-8").splitlines()[before:]
    entries = [json.loads(line) for line in lines if line.strip()]
    tools = {e["tool"] for e in entries}
    assert {"cursor_position", "wait", "mouse_move", "vlm_describe"} <= tools, tools
    assert all("ts" in e and "duration_ms" in e for e in entries)
    print(f"[4] 审计日志: 新写入 {len(entries)} 条（cursor_position/wait/mouse_move/vlm_describe）")


def check_timeout() -> None:
    """动作超时：拖拽超过 timeout_s 自动中止并释放。"""
    start = time.perf_counter()
    try:
        action.mouse_drag(50, 50, 300, 300, duration_ms=600000, move_step_ms=10, timeout_s=0.05)
        raise AssertionError("应触发 ActionTimeout")
    except action.ActionTimeout as exc:
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"超时中止过慢: {elapsed:.2f}s"
        assert held_buttons() == set(), f"超时后仍有按住: {held_buttons()}"
        print(f"[5] 动作超时: {exc}（{elapsed * 1000:.0f}ms 内中止且无残留按键）")


def main() -> None:
    set_dpi_awareness()
    panic.clear_interrupt()
    root = tk.Tk()
    root.withdraw()
    try:
        check_panic_hotkey(root)
        check_watchdog()
        asyncio.run(check_audit())
        check_timeout()
        print("== 步骤8 真机验证全部通过 ==")
    finally:
        root.destroy()
        panic.stop()
        panic.clear_interrupt()


if __name__ == "__main__":
    main()
