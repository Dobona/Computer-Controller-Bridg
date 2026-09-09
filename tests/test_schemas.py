"""统一返回结构、safe_tool 与参数校验单元测试。"""

from __future__ import annotations

import pytest

from core.action import ActionInterrupted, ActionTimeout
from safety import panic
from server.schemas import (
    make_result,
    safe_tool,
    validate_button,
    validate_key_name,
    validate_keys,
    validate_ms,
    validate_region,
    validate_scope,
    validate_text,
)


@pytest.fixture(autouse=True)
def clean_panic():
    panic.clear_interrupt()
    yield
    panic.clear_interrupt()


def test_make_result_without_t0():
    r = make_result(True, "完成", {"a": 1})
    assert r == {"ok": True, "message": "完成", "data": {"a": 1}}


def test_make_result_with_t0():
    import time

    t0 = time.perf_counter()
    r = make_result(False, "失败", None, t0)
    assert r["ok"] is False
    assert r["duration_ms"] >= 0


def test_safe_tool_value_error():
    @safe_tool
    def tool():
        raise ValueError("非法参数")

    r = tool()
    assert r["ok"] is False
    assert "参数错误" in r["message"]


def test_safe_tool_interrupted():
    @safe_tool
    def tool():
        raise ActionInterrupted("被急停")

    r = tool()
    assert r["ok"] is False
    assert "被急停" in r["message"]


def test_safe_tool_timeout():
    @safe_tool
    def tool():
        raise ActionTimeout("动作超时")

    r = tool()
    assert r["ok"] is False
    assert "超时" in r["message"]


def test_safe_tool_generic_error():
    @safe_tool
    def tool():
        raise RuntimeError("boom")

    r = tool()
    assert r["ok"] is False
    assert "执行失败" in r["message"]


def test_safe_tool_panic_gate():
    called = []

    @safe_tool
    def tool():
        called.append(1)
        return make_result(True, "ok")

    panic.request_interrupt()
    r = tool()
    assert r["ok"] is False
    assert "急停状态" in r["message"]
    assert called == []


def test_safe_tool_allow_during_panic():
    @safe_tool(allow_during_panic=True)
    def tool():
        return make_result(True, "ok")

    panic.request_interrupt()
    r = tool()
    assert r["ok"] is True


def test_validate_button():
    validate_button("left")
    with pytest.raises(ValueError, match="非法按键"):
        validate_button("side")


def test_validate_key():
    validate_key_name("enter")
    with pytest.raises(ValueError, match="非法按键名"):
        validate_key_name("??")


def test_validate_keys():
    validate_keys(["ctrl", "c"])
    with pytest.raises(ValueError, match="非空列表"):
        validate_keys([])
    with pytest.raises(ValueError, match="非法按键名"):
        validate_keys(["ctrl", "??"])


def test_validate_text():
    validate_text("你好")
    with pytest.raises(ValueError, match="非空字符串"):
        validate_text("")
    with pytest.raises(ValueError, match="过长"):
        validate_text("x" * 10001)


def test_validate_ms():
    validate_ms("ms", 100)
    with pytest.raises(ValueError, match="必须"):
        validate_ms("ms", -1)
    with pytest.raises(ValueError, match="必须"):
        validate_ms("ms", 999999999)
    with pytest.raises(ValueError, match="必须"):
        validate_ms("ms", "abc")


def test_validate_scope():
    validate_scope("window")
    with pytest.raises(ValueError, match="scope"):
        validate_scope("bogus")


def test_validate_region():
    validate_region([1, 2, 3, 4])
    with pytest.raises(ValueError, match="四个整数"):
        validate_region([1, 2])
    with pytest.raises(ValueError, match="x1<x2"):
        validate_region([5, 0, 3, 4])
