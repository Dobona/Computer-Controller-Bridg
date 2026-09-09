"""工具参数 Schema、统一返回结构与错误转译。

统一返回结构：{ok, message, data, duration_ms}（screenshot 等图片工具除外）。
"""

from __future__ import annotations

import functools
import time

from core.action import ActionInterrupted, ActionTimeout
from core.keyboard import _resolve_key
from safety import panic
from safety.audit import audit_log

MOUSE_BUTTONS = ("left", "right", "middle")
SCOPES = ("auto", "full", "window")


def make_result(ok: bool, message: str, data=None, t0: float | None = None) -> dict:
    """构造统一返回结构。"""
    result: dict = {"ok": ok, "message": message, "data": data}
    if t0 is not None:
        result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    return result


def safe_tool(func=None, *, allow_during_panic: bool = False):
    """工具装饰器：急停拦截 + 异常转译 + 补充 duration_ms。

    支持 @safe_tool 与 @safe_tool(allow_during_panic=True) 两种用法。
    """

    def decorate(target):
        @functools.wraps(target)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            if panic.interrupted() and not allow_during_panic:
                return make_result(
                    False,
                    "急停状态中：请先复位（重启服务或由安全层复位）后再执行新动作",
                    None,
                    t0,
                )
            try:
                result = target(*args, **kwargs)
                if isinstance(result, dict) and "duration_ms" not in result:
                    result["duration_ms"] = int((time.perf_counter() - t0) * 1000)
                return result
            except ActionInterrupted as exc:
                result = make_result(False, str(exc), None, t0)
                return result
            except ActionTimeout as exc:
                result = make_result(False, str(exc), None, t0)
                return result
            except ValueError as exc:
                return make_result(False, f"参数错误: {exc}", None, t0)
            except Exception as exc:
                return make_result(False, f"执行失败: {exc}", None, t0)
            finally:
                audit_log(
                    target.__name__,
                    kwargs if kwargs else list(args),
                    locals().get("result"),
                )

        return wrapper

    if func is not None:
        return decorate(func)
    return decorate


def validate_button(button: str) -> None:
    if button not in MOUSE_BUTTONS:
        raise ValueError(f"非法按键: {button}，可选 {list(MOUSE_BUTTONS)}")


def validate_key_name(key: str) -> None:
    _resolve_key(key)


def validate_keys(keys) -> None:
    if not isinstance(keys, list) or not keys:
        raise ValueError("keys 必须是非空列表，如 [\"ctrl\", \"c\"]")
    for key in keys:
        validate_key_name(str(key))


def validate_text(text, max_len: int = 10000) -> None:
    if not isinstance(text, str) or not text:
        raise ValueError("text 必须是非空字符串")
    if len(text) > max_len:
        raise ValueError(f"text 过长（{len(text)} 字符），最大 {max_len} 字符")


def validate_ms(name: str, value, minimum: int = 0, maximum: int = 600000) -> None:
    if not isinstance(value, int) or value < minimum or value > maximum:
        raise ValueError(f"{name} 必须是 {minimum}~{maximum} 之间的整数，实际为 {value!r}")


def validate_scope(scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"scope 可选 {list(SCOPES)}，实际为 {scope!r}")


def validate_region(region) -> None:
    if not isinstance(region, list) or len(region) != 4:
        raise ValueError("region 必须是 [x1, y1, x2, y2] 四个整数")
    x1, y1, x2, y2 = region
    if not all(isinstance(v, int) for v in region):
        raise ValueError("region 各分量必须是整数")
    if x1 >= x2 or y1 >= y2:
        raise ValueError(f"region 必须满足 x1<x2 且 y1<y2，实际为 {region}")
