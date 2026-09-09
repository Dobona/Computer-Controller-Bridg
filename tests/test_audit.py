"""审计日志单元测试。"""

from __future__ import annotations

import json

import pytest

from safety import audit


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    audit.set_enabled(True)
    audit.set_log_dir(tmp_path)
    yield tmp_path
    audit.set_enabled(False)


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_audit_log_writes_entry(audit_dir):
    audit.audit_log(
        "mouse_move",
        {"x": 10, "y": 20},
        {"ok": True, "message": "移动完成", "data": {"x": 10}, "duration_ms": 5},
    )
    files = list(audit_dir.glob("audit-*.jsonl"))
    assert len(files) == 1
    entry = _read_lines(files[0])[0]
    assert entry["tool"] == "mouse_move"
    assert entry["ok"] is True
    assert entry["params"] == '{"x": 10, "y": 20}'
    assert entry["duration_ms"] == 5
    assert "ts" in entry


def test_audit_log_non_dict_result(audit_dir):
    audit.audit_log("screenshot", {}, object())
    files = list(audit_dir.glob("audit-*.jsonl"))
    entry = _read_lines(files[0])[0]
    assert entry["ok"] is None
    assert "type" in json.loads(entry["data"])


def test_audit_log_truncates_long_params(audit_dir):
    audit.audit_log("keyboard_type", {"text": "x" * 5000}, {"ok": True})
    files = list(audit_dir.glob("audit-*.jsonl"))
    entry = _read_lines(files[0])[0]
    assert entry["params"].endswith("...(truncated)")
    assert len(entry["params"]) <= 2000 + len("...(truncated)")


def test_audit_disabled(audit_dir):
    audit.set_enabled(False)
    audit.audit_log("wait", {"ms": 10}, {"ok": True})
    assert list(audit_dir.glob("audit-*.jsonl")) == []
