"""看门狗：主进程被强杀后自动释放所有按键与鼠标按钮。

设计：
- StateWriter：主进程内的守护线程，周期性把当前按住状态写入 state 文件（也是心跳）；
- watchdog 子进程（python -m safety.watchdog --watch <pid> --state <file>）：
  等待主进程退出后读取 state 文件并发送 KEYUP / 鼠标松开，防止按键残留；
- 主进程正常退出时调用 StateWriter.stop() 删除 state 文件，子进程无需释放。

多实例安全：每个服务实例使用独立状态文件（见 main.py 按 PID 命名），
避免多个 stdio 实例互相覆盖状态导致看门狗误释放；看门狗释放后删除自己的状态文件。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

PROCESS_SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0x00000000


def current_state() -> dict:
    """当前进程的按住状态（供写入 state 文件）。"""
    from core.keyboard import held_keys
    from core.mouse import held_buttons

    return {
        "pid": os.getpid(),
        "held_keys": sorted(held_keys().values()),
        "held_buttons": sorted(held_buttons()),
        "ts": time.time(),
    }


def write_state(state_file, state: dict | None = None) -> None:
    """把状态写入文件。"""
    data = state if state is not None else current_state()
    path = Path(state_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_state(state_file) -> dict | None:
    """读取状态文件，不存在/损坏时返回 None。"""
    path = Path(state_file)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


class StateWriter(threading.Thread):
    """周期性写入按住状态的守护线程（心跳 + 崩溃恢复依据）。"""

    def __init__(self, state_file, interval_ms: int = 500):
        super().__init__(daemon=True, name="watchdog-state")
        self.state_file = Path(state_file)
        self.interval_ms = interval_ms
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                write_state(self.state_file)
            except Exception:
                pass
            self._stop_event.wait(self.interval_ms / 1000)

    def stop(self) -> None:
        """停止写入并删除状态文件（正常退出时调用，子进程无需释放）。"""
        self._stop_event.set()
        self.join(timeout=2)
        try:
            self.state_file.unlink()
        except FileNotFoundError:
            pass


def release_state(state: dict) -> dict:
    """按状态文件释放按键与鼠标按钮，返回释放结果。"""
    from core.keyboard import key_up
    from core.mouse import button_up

    released_keys: list[str] = []
    released_buttons: list[str] = []
    for name in state.get("held_keys", []):
        try:
            key_up(name)
            released_keys.append(name)
        except Exception:
            continue
    for button in state.get("held_buttons", []):
        try:
            button_up(button)
            released_buttons.append(button)
        except Exception:
            continue
    return {"released_keys": released_keys, "released_buttons": released_buttons}


def spawn(state_file, main_pid: int | None = None) -> subprocess.Popen:
    """启动看门狗子进程，监控 main_pid（默认当前进程）。"""
    pid = main_pid or os.getpid()
    src_dir = Path(__file__).resolve().parents[1]
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "safety.watchdog",
            "--watch",
            str(pid),
            "--state",
            str(Path(state_file).resolve()),
        ],
        cwd=str(src_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _watch_and_release(main_pid: int, state_file: Path) -> dict:
    """等待主进程退出，然后按其状态释放按键。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_SYNCHRONIZE, False, main_pid)
    if handle:
        try:
            kernel32.WaitForSingleObject(handle, INFINITE)
        finally:
            kernel32.CloseHandle(handle)
    state = read_state(state_file)
    if state:
        return release_state(state)
    return {"released_keys": [], "released_buttons": []}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="computer-controller 看门狗子进程")
    parser.add_argument("--watch", type=int, required=True, help="被监控的主进程 PID")
    parser.add_argument("--state", required=True, help="按住状态文件路径")
    args = parser.parse_args(argv)
    result = _watch_and_release(args.watch, Path(args.state))
    marker = Path(args.state).with_suffix(".released")
    marker.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    try:
        Path(args.state).unlink(missing_ok=True)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
