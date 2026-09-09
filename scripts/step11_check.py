"""步骤 11 真机验收：记事本无视觉完整链路、右键菜单、应用内拖拽、急停、VLM、自检。

运行方式：
    .venv/Scripts/python.exe scripts/step11_check.py
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "src"))

from fastmcp import Client  # noqa: E402

from core import windows  # noqa: E402
from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from safety import panic  # noqa: E402
from server.mcp_server import create_server  # noqa: E402


def center(rect: list[int]) -> tuple[int, int]:
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


def find_window(data: dict, title_part: str) -> dict | None:
    for win in data.get("windows", []):
        if title_part in win.get("title", ""):
            return win
    return None


def find_notepad_window(data: dict) -> dict | None:
    """按 class=Notepad 或标题含 记事本/Notepad 匹配记事本窗口（标题语言可能不同）。"""
    for win in data.get("windows", []):
        title = win.get("title", "")
        if win.get("class") == "Notepad" or "记事本" in title or "Notepad" in title:
            return win
    return None


def find_element(data: dict, type_name: str | None = None, name_part: str | None = None):
    for el in data.get("elements", []):
        if type_name and el.get("type") != type_name:
            continue
        if name_part and name_part not in el.get("name", ""):
            continue
        return el
    return None


def rect_inside(rect: list[int], outer: list[int]) -> bool:
    if not rect or not outer:
        return False
    return rect[0] >= outer[0] and rect[1] >= outer[1] and rect[2] <= outer[2] and rect[3] <= outer[3]


async def analyze(client, **kwargs) -> dict:
    # 步骤 11 验证的是本地分层链路（窗口/UIA/OCR），不附加外部 VLM 调用
    kwargs.setdefault("with_vlm", False)
    res = await client.call_tool("screen_analyze", kwargs)
    assert res.data["ok"] is True, res
    return res.data["data"]


async def call(client, name: str, args: dict) -> dict:
    res = await client.call_tool(name, args)
    return res.data


async def check_notepad_chain(client) -> None:
    """无视觉完整链路：screen_analyze → 鼠标/键盘 打开记事本 → 输入中文 → Ctrl+S 保存。"""
    tmp = Path(tempfile.mkdtemp(prefix="cc_step11_"))
    target = tmp / "验收文档.txt"
    baseline = set(_notepad_pids())
    proc = subprocess.Popen(["notepad.exe", str(target)])
    try:
        # 1) 用 screen_analyze 找到记事本窗口并等待编辑区可用（不依赖截图识图）
        win = None
        edit = None
        deadline = time.time() + 25
        while time.time() < deadline:
            data = await analyze(client, scope="full", with_uia=True, with_ocr=False)
            win = find_notepad_window(data)
            if win is None:
                await asyncio.sleep(0.5)
                continue
            # 处理“找不到文件，要创建新文件吗?”对话框（点“是”）
            yes_btn = None
            for el in data.get("elements", []):
                if (
                    el.get("type") == "button"
                    and el.get("name") == "是"
                    and rect_inside(el.get("rect", []), win["rect"])
                ):
                    yes_btn = el
                    break
            if yes_btn:
                yx, yy = center(yes_btn["rect"])
                await call(client, "mouse_click", {"x": yx, "y": yy})
                await asyncio.sleep(1.0)
                continue
            for el in data.get("elements", []):
                if el.get("type") == "document" and rect_inside(el.get("rect", []), win["rect"]):
                    edit = el
                    break
            if edit is None:
                for el in data.get("elements", []):
                    if el.get("type") == "edit" and rect_inside(el.get("rect", []), win["rect"]):
                        edit = el
                        break
            if edit:
                break
            await asyncio.sleep(0.5)
        assert win, "未通过 screen_analyze 找到记事本窗口"
        print("[1] 记事本: 通过 screen_analyze 定位到窗口")
        assert edit, "未通过 UIA 找到记事本编辑区"
        ex, ey = center(edit["rect"])
        await call(client, "mouse_click", {"x": ex, "y": ey})
        print("[2] 记事本: 通过 UIA 元素定位并点击编辑区")

        # 3) 输入中文 + 英文
        text = "你好，世界 Hello 123"
        await call(client, "keyboard_type", {"text": text})
        await asyncio.sleep(0.5)
        print("[3] 键盘输入: 中文+英文输入完成")

        # 4) OCR 验证中文文本可见（scope=window）
        data = await analyze(client, scope="window", with_uia=False, with_ocr=True)
        assert any("你好" in t.get("text", "") for t in data.get("texts", [])), data
        print("[4] OCR: 窗口内识别到中文「你好」")

        # 5) Ctrl+S 保存 → 出现“另存为”对话框
        await call(client, "keyboard_hotkey", {"keys": ["ctrl", "s"]})
        await asyncio.sleep(1.0)
        data = await analyze(client, scope="full", with_uia=True, with_ocr=False)
        dialog = find_window(data, "另存为") or find_window(data, "保存为") or find_window(data, "Save As")
        if dialog:
            fname = None
            for el in data.get("elements", []):
                if el.get("type") == "edit" and rect_inside(el.get("rect", []), dialog["rect"]):
                    name = el.get("name", "")
                    if "文件名" in name or el.get("automation_id") in ("1001", "FileNameControlHost"):
                        fname = el
                        break
                    fname = fname or el
            if fname is None:
                candidates = [
                    el for el in data.get("elements", [])
                    if el.get("type") == "edit" and rect_inside(el.get("rect", []), dialog["rect"])
                ]
                if candidates:
                    fname = max(candidates, key=lambda e: (e["rect"][2] - e["rect"][0]) * (e["rect"][3] - e["rect"][1]))
            assert fname, "保存对话框中未找到文件名输入框"
            fx, fy = center(fname["rect"])
            await call(client, "mouse_click", {"x": fx, "y": fy})
            await call(client, "keyboard_hotkey", {"keys": ["ctrl", "a"]})
            await call(client, "keyboard_type", {"text": str(target)})
            save_btn = None
            data = await analyze(client, scope="full", with_uia=True, with_ocr=False)
            for el in data.get("elements", []):
                if el.get("type") == "button" and "保存" in el.get("name", "") and rect_inside(el.get("rect", []), dialog["rect"]):
                    save_btn = el
                    break
            assert save_btn, "保存对话框中未找到「保存」按钮"
            sx, sy = center(save_btn["rect"])
            await call(client, "mouse_click", {"x": sx, "y": sy})
            print("[5] Ctrl+S: 另存为对话框出现，已填路径并点击保存")
        else:
            print("[5] Ctrl+S: 未出现对话框（记事本直接保存）")

        # 6) 验证文件内容
        deadline = time.time() + 10
        while time.time() < deadline and not target.exists():
            await asyncio.sleep(0.5)
        assert target.exists(), f"保存后文件未生成: {target}"
        content = target.read_text(encoding="utf-8", errors="ignore")
        assert "你好" in content and "Hello" in content, content
        print("[6] 保存验证: 文件内容包含中文与英文")

        # 7) 另存为对话框（Ctrl+Shift+S）→ 保存到第二个路径
        target2 = tmp / "验收文档-副本.txt"
        await call(client, "keyboard_hotkey", {"keys": ["ctrl", "shift", "s"]})
        await asyncio.sleep(1.0)
        data = await analyze(client, scope="full", with_uia=True, with_ocr=False)
        dialog = find_window(data, "另存为") or find_window(data, "保存为") or find_window(data, "Save As")
        assert dialog, "Ctrl+Shift+S 后未出现另存为对话框"
        fname = None
        for el in data.get("elements", []):
            if el.get("type") == "edit" and rect_inside(el.get("rect", []), dialog["rect"]):
                name = el.get("name", "")
                if "文件名" in name or el.get("automation_id") in ("1001", "FileNameControlHost"):
                    fname = el
                    break
                fname = fname or el
        if fname is None:
            candidates = [
                el for el in data.get("elements", [])
                if el.get("type") == "edit" and rect_inside(el.get("rect", []), dialog["rect"])
            ]
            if candidates:
                fname = max(candidates, key=lambda e: (e["rect"][2] - e["rect"][0]) * (e["rect"][3] - e["rect"][1]))
        assert fname, "另存为对话框中未找到文件名输入框"
        fx, fy = center(fname["rect"])
        await call(client, "mouse_click", {"x": fx, "y": fy})
        await call(client, "keyboard_hotkey", {"keys": ["ctrl", "a"]})
        await call(client, "keyboard_type", {"text": str(target2)})
        data = await analyze(client, scope="full", with_uia=True, with_ocr=False)
        dialog2 = find_window(data, "另存为") or find_window(data, "保存为") or find_window(data, "Save As")
        save_btn = None
        for el in data.get("elements", []):
            if el.get("type") == "button" and "保存" in el.get("name", "") and rect_inside(el.get("rect", []), dialog2["rect"]):
                save_btn = el
                break
        assert save_btn, "另存为对话框中未找到「保存」按钮"
        sx, sy = center(save_btn["rect"])
        await call(client, "mouse_click", {"x": sx, "y": sy})
        deadline = time.time() + 10
        while time.time() < deadline and not target2.exists():
            await asyncio.sleep(0.5)
        assert target2.exists(), f"另存为后副本未生成: {target2}"
        content2 = target2.read_text(encoding="utf-8", errors="ignore")
        assert "你好" in content2, content2
        print("[7] 另存为: 对话框出现，保存副本成功且内容一致")

        # 8) 关闭记事本（Alt+F4）
        await call(client, "keyboard_hotkey", {"keys": ["alt", "f4"]})
        deadline = time.time() + 10
        while time.time() < deadline and _notepad_window_exists():
            await asyncio.sleep(0.5)
        assert not _notepad_window_exists(), "Alt+F4 后记事本窗口仍在"
        print("[8] 关闭记事本: Alt+F4 生效，窗口已关闭")
    finally:
        # 关闭测试打开的记事本：Alt+F4 已尝试；兜底对新增的 notepad 进程发送 WM_CLOSE / 强杀
        for pid in set(_notepad_pids()) - baseline:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def _notepad_pids() -> list[int]:
    """当前 notepad 进程 PID 列表。"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process notepad -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    pids = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _notepad_window_exists() -> bool:
    """是否存在 Notepad 类窗口。"""
    sys.path.insert(0, str(ROOT / "src"))
    from core import windows

    wins = windows.list_windows(with_title_only=False, visible_only=True)
    return any(w.get("class") == "Notepad" for w in wins)


async def check_right_click_menu(client) -> None:
    """右键菜单：桌面空白处右键 → 出现弹出菜单（#32768 或 WinUI PopupWindowSiteBridge）→ Esc 关闭。"""
    vx, vy, vw, vh = virtual_screen()
    x, y = vx + vw - 560, vy + vh - 240  # 桌面右下空白区域
    await call(client, "mouse_right_click", {"x": x, "y": y})
    menu_found = False
    deadline = time.time() + 5
    while time.time() < deadline:
        wins = windows.list_windows(with_title_only=False, visible_only=True)
        if any(
            w.get("class") in ("#32768", "DV2ControlHost")
            or w.get("class") == "Microsoft.UI.Content.PopupWindowSiteBridge"
            for w in wins
        ):
            menu_found = True
            break
        await asyncio.sleep(0.3)
    assert menu_found, "右键后未检测到弹出菜单窗口（点：{x},{y}）"
    await call(client, "keyboard_key", {"key": "esc"})
    print("[9] 右键菜单: 桌面右键出现弹出菜单，Esc 关闭")


async def check_emergency_stop(client, root: tk.Tk) -> None:
    """急停：按住鼠标时调用 emergency_stop → 立即释放并拒绝新指令 → 复位后恢复。"""
    vx, vy, vw, vh = virtual_screen()
    win = tk.Toplevel(root)
    win.title("步骤11 急停测试")
    win.geometry(f"300x160+{vx + vw - 380}+{vy + 80}")
    win.attributes("-topmost", True)
    btn = tk.Button(win, text="急停测试", font=("Microsoft YaHei", 14))
    btn.pack(fill="both", expand=True)
    events: list[str] = []
    btn.bind("<ButtonPress-1>", lambda e: events.append("down"))
    btn.bind("<ButtonRelease-1>", lambda e: events.append("up"))
    for _ in range(5):
        win.update()
        time.sleep(0.05)
    bx, by = btn.winfo_rootx() + btn.winfo_width() // 2, btn.winfo_rooty() + btn.winfo_height() // 2
    panic.clear_interrupt()
    await call(client, "mouse_down", {"button": "left", "x": bx, "y": by})
    win.update()
    time.sleep(0.3)
    assert events == ["down"], events
    await call(client, "emergency_stop", {})
    deadline = time.time() + 2
    while time.time() < deadline and "up" not in events:
        win.update()
        time.sleep(0.05)
    assert panic.interrupted(), "emergency_stop 未置位急停"
    assert events == ["down", "up"], f"急停后未释放: {events}"
    res = await call(client, "cursor_position", {})
    assert res["ok"] is False and "急停状态" in res["message"], res
    print("[10] 急停: emergency_stop 立即释放鼠标按钮并拒绝新指令")
    panic.clear_interrupt()  # 等价于再次按下急停热键复位
    res = await call(client, "cursor_position", {})
    assert res["ok"] is True, res
    print("[11] 急停复位: 清除标志后恢复接受指令")
    win.destroy()


async def check_drag(client, root: tk.Tk) -> None:
    """应用内拖拽：绑定 B1-Motion 处理器，拖动自建窗口按预期位移（标题栏拖拽在本机受限）。"""
    from core import action

    vx, vy, vw, vh = virtual_screen()
    win = tk.Toplevel(root)
    win.title("步骤11 拖拽测试")
    win.geometry(f"260x140+{vx + 200}+{vy + 200}")
    lbl = tk.Label(win, text="拖动此区域", font=("Microsoft YaHei", 16), bg="#d0e8ff")
    lbl.pack(fill="both", expand=True)
    anchor: list[tuple[int, int] | None] = [None]

    def on_press(event):
        anchor[0] = (event.x_root - win.winfo_x(), event.y_root - win.winfo_y())

    def on_motion(event):
        if anchor[0] is not None:
            dx0, dy0 = anchor[0]
            win.geometry(f"+{event.x_root - dx0}+{event.y_root - dy0}")

    lbl.bind("<ButtonPress-1>", on_press)
    lbl.bind("<B1-Motion>", on_motion)
    for _ in range(5):
        win.update()
        time.sleep(0.05)
    start_x, start_y = win.winfo_rootx(), win.winfo_rooty()
    fx = start_x + lbl.winfo_width() // 2 + 20
    fy = start_y + lbl.winfo_height() // 2 + 10
    dx, dy = 160, 90
    t = threading.Thread(
        target=lambda: action.mouse_drag(
            fx, fy, fx + dx, fy + dy, duration_ms=500, move_step_ms=10
        ),
        daemon=True,
    )
    t.start()
    deadline = time.time() + 3
    while time.time() < deadline and t.is_alive():
        win.update()
        time.sleep(0.01)
    t.join(timeout=5)
    for _ in range(10):
        win.update()
        time.sleep(0.02)
    moved = (win.winfo_rootx(), win.winfo_rooty()) != (start_x, start_y)
    assert moved, "应用内拖拽未使窗口位移"
    assert abs((win.winfo_rootx() - start_x) - dx) <= 3, (start_x, win.winfo_rootx())
    assert abs((win.winfo_rooty() - start_y) - dy) <= 3, (start_y, win.winfo_rooty())
    print("[12] 应用内拖拽: 窗口精确位移 (160, 90)")
    win.destroy()


async def check_vlm_and_self_test(client) -> None:
    # VLM 已配置启用（真实调用由步骤 13 验证）；此处临时关闭验证“未配置”提示，不消耗 API
    from server import config as config_mod

    cfg = config_mod.get_config()
    saved = dict(cfg["screen"]["vlm"])
    cfg["screen"]["vlm"]["enabled"] = False
    try:
        res = await call(client, "vlm_describe", {})
        assert res["ok"] is False and "未配置 VLM" in res["message"], res
        print("[13] vlm_describe: 未配置时返回明确提示")
    finally:
        cfg["screen"]["vlm"].update(saved)
    res = await call(client, "self_test", {"dry_run": True})
    assert res["ok"] is True, res
    print("[14] self_test: dry-run 自检通过")


def check_multiscreen_note() -> None:
    """多屏：本机单屏；双屏坐标换算已由单元测试（负坐标/DPI 档位）覆盖。"""
    vx, vy, vw, vh = virtual_screen()
    print(f"[15] 多屏: 本机单显示器 {vw}x{vh}，双屏换算由 test_coords.py 单测覆盖（本机无副屏）")


async def run_all(root: tk.Tk) -> None:
    server = create_server()
    async with Client(server) as client:
        await check_notepad_chain(client)
        await check_right_click_menu(client)
        await check_emergency_stop(client, root)
        await check_drag(client, root)
        await check_vlm_and_self_test(client)
        check_multiscreen_note()
        print("== 步骤11 真机验收全部通过 ==")


def main() -> None:
    set_dpi_awareness()
    panic.clear_interrupt()
    root = tk.Tk()
    root.withdraw()
    try:
        asyncio.run(run_all(root))
    finally:
        root.destroy()
        panic.clear_interrupt()


if __name__ == "__main__":
    main()
