"""组合动作调度：长按 / 拖拽（原子执行，全程可被急停中断，中断时立即释放按键）。"""

from __future__ import annotations

import time

from core import keyboard, mouse
from safety.panic import interrupted


class ActionInterrupted(RuntimeError):
    """动作被急停中断。"""


class ActionTimeout(RuntimeError):
    """动作执行超时。"""


DEFAULT_TIMEOUT_S = 30.0


def _check_interrupt() -> None:
    """动作开始前检查：急停标志已置位则拒绝执行新动作。"""
    if interrupted():
        raise ActionInterrupted("急停标志已置位，拒绝执行新动作")


def _check_timeout(started_at: float, timeout_s: float | None) -> None:
    if timeout_s is not None and time.perf_counter() - started_at > timeout_s:
        raise ActionTimeout(f"动作超时（超过 {timeout_s}s 自动中止）")


def _wait_interruptible(ms: float, started_at: float | None = None, timeout_s: float | None = None) -> bool:
    """可中断等待。返回 True=正常等待完成，False=等待期间被急停。"""
    if ms <= 0:
        return True
    deadline = time.perf_counter() + ms / 1000
    while True:
        if interrupted():
            return False
        if started_at is not None:
            _check_timeout(started_at, timeout_s)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return True
        time.sleep(min(0.01, remaining))


def mouse_long_press(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    hold_ms: int = 500,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """长按：移动到目标 → 按下 → 等待（可中断）→ 抬起。返回实际按住时长。"""
    _check_interrupt()
    if hold_ms <= 0:
        raise ValueError(f"非法长按时长: {hold_ms}，必须大于 0")
    mouse.button_down(button, x=x, y=y)
    pressed_at = time.perf_counter()
    was_interrupted = False
    was_timeout = False
    try:
        if not _wait_interruptible(hold_ms, started_at=pressed_at, timeout_s=timeout_s):
            was_interrupted = True
    except ActionTimeout:
        was_timeout = True
    finally:
        mouse.button_up(button)
        if was_interrupted or was_timeout:
            keyboard.release_all()
    if was_interrupted:
        raise ActionInterrupted(f"{button} 长按被急停中断")
    if was_timeout:
        raise ActionTimeout(f"{button} 长按超时中止")
    return {
        "button": button,
        "x": x,
        "y": y,
        "held_ms": int((time.perf_counter() - pressed_at) * 1000),
    }


def mouse_drag(
    from_x: int,
    from_y: int,
    to_x: int,
    to_y: int,
    button: str = "left",
    duration_ms: int = 500,
    hold_before_ms: int = 0,
    move_step_ms: int = 10,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """拖拽：移动到起点 → 按下 →（可选长按停留）→ 插值移动到终点 → 抬起。全程可中断。"""
    _check_interrupt()
    if duration_ms < 0:
        raise ValueError(f"非法拖拽时长: {duration_ms}，不能为负数")
    if hold_before_ms < 0:
        raise ValueError(f"非法起始停留时长: {hold_before_ms}，不能为负数")
    if move_step_ms <= 0:
        raise ValueError(f"非法移动步长: {move_step_ms}，必须大于 0")

    mouse.button_down(button, x=from_x, y=from_y)
    started_at = time.perf_counter()
    was_interrupted = False
    was_timeout = False
    moved = False
    try:
        if hold_before_ms > 0:
            if not _wait_interruptible(hold_before_ms, started_at=started_at, timeout_s=timeout_s):
                was_interrupted = True
        if not was_interrupted and duration_ms > 0:
            steps = max(1, int(duration_ms / move_step_ms))
            for i in range(1, steps + 1):
                if interrupted():
                    was_interrupted = True
                    break
                _check_timeout(started_at, timeout_s)
                t = i / steps
                cx = round(from_x + (to_x - from_x) * t)
                cy = round(from_y + (to_y - from_y) * t)
                mouse.move_to(cx, cy)
                moved = True
                if i < steps:
                    time.sleep(move_step_ms / 1000)
    except ActionTimeout:
        was_timeout = True
    finally:
        mouse.button_up(button)
        if was_interrupted or was_timeout:
            keyboard.release_all()
    if was_interrupted:
        raise ActionInterrupted(f"{button} 拖拽被急停中断")
    if was_timeout:
        raise ActionTimeout(f"{button} 拖拽超时中止")
    return {
        "button": button,
        "from": [from_x, from_y],
        "to": [to_x, to_y],
        "duration_ms": int((time.perf_counter() - started_at) * 1000),
        "moved": moved,
    }
