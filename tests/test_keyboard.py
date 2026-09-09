"""键盘引擎单元测试：通过 mock 屏蔽真实输入，验证事件序列、参数与按键状态表。"""

from __future__ import annotations

import pytest

import core.keyboard as kb
from core.keyboard import hotkey, key_down, key_press, key_up, release_all, type_text


@pytest.fixture(autouse=True)
def clean_held():
    """每个用例前后清空按键状态表，避免用例间互相影响。"""
    release_all()
    yield
    release_all()


@pytest.fixture
def capture(monkeypatch):
    """收集 _send_keyboard 收到的事件，替代真实 SendInput。"""
    sent: list[tuple[int, int, int]] = []
    monkeypatch.setattr(kb, "_send_keyboard", lambda events: sent.extend(events))
    return sent


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(kb.time, "sleep", lambda s: None)


def scan(vk: int) -> int:
    return kb.user32.MapVirtualKeyW(vk, 0)


def test_key_name_mapping():
    assert kb.VK["ctrl"] == 0x11
    assert kb.VK["shift"] == 0x10
    assert kb.VK["alt"] == 0x12
    assert kb.VK["win"] == 0x5B
    assert kb.VK["enter"] == 0x0D
    assert kb.VK["esc"] == 0x1B
    assert kb.VK["f12"] == 0x7B
    assert kb.VK["f24"] == 0x87
    assert kb.VK["a"] == 0x41
    assert kb.VK["9"] == 0x39


def test_resolve_invalid_key():
    with pytest.raises(ValueError, match="非法按键名"):
        kb._resolve_key("??")
    with pytest.raises(ValueError, match="非法按键名"):
        key_down("not-a-key")


def test_key_down_up_events(capture):
    key_down("ctrl")
    key_up("ctrl")
    assert capture == [
        (0, scan(0x11), kb.KEYEVENTF_SCANCODE),
        (0, scan(0x11), kb.KEYEVENTF_SCANCODE | kb.KEYEVENTF_KEYUP),
    ]


def test_extended_key_flag(capture):
    key_down("down")
    assert capture[0][2] & kb.KEYEVENTF_EXTENDEDKEY
    assert capture[0][1] == scan(0x28)
    assert capture[0][2] & kb.KEYEVENTF_SCANCODE


def test_held_keys_tracking(capture):
    key_down("ctrl")
    key_down("shift")
    assert set(kb.held_keys().values()) == {"ctrl", "shift"}
    key_up("ctrl")
    assert set(kb.held_keys().values()) == {"shift"}
    key_up("shift")
    assert kb.held_keys() == {}


def test_release_all_reverse_order(capture):
    key_down("ctrl")
    key_down("c")
    released = release_all()
    assert released == ["c", "ctrl"]  # 逆序释放
    up_flags = [e[2] for e in capture if e[2] & kb.KEYEVENTF_KEYUP]
    assert len(up_flags) == 2
    assert kb.held_keys() == {}


def test_hotkey_order(capture, no_sleep):
    hotkey(["ctrl", "shift", "esc"])
    scans = [e[1] for e in capture]
    assert scans == [
        scan(0x11), scan(0x10), scan(0x1B),
        scan(0x1B), scan(0x10), scan(0x11),
    ]
    assert kb.held_keys() == {}


def test_hotkey_empty(capture):
    with pytest.raises(ValueError, match="至少需要一个按键"):
        hotkey([])
    assert capture == []


def test_key_press_hold(capture, no_sleep):
    key_press("enter", hold_ms=200)
    assert [e[1] for e in capture] == [scan(0x0D), scan(0x0D)]
    assert capture[1][2] & kb.KEYEVENTF_KEYUP
    assert kb.held_keys() == {}


def test_type_text_unicode_events(capture, no_sleep):
    type_text("A你")
    assert len(capture) == 4  # 每个字符一组 down/up
    assert capture[0] == (0, 0x41, kb.KEYEVENTF_UNICODE)
    assert capture[1][2] == kb.KEYEVENTF_UNICODE | kb.KEYEVENTF_KEYUP
    assert capture[2][1] == 0x4F60  # 你
    assert capture[3][2] == kb.KEYEVENTF_UNICODE | kb.KEYEVENTF_KEYUP


def test_type_text_emoji_surrogate_pair(capture, no_sleep):
    type_text("🌟")
    assert len(capture) == 4  # 代理对 2 个 UTF-16 单元 × down/up
    units = [e[1] for e in capture]
    assert units == [0xD83C, 0xD83C, 0xDF1F, 0xDF1F]


def test_type_text_interval_sleep(capture, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(kb.time, "sleep", lambda s: slept.append(s))
    type_text("ab", interval_ms=30)
    # 可中断睡眠切成 <=10ms 的小片，总时长保持 30ms 不变
    assert sum(slept) >= 0.03
    assert all(s <= 0.01 for s in slept)


def test_type_text_empty(capture):
    type_text("")
    assert capture == []


def test_type_text_clipboard_path(capture, no_sleep, monkeypatch):
    set_calls: list[str] = []
    monkeypatch.setattr(kb, "_set_clipboard_text", lambda t: set_calls.append(t))
    text = "很长" * 200
    type_text(text, use_clipboard=True)
    assert set_calls == [text]
    assert [e[1] for e in capture] == [
        scan(0x11), scan(0x56), scan(0x56), scan(0x11),
    ]  # ctrl+v 的扫描码序列
    assert kb.held_keys() == {}


def test_exception_path_release_all(capture, monkeypatch, no_sleep):
    """模拟发送中途失败：按键表仍保留记录，release_all 可兜底清空并释放。"""
    key_down("ctrl")
    key_down("shift")
    sent: list[tuple[int, int, int]] = []

    def flaky(events):
        sent.extend(events)
        raise OSError("模拟发送失败")

    monkeypatch.setattr(kb, "_send_keyboard", flaky)
    with pytest.raises(OSError):
        key_press("a")  # down 已登记，发送失败

    assert set(kb.held_keys().values()) == {"ctrl", "shift", "a"}
    monkeypatch.setattr(kb, "_send_keyboard", lambda events: capture.extend(events))
    released = release_all()
    assert released == ["a", "shift", "ctrl"]  # 逆序释放
    assert kb.held_keys() == {}


def test_key_press_hold_interrupted(capture, monkeypatch):
    """长按期间急停应立即中止并松开按键。"""
    from core.action import ActionInterrupted
    from safety import panic

    panic.clear_interrupt()
    try:
        monkeypatch.setattr(kb.time, "sleep", lambda s: panic.request_interrupt())
        with pytest.raises(ActionInterrupted, match="长按被急停中断"):
            key_press("enter", hold_ms=500)
        assert kb.held_keys() == {}
        # down 之后已发出 keyup
        assert capture[-1][2] & kb.KEYEVENTF_KEYUP
    finally:
        panic.clear_interrupt()


def test_hotkey_hold_interrupted(capture, monkeypatch):
    """组合快捷键保持期间急停应立即中止并逆序松开所有按键。"""
    from core.action import ActionInterrupted
    from safety import panic

    panic.clear_interrupt()
    try:
        monkeypatch.setattr(kb.time, "sleep", lambda s: panic.request_interrupt())
        with pytest.raises(ActionInterrupted, match="快捷键被急停中断"):
            hotkey(["ctrl", "c"], hold_ms=1000)
        assert kb.held_keys() == {}
        assert len(capture) == 4  # down ctrl / down c / up c / up ctrl
    finally:
        panic.clear_interrupt()


def test_type_text_interval_interrupted(capture, monkeypatch):
    """文本输入字符间隔期间急停应立即中止，不再继续发送后续字符。"""
    from core.action import ActionInterrupted
    from safety import panic

    panic.clear_interrupt()
    try:
        monkeypatch.setattr(kb.time, "sleep", lambda s: panic.request_interrupt())
        with pytest.raises(ActionInterrupted, match="文本输入被急停中断"):
            type_text("ab", interval_ms=100)
        assert len(capture) == 2  # 只发送了第一个字符
    finally:
        panic.clear_interrupt()
