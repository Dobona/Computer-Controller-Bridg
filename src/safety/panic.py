"""急停安全层：中断标志 + 全局急停热键（RegisterHotKey）。

- 中断标志：request_interrupt / interrupted / clear_interrupt；
- 全局热键：默认 Ctrl+Alt+Shift+Esc，按下立即中止动作并释放按键；
  再次按下 = 复位（清除急停状态，恢复接受指令）；
- 由 start() 在独立线程注册热键并泵消息，stop() 停止。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
VK_ESC = 0x1B

_HOTKEY_ID = 0xC0DE
_MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}

_interrupt = threading.Event()
_watcher_thread: threading.Thread | None = None
_watcher_thread_id = 0
_hotkey_ok = False


def request_interrupt() -> None:
    """请求急停：置位中断标志，当前动作应尽快中止并释放按键。"""
    _interrupt.set()


def interrupted() -> bool:
    """当前是否处于急停状态。"""
    return _interrupt.is_set()


def clear_interrupt() -> None:
    """清除急停标志（复位，恢复接受新指令）。"""
    _interrupt.clear()


def toggle_interrupt() -> bool:
    """切换急停状态，返回切换后的状态（True=已急停）。"""
    if _interrupt.is_set():
        clear_interrupt()
        return False
    request_interrupt()
    return True


def _parse_hotkey(keys) -> tuple[int, int]:
    """把 ["ctrl","alt","shift","esc"] 解析为 (modifiers, vk)。"""
    mods = 0
    vk = None
    for key in keys:
        k = str(key).lower()
        if k in _MODIFIER_NAMES:
            mods |= _MODIFIER_NAMES[k]
        elif k in ("esc", "escape"):
            vk = VK_ESC
        else:
            raise ValueError(f"急停热键暂不支持按键: {key}（支持修饰键 ctrl/alt/shift/win + esc）")
    if vk is None:
        raise ValueError("急停热键必须包含一个普通按键（如 esc）")
    return mods, vk


def _release_everything() -> None:
    from core.keyboard import release_all
    from core.mouse import release_all_buttons

    release_all()
    release_all_buttons()


def _on_hotkey() -> None:
    """热键回调：首次按下急停并释放；再次按下复位。"""
    if toggle_interrupt():
        _release_everything()


def _watcher_loop(mods: int, vk: int) -> None:
    global _watcher_thread_id, _hotkey_ok
    _watcher_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    if not user32.RegisterHotKey(None, _HOTKEY_ID, mods, vk):
        error = ctypes.get_last_error()
        import logging

        _hotkey_ok = False
        logging.getLogger("safety.panic").warning(
            "急停热键注册失败（错误码 %s，通常为该组合已被其他程序或其他服务实例占用）。"
            "请关闭重复实例，或在 config.json 的 safety.panic_hotkey 更换组合；"
            "即使热键不可用，emergency_stop 工具与看门狗仍可兜底。",
            error,
        )
        return
    _hotkey_ok = True
    try:
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:  # 0=WM_QUIT, -1=错误
                break
            if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                _on_hotkey()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, _HOTKEY_ID)


def start(keys=("ctrl", "alt", "shift", "esc")) -> bool:
    """启动全局急停热键监听线程。"""
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return True
    mods, vk = _parse_hotkey(keys)
    _watcher_thread = threading.Thread(
        target=_watcher_loop, args=(mods, vk), daemon=True, name="panic-hotkey"
    )
    _watcher_thread.start()
    return True


def stop() -> None:
    """停止急停热键监听（向监听线程投递 WM_QUIT）。"""
    if _watcher_thread is not None and _watcher_thread.is_alive():
        ctypes.windll.user32.PostThreadMessageW(_watcher_thread_id, WM_QUIT, 0, 0)
        _watcher_thread.join(timeout=2)


def hotkey_ok() -> bool:
    """全局急停热键是否已成功注册（注册失败时 emergency_stop 工具仍可用）。"""
    return _hotkey_ok
