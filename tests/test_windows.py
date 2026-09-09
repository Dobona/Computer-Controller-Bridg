"""窗口枚举单元测试（mock 系统调用，不触碰真实窗口）。"""

from __future__ import annotations

import pytest

from core import windows


def _fake_window_info(hwnd, active):
    return {
        "hwnd": hwnd,
        "title": f"窗口{hwnd}",
        "class": "TestClass",
        "rect": [0, 0, 100, 100],
        "pid": 1,
        "active": active,
    }


@pytest.fixture
def fake_windows(monkeypatch):
    """EnumWindows 按 Z 序返回 [3, 1, 2]，前台窗口为 1。"""
    hwnds = [3, 1, 2]

    def fake_enum(callback):
        for hwnd in hwnds:
            callback(hwnd, None)
        return True

    monkeypatch.setattr(windows, "_enum_windows", fake_enum)
    monkeypatch.setattr(windows, "_window_info", _fake_window_info)
    monkeypatch.setattr(windows, "_foreground_window", lambda: 1)
    monkeypatch.setattr(windows, "_is_visible", lambda hwnd: True)
    return hwnds


def test_list_windows_z_order_and_active(fake_windows):
    result = windows.list_windows()
    assert [w["hwnd"] for w in result] == [3, 1, 2]  # Z 序：前台在前
    assert result[1]["active"] is True
    assert result[0]["active"] is False


def test_list_windows_invisible_filtered(fake_windows, monkeypatch):
    monkeypatch.setattr(windows, "_is_visible", lambda hwnd: hwnd != 1)
    result = windows.list_windows()
    assert [w["hwnd"] for w in result] == [3, 2]


def test_list_windows_title_filter(fake_windows, monkeypatch):
    def info_with_title(hwnd, active):
        d = _fake_window_info(hwnd, active)
        d["title"] = "" if hwnd == 2 else d["title"]
        return d

    monkeypatch.setattr(windows, "_window_info", info_with_title)
    result = windows.list_windows(with_title_only=True)
    assert [w["hwnd"] for w in result] == [3, 1]


def test_active_window(monkeypatch):
    monkeypatch.setattr(windows, "_foreground_window", lambda: 42)
    monkeypatch.setattr(windows, "_window_info", _fake_window_info)
    info = windows.active_window()
    assert info["hwnd"] == 42
    assert info["active"] is True


def test_active_window_none(monkeypatch):
    monkeypatch.setattr(windows, "_foreground_window", lambda: 0)
    assert windows.active_window() is None
