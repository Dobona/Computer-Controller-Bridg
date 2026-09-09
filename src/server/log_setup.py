"""运行日志初始化（可复用）。

main.py 的 stdio 服务入口与进程内客户端（方式 B / 单元测试）共用此模块：
ensure_logging 只在根 logger 尚无处理器时初始化一次，避免重复添加；
文件日志写入 logs/server.log（按天轮转、保留 14 份）；
测试环境（pytest 运行中）不初始化文件日志，避免污染 logs/；
stdio 模式下 stdout 必须保持干净，因此只写文件与 stderr。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_LOG = ROOT / "logs" / "server.log"


def _in_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def ensure_logging(level: str = "info") -> bool:
    """确保根 logger 已配置。已配置时直接返回 False（未重复初始化）。

    返回 True 表示本次完成了初始化；False 表示此前已初始化或处于测试环境跳过。
    """
    root = logging.getLogger()
    if root.handlers or _in_pytest():
        return False
    try:
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            SERVER_LOG, when="midnight", backupCount=14, encoding="utf-8"
        )
    except Exception as exc:
        file_handler = None
        sys.stderr.write(f"[WARN] 无法写入日志文件 {SERVER_LOG}: {exc}\n")
    handlers: list[logging.Handler] = []
    if file_handler is not None:
        handlers.append(file_handler)
    handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return True
