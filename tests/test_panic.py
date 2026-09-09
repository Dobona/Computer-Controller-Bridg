"""急停模块单元测试：热键解析、中断切换、热键回调。"""

from __future__ import annotations

import pytest

from safety import panic


@pytest.fixture(autouse=True)
def clean_panic():
    panic.clear_interrupt()
    yield
    panic.clear_interrupt()
    panic.stop()


def test_parse_hotkey_default():
    mods, vk = panic._parse_hotkey(("ctrl", "alt", "shift", "esc"))
    assert mods == panic.MOD_CONTROL | panic.MOD_ALT | panic.MOD_SHIFT
    assert vk == panic.VK_ESC


def test_parse_hotkey_invalid_key():
    with pytest.raises(ValueError, match="暂不支持"):
        panic._parse_hotkey(("ctrl", "enter"))


def test_parse_hotkey_missing_key():
    with pytest.raises(ValueError, match="必须包含"):
        panic._parse_hotkey(("ctrl", "alt"))


def test_toggle_interrupt():
    assert panic.toggle_interrupt() is True
    assert panic.interrupted() is True
    assert panic.toggle_interrupt() is False
    assert panic.interrupted() is False


def test_on_hotkey_releases_and_resets(monkeypatch):
    released = []
    monkeypatch.setattr(panic, "_release_everything", lambda: released.append(1))
    panic._on_hotkey()
    assert panic.interrupted() is True
    assert released == [1]
    panic._on_hotkey()
    assert panic.interrupted() is False
    assert len(released) == 1  # 复位时不重复释放


def test_watcher_register_failure_returns_gracefully(monkeypatch):
    monkeypatch.setattr(panic.user32, "RegisterHotKey", lambda *a: 0)
    panic._watcher_loop(panic.MOD_CONTROL, panic.VK_ESC)  # 不应抛异常
    assert panic.hotkey_ok() is False
