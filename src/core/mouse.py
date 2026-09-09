"""鼠标引擎：基于 SendInput 的系统级鼠标输入。

覆盖：移动（可平滑插值）、左/右/中键单击、双击、右键、滚轮、
按住/松开原语、查询当前鼠标位置。
组合动作（长按/拖拽）在步骤 5 的 action 模块中实现。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from core.coords import get_cursor_pos, normalize_abs, validate_coords
from safety import panic

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x0100

BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

_held_buttons: set[str] = set()
_buttons_lock = threading.Lock()


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class INPUT(ctypes.Structure):
    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send_inputs(moves: list[tuple[int, int, int, int]]) -> None:
    """发送一组鼠标输入。moves: [(dx, dy, flags, mouse_data), ...]"""
    inputs = []
    for dx, dy, flags, mouse_data in moves:
        mi = MOUSEINPUT(dx, dy, mouse_data, flags, 0, 0)
        inputs.append(INPUT(INPUT_MOUSE, INPUT._INPUTUNION(mi)))
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(array), array, ctypes.sizeof(INPUT))
    if sent != len(array):
        raise ctypes.WinError(ctypes.get_last_error())


def _move_abs(x: int, y: int) -> None:
    nx, ny = normalize_abs(x, y)
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    _send_inputs([(nx, ny, flags, 0)])


def _validate_button(button: str) -> None:
    if button not in BUTTON_FLAGS:
        raise ValueError(f"非法按键: {button}，可选 {list(BUTTON_FLAGS)}")


def _raise_if_interrupted(action: str) -> None:
    """急停标志已置位时立即中止（平滑移动循环）。"""
    if panic.interrupted():
        from core.action import ActionInterrupted

        raise ActionInterrupted(action)


def _sleep_interruptible(seconds: float, action: str) -> None:
    """可被急停中断的睡眠；中断时抛 ActionInterrupted。"""
    deadline = time.perf_counter() + seconds
    while True:
        _raise_if_interrupted(action)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(0.01, remaining))


def move_to(x: int, y: int, duration_ms: int = 0, move_step_ms: int = 10) -> None:
    """移动鼠标到 (x, y)。duration_ms > 0 时按线性插值平滑移动（可被急停中断）。"""
    validate_coords(x, y)
    start = get_cursor_pos()
    if duration_ms <= 0 or start == (x, y):
        _move_abs(x, y)
        return
    steps = max(1, int(duration_ms / move_step_ms))
    for i in range(1, steps + 1):
        _raise_if_interrupted("鼠标平滑移动被急停中断")
        t = i / steps
        cx = round(start[0] + (x - start[0]) * t)
        cy = round(start[1] + (y - start[1]) * t)
        _move_abs(cx, cy)
        _sleep_interruptible(move_step_ms / 1000, "鼠标平滑移动被急停中断")


def click(
    x: int | None = None,
    y: int | None = None,
    button: str = "left",
    times: int = 1,
    click_interval_ms: int = 50,
) -> None:
    """单击/双击（button: left/right/middle）。坐标可选，缺省时在当前光标位置点击。"""
    _validate_button(button)
    if times < 1:
        raise ValueError(f"非法点击次数: {times}，至少为 1")
    if click_interval_ms < 0:
        raise ValueError(f"非法点击间隔: {click_interval_ms}，不能为负数")
    if x is not None and y is not None:
        move_to(x, y)
    down, up = BUTTON_FLAGS[button]
    for i in range(times):
        _send_inputs([(0, 0, down, 0), (0, 0, up, 0)])
        if i < times - 1:
            time.sleep(click_interval_ms / 1000)


def double_click(x: int | None = None, y: int | None = None) -> None:
    """双击左键。"""
    click(x, y, button="left", times=2)


def right_click(x: int | None = None, y: int | None = None) -> None:
    """右键单击。"""
    click(x, y, button="right")


def scroll(
    delta: int,
    x: int | None = None,
    y: int | None = None,
    horizontal: bool = False,
) -> None:
    """滚动滚轮。delta>0 向上（或向右）、delta<0 向下（或向左），120 为一格。

    x/y 可选：同时给出时先移动到该位置再滚动（定点滚动）；
    只给其中一个坐标会抛错，避免歧义。
    """
    if delta == 0:
        return
    if (x is None) != (y is None):
        raise ValueError("定点滚动必须同时提供 x 与 y")
    if x is not None and y is not None:
        move_to(x, y)
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send_inputs([(0, 0, flags, delta)])


def button_down(button: str = "left", x: int | None = None, y: int | None = None) -> None:
    """按下鼠标按钮（不自动松开）。x/y 可选，同时给出时先移动到该位置。"""
    _validate_button(button)
    if (x is None) != (y is None):
        raise ValueError("必须同时提供 x 与 y")
    if x is not None and y is not None:
        move_to(x, y)
    down, _ = BUTTON_FLAGS[button]
    _send_inputs([(0, 0, down, 0)])
    with _buttons_lock:
        _held_buttons.add(button)


def button_up(button: str = "left") -> None:
    """松开鼠标按钮。"""
    _validate_button(button)
    _, up = BUTTON_FLAGS[button]
    _send_inputs([(0, 0, up, 0)])
    with _buttons_lock:
        _held_buttons.discard(button)


def held_buttons() -> set[str]:
    """当前被按住的鼠标按钮（线程安全）。"""
    with _buttons_lock:
        return set(_held_buttons)


def release_all_buttons() -> list[str]:
    """释放所有被按住的鼠标按钮，返回成功释放的按钮名列表。"""
    with _buttons_lock:
        held = sorted(_held_buttons)
    released: list[str] = []
    for button in held:
        try:
            button_up(button)
            released.append(button)
        except OSError:
            continue
    return released
