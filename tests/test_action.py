"""组合动作调度单元测试：长按/拖拽的事件序列与急停中断（不触碰真实输入）。"""

from __future__ import annotations

import pytest

from core import action
from safety import panic


@pytest.fixture(autouse=True)
def clean_panic():
    """每个用例前后清除急停标志。"""
    panic.clear_interrupt()
    yield
    panic.clear_interrupt()


@pytest.fixture
def fake_inputs(monkeypatch):
    """mock 鼠标/键盘/睡眠，记录调用序列。"""
    calls: list[str] = []
    moves: list[tuple[int, int]] = []

    monkeypatch.setattr(
        action.mouse,
        "button_down",
        lambda button="left", x=None, y=None: calls.append(f"down:{button}"),
    )
    monkeypatch.setattr(action.mouse, "button_up", lambda button="left": calls.append(f"up:{button}"))
    monkeypatch.setattr(
        action.mouse,
        "move_to",
        lambda x, y: (moves.append((x, y)), calls.append(f"move:({x},{y})"))[1],
    )
    monkeypatch.setattr(action.keyboard, "release_all", lambda: calls.append("release_all"))
    monkeypatch.setattr(action.time, "sleep", lambda s: calls.append("sleep"))
    return {"calls": calls, "moves": moves}


def test_long_press_normal(fake_inputs, monkeypatch):
    monkeypatch.setattr(action, "_wait_interruptible", lambda ms, started_at=None, timeout_s=None: True)
    result = action.mouse_long_press(x=10, y=10, button="left", hold_ms=800)
    calls = fake_inputs["calls"]
    assert calls[:2] == ["down:left", "up:left"]
    assert "release_all" not in calls
    assert isinstance(result["held_ms"], int) and result["held_ms"] >= 0
    assert result["button"] == "left"


def test_long_press_interrupted(fake_inputs, monkeypatch):
    monkeypatch.setattr(action, "_wait_interruptible", lambda ms, started_at=None, timeout_s=None: False)
    with pytest.raises(action.ActionInterrupted, match="长按被急停中断"):
        action.mouse_long_press(button="left", hold_ms=500)
    calls = fake_inputs["calls"]
    assert "down:left" in calls and "up:left" in calls
    assert "release_all" in calls  # 中断后兜底释放


def test_long_press_refused_when_flag_set(fake_inputs):
    panic.request_interrupt()
    with pytest.raises(action.ActionInterrupted, match="拒绝执行"):
        action.mouse_long_press(button="left", hold_ms=500)
    assert "down:left" not in fake_inputs["calls"]


def test_long_press_invalid_hold(fake_inputs):
    with pytest.raises(ValueError, match="长按时长"):
        action.mouse_long_press(button="left", hold_ms=0)
    assert fake_inputs["calls"] == []


def test_drag_normal(fake_inputs):
    result = action.mouse_drag(0, 0, 100, 0, duration_ms=100, move_step_ms=10)
    calls = fake_inputs["calls"]
    moves = fake_inputs["moves"]
    assert calls[0] == "down:left"
    assert calls[-1] == "up:left"
    assert len(moves) == 10  # 100ms / 10ms = 10 步插值
    assert moves[0] == (10, 0) and moves[-1] == (100, 0)
    assert calls.count("sleep") == 9  # 步与步之间
    assert "release_all" not in calls
    assert result["moved"] is True


def test_drag_interrupted_mid_move(fake_inputs, monkeypatch):
    original_moves = fake_inputs["moves"]

    def interrupt_after_3(x, y):
        original_moves.append((x, y))
        if len(original_moves) >= 3:
            panic.request_interrupt()

    monkeypatch.setattr(action.mouse, "move_to", interrupt_after_3)
    with pytest.raises(action.ActionInterrupted, match="拖拽被急停中断"):
        action.mouse_drag(0, 0, 100, 0, duration_ms=500, move_step_ms=10)
    calls = fake_inputs["calls"]
    assert "down:left" in calls and "up:left" in calls
    assert "release_all" in calls
    assert len(original_moves) == 3  # 中断后不再继续移动


def test_drag_interrupted_during_hold(fake_inputs, monkeypatch):
    monkeypatch.setattr(action, "_wait_interruptible", lambda ms, started_at=None, timeout_s=None: False)
    with pytest.raises(action.ActionInterrupted, match="拖拽被急停中断"):
        action.mouse_drag(0, 0, 10, 10, hold_before_ms=500, duration_ms=100)
    calls = fake_inputs["calls"]
    assert "down:left" in calls and "up:left" in calls
    assert "release_all" in calls
    assert fake_inputs["moves"] == []  # 未开始移动


def test_drag_refused_when_flag_set(fake_inputs):
    panic.request_interrupt()
    with pytest.raises(action.ActionInterrupted, match="拒绝执行"):
        action.mouse_drag(0, 0, 10, 10)
    assert fake_inputs["calls"] == []


def test_drag_invalid_params(fake_inputs):
    with pytest.raises(ValueError, match="拖拽时长"):
        action.mouse_drag(0, 0, 10, 10, duration_ms=-1)
    with pytest.raises(ValueError, match="起始停留"):
        action.mouse_drag(0, 0, 10, 10, hold_before_ms=-1)
    with pytest.raises(ValueError, match="移动步长"):
        action.mouse_drag(0, 0, 10, 10, move_step_ms=0)
    assert fake_inputs["calls"] == []


def test_clear_interrupt_resumes(fake_inputs, monkeypatch):
    monkeypatch.setattr(action, "_wait_interruptible", lambda ms, started_at=None, timeout_s=None: True)
    panic.request_interrupt()
    with pytest.raises(action.ActionInterrupted):
        action.mouse_long_press(button="left", hold_ms=100)
    panic.clear_interrupt()
    result = action.mouse_long_press(button="left", hold_ms=100)
    assert isinstance(result["held_ms"], int)


def test_wait_interruptible_normal(monkeypatch):
    monkeypatch.setattr(action.time, "sleep", lambda s: None)
    assert action._wait_interruptible(50) is True


def test_wait_interruptible_aborted(monkeypatch):
    monkeypatch.setattr(action.time, "sleep", lambda s: panic.request_interrupt())
    assert action._wait_interruptible(5000) is False


def test_long_press_timeout(fake_inputs):
    """长按超过 timeout_s 自动中止并释放。"""
    with pytest.raises(action.ActionTimeout, match="超时"):
        action.mouse_long_press(button="left", hold_ms=50000, timeout_s=0.05)
    calls = fake_inputs["calls"]
    assert "down:left" in calls and "up:left" in calls
    assert "release_all" in calls


def test_drag_timeout(fake_inputs):
    """拖拽超过 timeout_s 自动中止并释放。"""
    with pytest.raises(action.ActionTimeout, match="超时"):
        action.mouse_drag(0, 0, 1000, 1000, duration_ms=10_000_000, move_step_ms=10, timeout_s=0.05)
    calls = fake_inputs["calls"]
    assert "down:left" in calls and "up:left" in calls
    assert "release_all" in calls
