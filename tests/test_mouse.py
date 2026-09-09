"""鼠标引擎单元测试：通过 mock 屏蔽真实输入，只验证事件序列与参数。"""

from __future__ import annotations

import pytest

import core.coords as coords_mod
import core.mouse as mouse_mod
from core.mouse import (
    button_down,
    button_up,
    click,
    double_click,
    held_buttons,
    move_to,
    release_all_buttons,
    right_click,
    scroll,
)


@pytest.fixture
def fake_screen(monkeypatch):
    monkeypatch.setattr(coords_mod, "virtual_screen", lambda: (0, 0, 2560, 1440))
    monkeypatch.setattr(mouse_mod, "get_cursor_pos", lambda: (0, 0))


@pytest.fixture
def capture(monkeypatch):
    """收集 _send_inputs 收到的 moves，替代真实 SendInput。"""
    sent: list[tuple[int, int, int, int]] = []
    monkeypatch.setattr(mouse_mod, "_send_inputs", lambda moves: sent.extend(moves))
    return sent


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(mouse_mod.time, "sleep", lambda s: None)


def test_move_sends_absolute_move(fake_screen, capture):
    move_to(100, 100)
    assert len(capture) == 1
    dx, dy, flags, data = capture[0]
    assert flags & mouse_mod.MOUSEEVENTF_MOVE
    assert flags & mouse_mod.MOUSEEVENTF_ABSOLUTE
    assert flags & mouse_mod.MOUSEEVENTF_VIRTUALDESK
    assert data == 0
    assert (dx, dy) == mouse_mod.normalize_abs(100, 100)


def test_move_out_of_bounds_rejected(fake_screen, capture):
    with pytest.raises(ValueError, match="越界"):
        move_to(99999, 0)
    assert capture == []


def test_move_smooth_interpolates(fake_screen, capture, no_sleep):
    move_to(100, 0, duration_ms=100, move_step_ms=10)
    assert len(capture) == 10  # 100ms / 10ms = 10 步插值


def test_move_to_same_position_single_event(fake_screen, capture):
    # 起点就是目标 (0,0)，不应插值
    move_to(0, 0, duration_ms=200)
    assert len(capture) == 1


def test_click_left_sends_down_up(fake_screen, capture):
    click(10, 10, button="left")
    assert len(capture) == 3  # 移动 + 按下 + 松开
    assert capture[0][2] & mouse_mod.MOUSEEVENTF_MOVE
    assert capture[1][2] & mouse_mod.MOUSEEVENTF_LEFTDOWN
    assert capture[2][2] & mouse_mod.MOUSEEVENTF_LEFTUP


def test_click_right(fake_screen, capture):
    right_click(10, 10)
    assert len(capture) == 3
    assert capture[1][2] & mouse_mod.MOUSEEVENTF_RIGHTDOWN
    assert capture[2][2] & mouse_mod.MOUSEEVENTF_RIGHTUP


def test_double_click(fake_screen, capture, no_sleep):
    double_click(10, 10)
    assert len(capture) == 5  # 移动 + 两次 (按下+松开)
    flags = [m[2] for m in capture]
    assert flags[0] & mouse_mod.MOUSEEVENTF_MOVE
    assert flags[1:] == [
        mouse_mod.MOUSEEVENTF_LEFTDOWN,
        mouse_mod.MOUSEEVENTF_LEFTUP,
        mouse_mod.MOUSEEVENTF_LEFTDOWN,
        mouse_mod.MOUSEEVENTF_LEFTUP,
    ]


def test_click_without_coords_uses_cursor(fake_screen, capture):
    click(button="middle")
    assert len(capture) == 2
    assert capture[0][2] & mouse_mod.MOUSEEVENTF_MIDDLEDOWN
    assert capture[1][2] & mouse_mod.MOUSEEVENTF_MIDDLEUP


def test_click_invalid_button(fake_screen, capture):
    with pytest.raises(ValueError, match="非法按键"):
        click(10, 10, button="side")
    assert capture == []


def test_click_invalid_times(fake_screen, capture):
    with pytest.raises(ValueError, match="点击次数"):
        click(10, 10, times=0)
    assert capture == []


def test_scroll_sends_wheel(fake_screen, capture):
    scroll(120)
    assert len(capture) == 1
    dx, dy, flags, data = capture[0]
    assert flags & mouse_mod.MOUSEEVENTF_WHEEL
    assert data == 120


def test_scroll_negative_delta(fake_screen, capture):
    scroll(-120)
    assert capture[0][3] == -120


def test_scroll_zero_is_noop(fake_screen, capture):
    scroll(0)
    assert capture == []


def test_scroll_moves_before_wheel(fake_screen, capture):
    scroll(120, x=100, y=100)
    assert len(capture) == 2
    assert capture[0][2] & mouse_mod.MOUSEEVENTF_MOVE
    assert capture[1][2] & mouse_mod.MOUSEEVENTF_WHEEL


def test_scroll_partial_coords_rejected(fake_screen, capture):
    with pytest.raises(ValueError, match="同时提供"):
        scroll(120, x=100)
    assert capture == []


def test_button_down_up(fake_screen, capture):
    button_down("left", x=50, y=50)
    button_up("left")
    assert len(capture) == 3
    assert capture[0][2] & mouse_mod.MOUSEEVENTF_MOVE
    assert capture[1][2] & mouse_mod.MOUSEEVENTF_LEFTDOWN
    assert capture[2][2] & mouse_mod.MOUSEEVENTF_LEFTUP


def test_button_down_invalid(fake_screen, capture):
    with pytest.raises(ValueError, match="非法按键"):
        button_down("wheel")
    assert capture == []


def test_held_buttons_tracking(fake_screen, capture):
    button_down("left")
    button_down("right")
    assert held_buttons() == {"left", "right"}
    button_up("left")
    assert held_buttons() == {"right"}
    button_up("right")
    assert held_buttons() == set()


def test_release_all_buttons(fake_screen, capture):
    button_down("left")
    button_down("middle")
    released = release_all_buttons()
    assert sorted(released) == ["left", "middle"]
    assert held_buttons() == set()
    # 两个 KEYUP 事件
    up_flags = [m[2] for m in capture if m[2] & mouse_mod.MOUSEEVENTF_LEFTUP or m[2] & mouse_mod.MOUSEEVENTF_MIDDLEUP]
    assert len(up_flags) == 2


def test_move_smooth_interrupted(fake_screen, capture, monkeypatch):
    """平滑移动途中急停应立即中止，不再继续插值。"""
    from core.action import ActionInterrupted
    from safety import panic

    panic.clear_interrupt()
    try:
        monkeypatch.setattr(mouse_mod.time, "sleep", lambda s: panic.request_interrupt())
        with pytest.raises(ActionInterrupted, match="平滑移动"):
            move_to(100, 0, duration_ms=100, move_step_ms=10)
        assert len(capture) == 1  # 只发出第一步移动
    finally:
        panic.clear_interrupt()
