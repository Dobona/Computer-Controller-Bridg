"""MCP 服务层测试：工具发现、急停、非法参数、自检（mock 引擎避免真实输入）。"""

from __future__ import annotations

import asyncio
import copy

import pytest
from fastmcp import Client

from PIL import Image

from core import ocr, screen, uia, windows
from safety import panic
from server import config as config_mod
from server import mcp_server

EXPECTED_TOOLS = {
    "cursor_position",
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_right_click",
    "mouse_down",
    "mouse_up",
    "mouse_scroll",
    "mouse_long_press",
    "mouse_drag",
    "keyboard_type",
    "keyboard_hotkey",
    "keyboard_key",
    "wait",
    "emergency_stop",
    "self_test",
    "screenshot",
    "screen_info",
    "screen_analyze",
    "vlm_describe",
    "find_text",
}


@pytest.fixture(autouse=True)
def clean_panic():
    panic.clear_interrupt()
    yield
    panic.clear_interrupt()


@pytest.fixture(autouse=True)
def _clear_vlm_cache():
    """清空 VLM 短时缓存，避免测试间相互污染。"""
    mcp_server._vlm_cache.clear()
    yield
    mcp_server._vlm_cache.clear()


def _call(name: str, args: dict):
    async def run():
        server = mcp_server.create_server()
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return asyncio.run(run())


def test_discover_all_tools():
    async def run():
        server = mcp_server.create_server()
        async with Client(server) as client:
            tools = await client.list_tools()
            return {t.name for t in tools}

    names = asyncio.run(run())
    assert EXPECTED_TOOLS <= names
    assert len(names) == len(EXPECTED_TOOLS)


def test_invalid_params_return_clear_errors():
    cases = [
        ("mouse_move", {"x": 999999, "y": 0}, "参数错误"),
        ("mouse_click", {"button": "side"}, "参数错误"),
        ("keyboard_key", {"key": "??"}, "参数错误"),
        ("keyboard_type", {"text": ""}, "参数错误"),
        ("wait", {"ms": -1}, "参数错误"),
        ("screen_analyze", {"scope": "bogus"}, "参数错误"),
        ("vlm_describe", {"region": [1, 2]}, "参数错误"),
        ("mouse_scroll", {"delta": 99999999}, "参数错误"),
    ]
    for name, args, expect in cases:
        res = _call(name, args)
        assert res.data["ok"] is False, f"{name} 应失败"
        assert expect in res.data["message"], f"{name} 错误信息不符: {res.data['message']}"


def test_emergency_stop_releases_and_blocks(monkeypatch):
    monkeypatch.setattr(mcp_server, "release_all", lambda: ["ctrl", "shift"])
    monkeypatch.setattr(mcp_server, "release_all_buttons", lambda: ["left"])
    monkeypatch.setattr(mcp_server, "move_to", lambda x, y, duration_ms=0: None)
    monkeypatch.setattr(mcp_server, "virtual_screen", lambda: (0, 0, 1920, 1080))

    res = _call("emergency_stop", {})
    assert res.data["ok"] is True
    assert res.data["data"]["released_keys"] == ["ctrl", "shift"]
    assert res.data["data"]["released_buttons"] == ["left"]
    assert panic.interrupted() is True

    # 急停后普通工具被拒绝
    res2 = _call("cursor_position", {})
    assert res2.data["ok"] is False
    assert "急停状态" in res2.data["message"]

    # emergency_stop 自身仍可调用（幂等）
    res3 = _call("emergency_stop", {})
    assert res3.data["ok"] is True


def test_self_test_dry_run(monkeypatch):
    monkeypatch.setattr("core.uia.available", lambda: True)
    monkeypatch.setattr("core.ocr.available", lambda: True)
    monkeypatch.setattr("core.screen.grab", lambda: object())
    res = _call("self_test", {"dry_run": True})
    assert res.data["ok"] is True
    assert res.data["data"]["screenshot"] is True
    assert res.data["data"]["uia"] is True
    assert "uia_reason" in res.data["data"]
    assert "ocr_reason" in res.data["data"]
    assert "panic_hotkey" in res.data["data"]
    assert "real_actions" not in res.data["data"]


def test_wait_interrupted():
    panic.request_interrupt()
    res = _call("wait", {"ms": 100})
    assert res.data["ok"] is False


def test_vlm_not_configured():
    res = _call("vlm_describe", {})
    assert res.data["ok"] is False
    assert "未配置 VLM" in res.data["message"]


def test_vlm_describe_structured_unconfigured():
    res = _call("vlm_describe", {"structured": True})
    assert res.data["ok"] is False
    assert "未配置 VLM" in res.data["message"]


def test_screen_analyze_with_vlm_layer(monkeypatch):
    """screen_analyze with_vlm=true 时附加 VLM 描述层（mock 掉真实调用）。"""
    monkeypatch.setattr(mcp_server, "_vlm_describe", lambda region, prompt, structured: {
        "ok": True,
        "text": "屏幕上有一个记事本窗口",
        "objects": [],
    })
    res = _call("screen_analyze", {"with_vlm": True})
    assert res.data["ok"] is True
    assert res.data["data"]["vlm"]["ok"] is True
    assert "记事本" in res.data["data"]["vlm"]["text"]


def test_screen_analyze_without_vlm_layer():
    """默认（配置未启用 VLM）时 screen_analyze 不附加 VLM 层。"""
    res = _call("screen_analyze", {})
    assert res.data["ok"] is True
    assert "vlm" not in res.data["data"]


def test_screen_analyze_no_default_vlm_even_if_config_enabled(monkeypatch):
    """优化：即使配置 screen.vlm.enabled=true，screen_analyze 缺省也不再自动触发 VLM。"""
    cfg = copy.deepcopy(config_mod.DEFAULT_CONFIG)
    cfg["screen"]["vlm"]["enabled"] = True
    config_mod.set_config(cfg)

    def boom(*args, **kwargs):
        raise AssertionError("缺省不应触发 VLM 调用")

    monkeypatch.setattr(mcp_server, "_vlm_describe", boom)
    res = _call("screen_analyze", {})
    assert res.data["ok"] is True
    assert "vlm" not in res.data["data"]


def test_vlm_describe_annotates_coordinates(monkeypatch):
    """结构化返回标注绝对物理像素坐标；描述模式标注区域相对坐标。"""

    monkeypatch.setattr(
        mcp_server,
        "_vlm_describe",
        lambda region, prompt, structured: {
            "ok": True,
            "text": "{}" if structured else "屏幕上有一个按钮",
            "objects": [{"name": "发送按钮", "box": [1, 2, 3, 4]}] if structured else [],
        },
    )
    res = _call("vlm_describe", {"structured": True})
    data = res.data["data"]
    assert data["coords"]["space"] == "absolute"
    assert data["coords"]["unit"] == "physical_pixels"

    res2 = _call("vlm_describe", {"structured": False})
    data2 = res2.data["data"]
    assert data2["coords"]["space"] == "region_relative"
    assert "不可直接用于鼠标工具" in data2["coords"]["note"]


def test_vlm_describe_cache(monkeypatch):
    """同一 region+prompt 在 TTL 内只调用一次外部 VLM。"""
    calls = {"n": 0}

    def fake(region, prompt, structured):
        calls["n"] += 1
        return {"ok": True, "text": "结果", "objects": []}

    monkeypatch.setattr(mcp_server, "_vlm_describe", fake)
    _call("vlm_describe", {"region": [0, 0, 100, 100], "prompt": "定位按钮", "structured": True})
    _call("vlm_describe", {"region": [0, 0, 100, 100], "prompt": "定位按钮", "structured": True})
    _call("vlm_describe", {"region": [0, 0, 100, 100], "prompt": "定位输入框", "structured": True})
    assert calls["n"] == 2  # 前两次命中缓存，第三次 prompt 不同重新调用


def test_vlm_cache_sweep_expired():
    """过期的缓存条目会被清理，未过期条目保留。"""
    mcp_server._vlm_cache.clear()
    try:
        now = 1000.0
        mcp_server._vlm_cache[("old",)] = (now - 10, {"ok": True})
        mcp_server._vlm_cache[("fresh",)] = (now - 1, {"ok": True})
        with mcp_server._vlm_cache_lock:
            mcp_server._vlm_cache_sweep(now, ttl=5)
        assert set(mcp_server._vlm_cache) == {("fresh",)}
    finally:
        mcp_server._vlm_cache.clear()


def test_vlm_cache_sweep_caps_capacity():
    """缓存超过容量上限时淘汰最旧条目，而不是无限增长。"""
    mcp_server._vlm_cache.clear()
    try:
        now = 10_000.0
        with mcp_server._vlm_cache_lock:
            for i in range(mcp_server._VLM_CACHE_MAX_ENTRIES + 20):
                mcp_server._vlm_cache[(f"k{i}",)] = (now - i, {"ok": True})
            mcp_server._vlm_cache_sweep(now=now, ttl=3600)
        assert len(mcp_server._vlm_cache) <= mcp_server._VLM_CACHE_MAX_ENTRIES
        # 最旧的被淘汰，最新写入的保留
        assert ("k0",) in mcp_server._vlm_cache
        assert (f"k{mcp_server._VLM_CACHE_MAX_ENTRIES + 19}",) not in mcp_server._vlm_cache
    finally:
        mcp_server._vlm_cache.clear()


def test_vlm_cache_sweep_clears_when_disabled():
    """TTL<=0（缓存关闭）时旧配置残留的条目被清空。"""
    mcp_server._vlm_cache.clear()
    try:
        mcp_server._vlm_cache[("x",)] = (100.0, {"ok": True})
        with mcp_server._vlm_cache_lock:
            mcp_server._vlm_cache_sweep(now=200.0, ttl=0)
        assert mcp_server._vlm_cache == {}
    finally:
        mcp_server._vlm_cache.clear()


def _patch_screen(monkeypatch):
    monkeypatch.setattr(
        screen, "grab", lambda: Image.new("RGB", (2560, 1440), "white")
    )
    monkeypatch.setattr(
        screen, "grab_region", lambda r: Image.new("RGB", (r[2] - r[0], r[3] - r[1]), "white")
    )


def test_find_text_via_ocr(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(
        ocr,
        "ocr_image",
        lambda img, offset_x=0, offset_y=0: {
            "ok": True,
            "texts": [
                {"text": "确定", "rect": [100, 100, 200, 130], "score": 0.99},
                {"text": "目标会话", "rect": [50 + offset_x, 50 + offset_y, 300 + offset_x, 90 + offset_y], "score": 0.98},
            ],
        },
    )
    called = {"vlm": False}

    def boom(*a, **k):
        called["vlm"] = True
        raise AssertionError("OCR 命中后不应调用 VLM")

    monkeypatch.setattr(mcp_server, "_vlm_cached", boom)
    res = _call("find_text", {"text": "目标会话", "region": [10, 10, 500, 400]})
    data = res.data["data"]
    assert res.data["ok"] is True
    assert data["sources"] == ["ocr"]
    assert len(data["matches"]) == 1
    assert data["matches"][0]["rect"] == [60, 60, 310, 100]  # region 偏移叠加
    assert called["vlm"] is False


def test_find_text_via_uia(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(ocr, "ocr_image", lambda img, offset_x=0, offset_y=0: {"ok": True, "texts": []})
    monkeypatch.setattr(
        windows,
        "list_windows",
        lambda *a, **k: [{"hwnd": 1, "title": "QQ", "rect": [0, 0, 1280, 1368]}],
    )
    monkeypatch.setattr(
        uia,
        "get_elements",
        lambda scope_hwnd=None, max_elements=200: {
            "ok": True,
            "elements": [{"name": "发送按钮", "type": "button", "rect": [833, 1296, 974, 1343]}],
        },
    )
    monkeypatch.setattr(mcp_server, "_vlm_cached", lambda *a, **k: (_ for _ in ()).throw(AssertionError("UIA 命中后不应调用 VLM")))
    res = _call("find_text", {"text": "发送按钮"})
    data = res.data["data"]
    assert data["sources"] == ["uia"]
    assert data["matches"][0]["source"] == "uia"
    assert data["matches"][0]["rect"] == [833, 1296, 974, 1343]


def test_find_text_via_vlm_fallback(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(ocr, "ocr_image", lambda img, offset_x=0, offset_y=0: {"ok": True, "texts": []})
    monkeypatch.setattr(windows, "list_windows", lambda *a, **k: [])
    monkeypatch.setattr(
        mcp_server,
        "_vlm_cached",
        lambda region, prompt, structured: {
            "ok": True,
            "objects": [{"name": "目标会话", "box": [86, 152, 410, 226]}],
        },
    )
    res = _call("find_text", {"text": "目标会话", "with_vlm": True})
    data = res.data["data"]
    assert data["sources"] == ["vlm"]
    assert data["matches"][0]["source"] == "vlm"
    assert data["matches"][0]["rect"] == [86, 152, 410, 226]


def test_find_text_not_found_and_no_vlm(monkeypatch):
    _patch_screen(monkeypatch)
    monkeypatch.setattr(ocr, "ocr_image", lambda img, offset_x=0, offset_y=0: {"ok": True, "texts": []})
    monkeypatch.setattr(windows, "list_windows", lambda *a, **k: [])
    monkeypatch.setattr(mcp_server, "_vlm_cached", lambda *a, **k: (_ for _ in ()).throw(AssertionError("with_vlm=false 不应调用 VLM")))
    res = _call("find_text", {"text": "不存在的文本", "with_vlm": False})
    assert res.data["ok"] is True
    assert res.data["data"]["matches"] == []
    assert "未找到" in res.data["message"]
