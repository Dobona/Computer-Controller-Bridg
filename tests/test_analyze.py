"""screen_analyze 聚合单元测试（mock 各引擎，验证结构/摘要/降级/坐标）。"""

from __future__ import annotations

import pytest
from PIL import Image

from core import analyze


@pytest.fixture
def fake_engines(monkeypatch):
    wins = [
        {"hwnd": 1, "title": "测试窗口", "class": "Test", "rect": [100, 100, 500, 400], "pid": 1, "active": True},
        {"hwnd": 2, "title": "其他窗口", "class": "Other", "rect": [0, 0, 200, 200], "pid": 2, "active": False},
    ]
    monkeypatch.setattr(analyze.windows, "list_windows", lambda *a, **k: wins)
    monkeypatch.setattr(
        analyze.uia,
        "get_elements",
        lambda scope_hwnd=None, max_elements=200: {
            "ok": True,
            "elements": [
                {"name": "确定", "type": "button", "rect": [150, 150, 180, 170], "enabled": True},
                {"name": "输入", "type": "edit", "rect": [160, 200, 300, 220], "enabled": True},
            ],
        },
    )
    monkeypatch.setattr(
        analyze.ocr,
        "ocr_image",
        lambda img, offset_x=0, offset_y=0: {
            "ok": True,
            "texts": [{"text": "你好", "rect": [100 + offset_x, 100 + offset_y, 200 + offset_x, 120 + offset_y], "score": 0.99}],
        },
    )
    monkeypatch.setattr(analyze.screen, "grab", lambda: Image.new("RGB", (2560, 1440), "white"))
    monkeypatch.setattr(analyze.coords, "virtual_screen", lambda: (0, 0, 2560, 1440))
    return wins


def test_analyze_full_structure(fake_engines):
    result = analyze.analyze(scope="full")
    assert result["screen"]["virtual"] == [0, 0, 2560, 1440]
    assert len(result["windows"]) == 2
    assert len(result["elements"]) == 2
    assert result["texts"][0]["text"] == "你好"
    assert result["degraded"] == {"uia": False, "ocr": False}
    assert "测试窗口" in result["summary"]
    assert "可交互元素 2 个" in result["summary"]
    assert "识别到文本 1 处" in result["summary"]
    assert result["duration_ms"] >= 0


def test_analyze_window_scope(fake_engines):
    """window 范围：只保留前台窗口，OCR 用窗口矩形偏移，元素按矩形过滤。"""
    result = analyze.analyze(scope="window")
    assert [w["title"] for w in result["windows"]] == ["测试窗口"]
    # OCR 偏移 = 窗口 rect 左上角 (100, 100)
    assert result["texts"][0]["rect"] == [200, 200, 300, 220]
    # UIA 元素按窗口矩形过滤（两个元素都在窗口内，保留）
    assert len(result["elements"]) == 2


def test_analyze_degrades(fake_engines, monkeypatch):
    monkeypatch.setattr(
        analyze.uia,
        "get_elements",
        lambda scope_hwnd=None, max_elements=200: {
            "ok": False,
            "elements": [],
            "error": "COM 初始化失败",
        },
    )
    monkeypatch.setattr(
        analyze.ocr,
        "ocr_image",
        lambda img, offset_x=0, offset_y=0: {"ok": False, "texts": [], "error": "模型缺失"},
    )
    result = analyze.analyze(scope="full")
    assert result["degraded"] == {"uia": True, "ocr": True}
    assert result["enabled"] == {"uia": True, "ocr": True}
    assert result["reasons"] == {"uia": "COM 初始化失败", "ocr": "模型缺失"}
    assert "UIA 不可用" in result["summary"]
    assert "OCR 不可用" in result["summary"]
    assert "COM 初始化失败" in result["summary"]
    assert "模型缺失" in result["summary"]


def test_analyze_disabled_flags(fake_engines, monkeypatch):
    called = {"uia": False, "ocr": False}

    def fake_uia(**kw):
        called["uia"] = True
        return {"ok": True, "elements": []}

    def fake_ocr(**kw):
        called["ocr"] = True
        return {"ok": True, "texts": []}

    monkeypatch.setattr(analyze.uia, "get_elements", fake_uia)
    monkeypatch.setattr(analyze.ocr, "ocr_image", fake_ocr)
    result = analyze.analyze(scope="full", with_uia=False, with_ocr=False)
    assert called == {"uia": False, "ocr": False}
    # 未请求的层不算降级，也不报原因
    assert result["degraded"] == {"uia": False, "ocr": False}
    assert result["enabled"] == {"uia": False, "ocr": False}
    assert result["reasons"] == {"uia": None, "ocr": None}
    assert "UIA 未启用" in result["summary"]
    assert "OCR 未启用" in result["summary"]
