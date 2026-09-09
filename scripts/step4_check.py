"""步骤 4 真机验证：受控文本窗口中的键盘输入、快捷键、单键长按与按键状态表。

运行方式：
    .venv/Scripts/python.exe scripts/step4_check.py

为什么不用记事本：本机记事本为单实例多标签应用，且启动时会恢复上次会话，
直接操作会把按键发到用户已有标签（调试中已实测并造成误输入）。
因此本步骤用自建受控文本窗口验证；记事本专项验收保留在步骤 11（届时配合
屏幕理解引擎的 UIA 标签控制）。
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from core.keyboard import held_keys, hotkey, key_press, release_all, type_text  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

TEST_TEXT = "你好，世界 Hello 123 abc 🌟"
TARGET_FILE = Path(tempfile.gettempdir()) / "cc_step4_verify.txt"


def find_window_by_title(title: str) -> int | None:
    """按标题枚举可见顶层窗口，返回真实 OS 句柄（Tk 的 winfo_id 不是顶层句柄）。"""
    result: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value == title:
            result.append(hwnd)
        return True

    user32.EnumWindows(callback, None)
    return result[0] if result else None


def ensure_focused(root: tk.Tk, widget: tk.Widget, hwnd: int, timeout: float = 6.0) -> None:
    """反复把自建窗口带到前台并确认 OS 前台窗口就是它；失败则中止（不冒险发送按键）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.lift()
        root.attributes("-topmost", True)
        widget.focus_force()
        widget.focus_set()
        # 附加线程输入绕过 Windows 前台锁定
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
            # 窗口已在前台，确保窗口内的输入焦点在目标控件上
            widget.focus_set()
            root.update()
            return
        time.sleep(0.2)
    raise RuntimeError(
        f"无法取得前台焦点（当前前台 {user32.GetForegroundWindow()}，目标 {hwnd}）"
    )


def pump(root: tk.Tk, duration: float) -> None:
    """持续泵 Tk 消息：窗口无常驻消息循环时，键盘事件需要反复 update 才能及时处理。"""
    end = time.time() + duration
    while time.time() < end:
        root.update()
        time.sleep(0.005)


def main() -> None:
    set_dpi_awareness()
    if TARGET_FILE.exists():
        TARGET_FILE.unlink()

    vx, vy, vw, vh = virtual_screen()
    win_w, win_h = 520, 300
    wx, wy = vx + vw - win_w - 80, vy + 80

    root = tk.Tk()
    root.title("步骤4 键盘验证")
    root.geometry(f"{win_w}x{win_h}+{wx}+{wy}")
    root.attributes("-topmost", True)
    text = tk.Text(root, font=("Microsoft YaHei", 12))
    text.pack(fill="both", expand=True)
    text.focus_set()
    root.title("步骤4 键盘验证")

    saved_ok = {"flag": False}

    def on_ctrl_s(_event=None):
        TARGET_FILE.write_text(text.get("1.0", "end-1c"), encoding="utf-8")
        saved_ok["flag"] = True
        return "break"

    text.bind("<Control-s>", on_ctrl_s)
    root.update()
    hwnd = find_window_by_title("步骤4 键盘验证")
    if hwnd is None:
        raise RuntimeError("找不到自建验证窗口")

    try:
        # 1) Unicode 文本输入（中文/英文/emoji）
        ensure_focused(root, text, hwnd)
        type_text(TEST_TEXT, interval_ms=20)
        pump(root, 0.4)
        got = text.get("1.0", "end-1c")
        print(f"[1] 文本输入: 期望 {len(TEST_TEXT)} 字符，窗口实际 {len(got)} 字符")
        assert got == TEST_TEXT, f"输入不一致:\n  期望 {TEST_TEXT!r}\n  实际 {got!r}"

        # 2) Ctrl+S 快捷键：绑定保存到临时文件
        ensure_focused(root, text, hwnd)
        hotkey(["ctrl", "s"])
        pump(root, 0.4)
        deadline = time.time() + 3
        while time.time() < deadline and not saved_ok["flag"]:
            root.update()
            time.sleep(0.05)
        assert saved_ok["flag"], "Ctrl+S 未触发保存回调"
        file_text = TARGET_FILE.read_text(encoding="utf-8")
        assert file_text == TEST_TEXT, f"保存内容不一致: {file_text!r}"
        print(f"[2] Ctrl+S 生效: 已保存 {len(file_text)} 字符到临时文件")

        # 3) 单键（End）与长按（Enter 200ms）
        ensure_focused(root, text, hwnd)
        key_press("end")
        key_press("enter", hold_ms=200)
        pump(root, 0.5)
        got2 = text.get("1.0", "end-1c")
        assert got2 == TEST_TEXT + "\n", f"长按 Enter 后内容异常: {got2!r}"
        print("[3] 单键 End + 长按 Enter: 光标移动与换行生效")

        # 4) Ctrl+S 再次保存并核对文件
        saved_ok["flag"] = False
        ensure_focused(root, text, hwnd)
        hotkey(["ctrl", "s"])
        pump(root, 0.4)
        deadline = time.time() + 3
        while time.time() < deadline and not saved_ok["flag"]:
            root.update()
            time.sleep(0.05)
        file_text2 = TARGET_FILE.read_text(encoding="utf-8")
        assert file_text2 == TEST_TEXT + "\n", f"二次保存内容不一致: {file_text2!r}"
        print("[4] Ctrl+S 二次保存: 文件已包含换行")

        # 5) 按键状态表无残留
        assert held_keys() == {}, f"操作结束后仍有按键被按住: {held_keys()}"
        print("[5] 按键状态表: 操作结束后无残留按键")
        print("== 步骤4 真机验证全部通过 ==")
    finally:
        root.destroy()
        release_all()
        if TARGET_FILE.exists():
            TARGET_FILE.unlink()


if __name__ == "__main__":
    main()
