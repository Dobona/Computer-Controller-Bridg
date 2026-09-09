"""审计日志：每个工具动作写入 JSONL（时间、工具、参数、结果、耗时）。

默认目录 logs/audit-YYYYMMDD.jsonl；测试可用 set_log_dir 指向临时目录。
审计失败不影响业务（best-effort）。
"""

from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path

_LOG_DIR = Path("logs")
_lock = threading.Lock()
_enabled = True
_MAX_FIELD_LEN = 2000


def set_log_dir(path) -> None:
    """设置审计日志目录（测试用）。"""
    global _LOG_DIR
    _LOG_DIR = Path(path)


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def _truncate(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) > _MAX_FIELD_LEN:
        text = text[:_MAX_FIELD_LEN] + "...(truncated)"
    return text


def audit_log(tool: str, params, result) -> None:
    """记录一次工具调用。result 可以是统一返回 dict 或任意值（如图片对象）。"""
    if not _enabled:
        return
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / f"audit-{datetime.date.today():%Y%m%d}.jsonl"
        if isinstance(result, dict):
            ok = result.get("ok")
            message = result.get("message")
            data = result.get("data")
            duration_ms = result.get("duration_ms")
        else:
            ok = None
            message = None
            data = {"type": type(result).__name__}
            duration_ms = None
        entry = {
            "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "tool": tool,
            "params": _truncate(params),
            "ok": ok,
            "message": message,
            "data": _truncate(data),
            "duration_ms": duration_ms,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
