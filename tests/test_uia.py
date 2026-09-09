"""UIA 元素树单元测试：解析与降级路径（mock 自动化对象）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import uia


def _fake_element(name="确定", ctype=50000, rect=(10, 20, 30, 40), enabled=True, aid="", cls="Button"):
    return SimpleNamespace(
        CurrentName=name,
        CurrentControlType=ctype,
        CurrentBoundingRectangle=SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3]),
        CurrentIsEnabled=enabled,
        CurrentAutomationId=aid,
        CurrentClassName=cls,
    )


def test_element_to_dict_button():
    d = uia.element_to_dict(_fake_element())
    assert d["name"] == "确定"
    assert d["type"] == "button"
    assert d["rect"] == [10, 20, 30, 40]
    assert d["enabled"] is True


def test_element_to_dict_unknown_type():
    d = uia.element_to_dict(_fake_element(ctype=99999))
    assert d["type"] == "other"


def test_element_to_dict_failure_returns_empty():
    assert uia.element_to_dict(SimpleNamespace()) == {}


def test_get_elements_filters_and_caps(monkeypatch):
    elements = [
        _fake_element(name="有名字"),
        _fake_element(name="", ctype=50020),  # 无名 → 过滤
        _fake_element(name="零尺寸", rect=(5, 5, 5, 5)),  # 零尺寸 → 过滤
        _fake_element(name="按钮2"),
    ]
    fake_array = SimpleNamespace(Length=len(elements), _els=elements)

    def fake_get(i):
        return fake_array._els[i]

    fake_array.GetElement = fake_get
    fake_root = SimpleNamespace(FindAll=lambda scope, cond: fake_array)
    fake_automation = SimpleNamespace(
        GetRootElement=lambda: fake_root,
        ElementFromHandle=lambda h: fake_root,
        CreateTrueCondition=lambda: None,
    )
    monkeypatch.setattr(uia, "_get_automation", lambda: fake_automation)

    result = uia.get_elements()
    assert result["ok"] is True
    assert [e["name"] for e in result["elements"]] == ["有名字", "按钮2"]


def test_get_elements_unavailable_degrades(monkeypatch):
    def boom():
        raise RuntimeError("UIA 不可用")

    monkeypatch.setattr(uia, "_get_automation", boom)
    result = uia.get_elements()
    assert result["ok"] is False
    assert result["elements"] == []
    assert "error" in result
