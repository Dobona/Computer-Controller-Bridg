"""UIAutomation 元素树读取（comtypes 实现）。

返回按钮/输入框/菜单等可交互元素：名称、类型、坐标（物理像素）、可用状态。
UIA 不可用时自动降级（ok=False），不影响其他功能。
"""

from __future__ import annotations

import ctypes

_UIA = None
_AUTOMATION = None
_last_error: str | None = None

# UIA ControlType 常量 → 友好名称
CONTROL_TYPE_NAMES = {
    50000: "button",
    50001: "calendar",
    50002: "checkbox",
    50003: "combobox",
    50004: "edit",
    50005: "hyperlink",
    50006: "image",
    50007: "list_item",
    50008: "list",
    50009: "menu",
    50010: "menu_bar",
    50011: "menu_item",
    50012: "progress_bar",
    50013: "radio_button",
    50014: "scroll_bar",
    50015: "slider",
    50016: "spinner",
    50017: "status_bar",
    50018: "tab",
    50019: "tab_item",
    50020: "text",
    50021: "tool_bar",
    50022: "tool_tip",
    50023: "tree",
    50024: "tree_item",
    50025: "custom",
    50026: "group",
    50027: "thumb",
    50028: "data_grid",
    50029: "data_item",
    50030: "document",
    50031: "split_button",
    50032: "window",
    50033: "pane",
    50034: "header",
    50035: "header_item",
    50036: "table",
    50037: "title_bar",
    50038: "separator",
    50039: "semantic_zoom",
    50040: "app_bar",
}


def _import_uia():
    global _UIA
    if _UIA is None:
        # comtypes 在导入时会自动初始化当前线程的 COM（公寓线程模型），
        # 不要再手动调用 CoInitializeEx，否则会因线程模式冲突报 RPC_E_CHANGED_MODE。
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        _UIA = UIA
    return _UIA


def _get_automation():
    global _AUTOMATION
    if _AUTOMATION is None:
        import comtypes

        UIA = _import_uia()
        _AUTOMATION = comtypes.client.CreateObject(UIA.CUIAutomation)
    return _AUTOMATION


def available() -> bool:
    """UIA 是否可用（引擎能否初始化）。"""
    try:
        _get_automation()
        return True
    except Exception as exc:
        _set_last_error(str(exc))
        return False


def last_error() -> str | None:
    """最近一次失败原因（引擎初始化或查询失败时记录），供自检/日志使用。"""
    return _last_error


def _set_last_error(error: str | None) -> None:
    global _last_error
    _last_error = (error or "")[:500] or None


def _rect_to_list(rect) -> list[int]:
    try:
        return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    except Exception:
        return [0, 0, 0, 0]


def element_to_dict(el) -> dict:
    """单个 UIA 元素 → 结构化 dict（解析失败返回空 dict）。"""
    try:
        rect = _rect_to_list(el.CurrentBoundingRectangle)
        return {
            "name": str(el.CurrentName or ""),
            "type": CONTROL_TYPE_NAMES.get(int(el.CurrentControlType), "other"),
            "control_type_id": int(el.CurrentControlType),
            "rect": rect,
            "enabled": bool(el.CurrentIsEnabled),
            "automation_id": str(el.CurrentAutomationId or ""),
            "class": str(el.CurrentClassName or ""),
        }
    except Exception:
        return {}


def get_elements(scope_hwnd: int | None = None, max_elements: int = 300) -> dict:
    """返回 UIA 元素列表。scope_hwnd 非空时只读取该窗口；失败时降级 ok=False。"""
    try:
        au = _get_automation()
        if scope_hwnd is None:
            root = au.GetRootElement()
        else:
            root = au.ElementFromHandle(ctypes.c_void_p(scope_hwnd))
        cond = au.CreateTrueCondition()
        found = root.FindAll(4, cond)  # TreeScope_Descendants
        elements: list[dict] = []
        for i in range(found.Length):
            if len(elements) >= max_elements:
                break
            d = element_to_dict(found.GetElement(i))
            if not d or not d["name"]:
                continue
            left, top, right, bottom = d["rect"]
            if right <= left or bottom <= top:
                continue
            elements.append(d)
        return {"ok": True, "elements": elements}
    except Exception as exc:
        _set_last_error(str(exc))
        return {"ok": False, "elements": [], "error": str(exc)}
