"""键盘引擎：基于 SendInput 的系统级键盘输入。

覆盖：Unicode 文本输入（中文、emoji）、组合快捷键（Ctrl+C 等）、
单键点按 / 长按，以及按键状态表 held_keys（供急停时统一释放）。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from safety import panic

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# 常用虚拟键码
VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "menu": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "lwin": 0x5B,
    "rwin": 0x5C,
    "numlock": 0x90,
    "scrolllock": 0x91,
}

# F1..F24
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i
# 0..9、a..z
for _i in range(10):
    VK[str(_i)] = ord("0") + _i
for _ch in "abcdefghijklmnopqrstuvwxyz":
    VK[_ch] = ord(_ch.upper())
# 常用标点（按美式键盘布局的 VK_OEM 码）
VK.update(
    {
        ";": 0xBA,
        "=": 0xBB,
        ",": 0xBC,
        "-": 0xBD,
        ".": 0xBE,
        "/": 0xBF,
        "`": 0xC0,
        "[": 0xDB,
        "\\": 0xDC,
        "]": 0xDD,
        "'": 0xDE,
    }
)

# 需要 KEYEVENTF_EXTENDEDKEY 标志的键（区分主键盘区与数字小键盘区）
_EXTENDED_KEYS = {
    0x2D,  # Insert
    0x2E,  # Delete
    0x21,  # PageUp
    0x22,  # PageDown
    0x23,  # End
    0x24,  # Home
    0x25,  # Left
    0x26,  # Up
    0x27,  # Right
    0x28,  # Down
    0x5C,  # Right Win
}

_held: dict[int, str] = {}
_lock = threading.Lock()


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    class _INPUTUNION(ctypes.Union):
        # 必须包含全部三种输入结构，使 sizeof(INPUT) 与 Windows 定义一致（x64 为 40 字节）
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send_keyboard(events: list[tuple[int, int, int]]) -> None:
    """发送一组键盘输入。events: [(wVk, wScan, dwFlags), ...]"""
    inputs = []
    for vk, scan, flags in events:
        ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        inputs.append(INPUT(INPUT_KEYBOARD, INPUT._INPUTUNION(ki=ki)))
    array = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(array), array, ctypes.sizeof(INPUT))
    if sent != len(array):
        raise ctypes.WinError(ctypes.get_last_error())


def _resolve_key(name: str) -> int:
    key = name.lower()
    if key in VK:
        return VK[key]
    raise ValueError(f"非法按键名: {name}，可用按键见 KEY_NAMES")


def _key_event(vk: int, keyup: bool = False) -> tuple[int, int, int]:
    """生成键盘事件。使用扫描码方式（KEYEVENTF_SCANCODE），兼容性最好。

    实测本机 Tk/部分应用对纯 VK 码（如 VK_RETURN，wScan=0）无响应，
    而真实键盘事件均为扫描码形态；快捷键/单键统一走扫描码。
    """
    flags = KEYEVENTF_SCANCODE
    if vk in _EXTENDED_KEYS:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    scan = user32.MapVirtualKeyW(vk, 0)
    return (0, scan, flags)


def key_down(name: str) -> None:
    """按下按键并登记到 held_keys。"""
    vk = _resolve_key(name)
    with _lock:
        _held[vk] = name
    _send_keyboard([_key_event(vk)])


def key_up(name: str) -> None:
    """松开按键并从 held_keys 移除。"""
    vk = _resolve_key(name)
    with _lock:
        _held.pop(vk, None)
    _send_keyboard([_key_event(vk, keyup=True)])


def _raise_if_interrupted(action: str) -> None:
    """急停标志已置位时立即中止（长按/快捷键/文本输入循环）。"""
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


def key_press(name: str, hold_ms: int = 0) -> None:
    """点按单个按键；hold_ms > 0 时为长按（保持期可被急停中断）。"""
    key_down(name)
    try:
        if hold_ms > 0:
            _sleep_interruptible(hold_ms / 1000, f"{name} 长按被急停中断")
    finally:
        key_up(name)


def hotkey(keys: list[str], hold_ms: int = 0) -> None:
    """组合快捷键：按顺序按下、短暂保持、逆序松开（如 ["ctrl","c"]）；保持期可被急停中断。"""
    if not keys:
        raise ValueError("快捷键至少需要一个按键")
    for name in keys:
        key_down(name)
    try:
        _sleep_interruptible((hold_ms / 1000) if hold_ms > 0 else 0.015, "快捷键被急停中断")
    finally:
        for name in reversed(keys):
            key_up(name)


def _utf16_units(ch: str) -> list[int]:
    """单个字符 → UTF-16 码元列表（emoji 等超出 BMP 的字符拆为代理对）。"""
    code = ord(ch)
    if code < 0x10000:
        return [code]
    code -= 0x10000
    return [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]


def type_text(text: str, interval_ms: int = 0, use_clipboard: bool = False) -> None:
    """输入文本（支持中文、emoji）；每字符间隔可被急停中断。

    use_clipboard=True 时通过剪贴板 + Ctrl+V 加速大段文本。
    """
    if not text:
        return
    if use_clipboard:
        _set_clipboard_text(text)
        hotkey(["ctrl", "v"])
        return
    for i, ch in enumerate(text):
        _raise_if_interrupted("文本输入被急停中断")
        for unit in _utf16_units(ch):
            _send_keyboard(
                [
                    (0, unit, KEYEVENTF_UNICODE),
                    (0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                ]
            )
        if interval_ms > 0 and i < len(text) - 1:
            _sleep_interruptible(interval_ms / 1000, "文本输入被急停中断")


def _set_clipboard_text(text: str) -> None:
    """把文本写入系统剪贴板（CF_UNICODETEXT）。"""
    if not user32.OpenClipboard(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        hmem = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not hmem:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ptr = kernel32.GlobalLock(hmem)
            if not ptr:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(hmem)
            if not user32.SetClipboardData(13, hmem):  # CF_UNICODETEXT
                raise ctypes.WinError(ctypes.get_last_error())
            hmem = None  # 所有权已移交给系统，不再释放
        finally:
            if hmem:
                kernel32.GlobalFree(hmem)
    finally:
        user32.CloseClipboard()


def held_keys() -> dict[str, str]:
    """当前被按住的按键表快照 {虚拟键码: 按键名}（线程安全）。"""
    with _lock:
        return dict(_held)


def release_all() -> list[str]:
    """释放所有被按住的按键（逆序松开），返回成功释放的按键名列表。"""
    with _lock:
        held = list(_held.items())
        _held.clear()
    released: list[str] = []
    for vk, name in reversed(held):
        try:
            _send_keyboard([_key_event(vk, keyup=True)])
            released.append(name)
        except OSError:
            # 单个按键释放失败不阻断其余按键的释放
            continue
    return released
