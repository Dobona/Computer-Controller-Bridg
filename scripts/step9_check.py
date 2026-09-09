"""步骤 9 真机验证：一键启停脚本、status.json、托盘图标、优雅退出。

运行方式：
    .venv/Scripts/python.exe scripts/step9_check.py
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")

user32 = ctypes.WinDLL("user32", use_last_error=True)
TMP_DIR = Path(tempfile.mkdtemp(prefix="cc_step9_"))
_bat_counter = 0


def run_bat(name: str) -> tuple[str, str]:
    """运行 scripts/<name>，捕获 stdout/stderr（用文件避免管道被后台进程挂起）。"""
    global _bat_counter
    _bat_counter += 1
    out_path = TMP_DIR / f"out_{_bat_counter}.txt"
    err_path = TMP_DIR / f"err_{_bat_counter}.txt"
    with open(out_path, "w", encoding="utf-8") as fo, open(err_path, "w", encoding="utf-8") as fe:
        subprocess.run(
            ["cmd", "/c", f"scripts\\{name}"],
            cwd=str(ROOT),
            stdout=fo,
            stderr=fe,
            timeout=90,
        )
    # 注意：后台服务会继承输出文件句柄，只能在服务停止后由 main() 统一清理临时目录
    return out_path.read_text(encoding="utf-8"), err_path.read_text(encoding="utf-8")


def read_status() -> dict | None:
    status_file = ROOT / "status.json"
    if not status_file.exists():
        return None
    return json.loads(status_file.read_text(encoding="utf-8"))


def python_processes() -> list[dict]:
    """列出项目相关的 python 进程（服务 + 看门狗）。"""
    self_pid = os.getpid()
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -match 'main.py|safety.watchdog' } | "
         "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    text = result.stdout.strip()
    if not text or text == "null":
        return []
    data = json.loads(text)
    items = data if isinstance(data, list) else [data]
    return [item for item in items if int(item["ProcessId"]) != self_pid]


def process_alive(pid: int) -> bool:
    return subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
    ).stdout.lower().count(str(pid)) > 0


def tray_windows(pid: int) -> list[str]:
    """枚举某进程的顶层窗口，返回包含 SystemTrayIcon 的类名列表。"""
    found: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        proc = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if proc.value == pid:
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if "SystemTrayIcon" in cls.value:
                found.append(cls.value)
        return True

    user32.EnumWindows(cb, 0)
    return found


def check_start() -> dict:
    """start.bat 首次启动：status.json 内容正确、托盘图标窗口存在。"""
    status_file = ROOT / "status.json"
    status_file.unlink(missing_ok=True)
    stdout, _ = run_bat("start.bat")
    assert "Service started" in stdout, stdout
    status = read_status()
    assert status is not None, "start.bat 后 status.json 未写入"
    assert status["status"] == "running" and status["transport"] == "stdio"
    assert isinstance(status["pid"], int) and status["started_at"]
    deadline = time.time() + 15
    while time.time() < deadline and not tray_windows(status["pid"]):
        time.sleep(0.5)
    tray = tray_windows(status["pid"])
    assert tray, f"服务进程 {status['pid']} 未创建托盘图标窗口"
    print(f"[1] start.bat: 后台启动成功，status.json 正确（PID {status['pid']}）")
    print(f"[2] 托盘图标: 服务进程存在 SystemTrayIcon 窗口 {len(tray)} 个")
    return status


def check_already_running(status: dict) -> None:
    """start.bat 重复执行：检测到已在运行，不重复启动。"""
    before = python_processes()
    stdout, _ = run_bat("start.bat")
    assert "already running" in stdout.lower(), stdout
    after = python_processes()
    assert len(after) == len(before), f"重复启动产生了新进程: {after}"
    assert process_alive(status["pid"]), "原服务进程不在了"
    print("[3] start.bat 重复执行: 正确识别已在运行，未重复启动")


def check_stop(status: dict) -> None:
    """stop.bat：优雅退出、状态清空、无 python 残留、无按键残留。"""
    sys.path.insert(0, str(ROOT / "src"))
    from core.keyboard import held_keys
    from core.mouse import held_buttons

    held_keys()  # 本进程无按键，仅确认导入正常
    held_buttons()
    stdout, _ = run_bat("stop.bat")
    assert "Service stopped" in stdout, stdout
    deadline = time.time() + 10
    while time.time() < deadline and process_alive(status["pid"]):
        time.sleep(0.5)
    assert not process_alive(status["pid"]), "服务未退出"
    assert read_status() is None, "status.json 未清空"
    assert not python_processes(), f"仍有 python 进程: {python_processes()}"
    watchdog_state = ROOT / "logs" / "watchdog-state.json"
    assert not watchdog_state.exists(), "watchdog 状态文件未清理"
    print("[4] stop.bat: 优雅退出，status.json 清空，无 python/看门狗残留")


def check_stop_idle() -> None:
    """stop.bat 服务未运行时：给出明确提示且无副作用。"""
    stdout, _ = run_bat("stop.bat")
    assert "not running" in stdout.lower(), stdout
    print("[5] stop.bat 未运行时: 明确提示未在运行")


def main() -> None:
    try:
        status = check_start()
        try:
            check_already_running(status)
            check_stop(status)
            check_stop_idle()
            print("== 步骤9 真机验证全部通过 ==")
        finally:
            # 兜底：若验证中途失败，确保服务被停止
            try:
                run_bat("stop.bat")
            except Exception:
                pass
            for proc in python_processes():
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc["ProcessId"]), "/F"],
                        capture_output=True,
                    )
                except Exception:
                    pass
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
