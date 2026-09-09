"""pytest 公共配置：确保 tests 可以直接导入 src 下的包。"""

from __future__ import annotations

import sys
import copy
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def _disable_audit():
    """单元测试默认不写审计日志，避免污染 logs/（审计相关测试自行开启）。"""
    from safety import audit

    audit.set_enabled(False)
    yield
    audit.set_enabled(True)


@pytest.fixture(autouse=True)
def _default_config():
    """测试默认使用 DEFAULT_CONFIG，不依赖真实 config.json。"""
    from server import config

    config.set_config(copy.deepcopy(config.DEFAULT_CONFIG))
    yield
    config.set_config(None)
