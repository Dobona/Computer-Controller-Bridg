"""MCP 服务入口：启动 stdio 传输的 computer-controller 服务。

职责：自检 → 启动安全层（急停热键/看门狗）→ 写入 status.json →
启动托盘与停止请求监控 → 运行 MCP stdio 服务；退出时统一收尾。
"""

from __future__ import annotations

import atexit
import datetime
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server.mcp_server import create_server  # noqa: E402
from server.log_setup import ensure_logging  # noqa: E402
from safety import panic, watchdog  # noqa: E402
from server import config as config_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
STATUS_FILE = ROOT / "status.json"
STOP_REQUEST = LOGS_DIR / "stop.request"
SERVER_LOG = LOGS_DIR / "server.log"

_started_at = datetime.datetime.now().astimezone()
_writer = None
_stop_monitor = None


def _setup_logging(level: str = "info") -> None:
    """运行日志：写入 logs/server.log（按天轮转），同时输出到 stderr（stdout 留给 MCP stdio）。"""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_logging(level=level)


def _self_check() -> bool:
    """启动自检：输入引擎可导入、截图可用。失败不阻断启动，仅记录。"""
    ok = True
    try:
        from core import screen

        screen.grab()
    except Exception as exc:
        ok = False
        logging.warning("启动自检：截图不可用: %s", exc)
    try:
        from core import keyboard, mouse

        keyboard.held_keys()
        mouse.held_buttons()
    except Exception as exc:
        ok = False
        logging.warning("启动自检：输入引擎不可用: %s", exc)
    # 预热并记录本地屏幕理解引擎（OCR / UIA）可用性，降级原因写入日志便于排查
    try:
        from core import ocr, uia

        uia_ok = uia.available()
        logging.info(
            "启动自检：UIA %s%s",
            "可用" if uia_ok else "不可用",
            f"（原因：{uia.last_error()}）" if not uia_ok and uia.last_error() else "",
        )
        ocr_ok = ocr.available()
        logging.info(
            "启动自检：OCR %s%s",
            "可用" if ocr_ok else "不可用",
            f"（原因：{ocr.last_error()}）" if not ocr_ok and ocr.last_error() else "",
        )
    except Exception as exc:
        ok = False
        logging.warning("启动自检：屏幕理解引擎初始化异常: %s", exc)
    return ok


def _write_status() -> None:
    """写入 status.json（供 start.bat / stop.bat / 托盘读取）。"""
    try:
        STATUS_FILE.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": _started_at.isoformat(timespec="seconds"),
                    "status": "running",
                    "transport": "stdio",
                    "port": None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clear_status() -> None:
    try:
        STATUS_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _cleanup() -> None:
    """统一收尾：释放按键/鼠标、停止热键/看门狗/托盘、清空状态文件。"""
    try:
        from core.keyboard import release_all
        from core.mouse import release_all_buttons

        release_all()
        release_all_buttons()
    except Exception:
        pass
    try:
        panic.stop()
    except Exception:
        pass
    if _writer is not None:
        try:
            _writer.stop()
        except Exception:
            pass
    try:
        from ui import tray

        tray.stop()
    except Exception:
        pass
    _clear_status()
    try:
        STOP_REQUEST.unlink(missing_ok=True)
    except Exception:
        pass
    logging.info("服务已退出（PID %s）", os.getpid())


def _cleanup_and_exit() -> None:
    """托盘“立即停止”/stop.bat 回调：收尾后立即退出（不等待 stdio 循环返回）。"""
    _cleanup()
    os._exit(0)


def _start_safety():
    """启动急停热键、按住状态写入与看门狗子进程（best-effort，失败不影响服务）。"""
    global _writer
    try:
        hotkey = tuple(config_mod.get_config()["safety"]["panic_hotkey"])
        panic.start(hotkey)
        logging.info("急停热键监听已启动（组合: %s）", "+".join(hotkey))
    except Exception:
        pass
    # 每个服务实例使用独立的状态文件，避免多个 stdio 实例互相覆盖导致看门狗误释放
    state_file = LOGS_DIR / f"watchdog-state-{os.getpid()}.json"
    _writer = watchdog.StateWriter(state_file)
    _writer.start()
    try:
        watchdog.spawn(state_file)
    except Exception:
        pass
    return _writer


def _start_tray() -> None:
    """启动托盘图标（绿色 = 运行中）。失败仅记录，不影响服务。"""
    try:
        from ui import tray

        def status_provider() -> str:
            uptime = datetime.datetime.now().astimezone() - _started_at
            minutes, seconds = divmod(int(uptime.total_seconds()), 60)
            return (
                f"运行状态：运行中（PID {os.getpid()}）\n"
                f"启动时间：{_started_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"已运行：{minutes} 分 {seconds} 秒\n"
                "传输方式：stdio（本机 Codex 接入）\n"
                "急停热键：Ctrl+Alt+Shift+Esc（再按一次复位）"
            )

        tray.start(status_provider=status_provider, stop_callback=_cleanup_and_exit)
    except Exception as exc:
        logging.warning("托盘启动失败: %s", exc)


def _monitor_stop_request() -> None:
    """监控 stop.bat 写入的停止请求文件；收到后优雅退出。"""
    while True:
        try:
            if STOP_REQUEST.exists():
                STOP_REQUEST.unlink(missing_ok=True)
                logging.info("收到停止请求（stop.bat），正在退出…")
                _cleanup_and_exit()
        except Exception:
            pass
        time.sleep(0.2)


def main() -> None:
    try:
        cfg = config_mod.load_config()
    except config_mod.ConfigError as exc:
        sys.stderr.write(f"[FATAL] {exc}\n")
        sys.exit(1)
    config_mod.set_config(cfg)
    _setup_logging(cfg["log"]["level"])
    from safety import audit

    audit.set_enabled(bool(cfg["log"]["audit"]))
    logging.info("服务启动中（PID %s）…", os.getpid())
    self_check_ok = _self_check()
    _start_safety()
    _write_status()
    atexit.register(_cleanup)
    _start_tray()
    global _stop_monitor
    _stop_monitor = threading.Thread(
        target=_monitor_stop_request, daemon=True, name="stop-monitor"
    )
    _stop_monitor.start()
    logging.info(
        "服务启动完成（自检 %s，stdio 模式，status.json 已写入）",
        "通过" if self_check_ok else "警告",
    )
    mcp = create_server()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
