"""屏幕理解聚合：窗口 + UIA 元素 + OCR 文本 → 结构化 JSON + 简短摘要。

注意：with_uia/with_ocr 为 False 表示调用方未请求该层，与「请求了但不可用」
（降级）是不同的状态，返回的 summary/degraded/reasons 会明确区分两者。
"""

from __future__ import annotations

import time

from core import coords, ocr, screen, uia, windows


def _build_summary(
    wins, elements, texts, uia_ok, ocr_ok, uia_enabled, ocr_enabled, uia_reason, ocr_reason
) -> str:
    parts: list[str] = []
    active = next((w for w in wins if w.get("active")), None)
    if active:
        parts.append(f"前台窗口为「{active['title']}」")
    elif wins:
        parts.append(f"共 {len(wins)} 个窗口")
    if not uia_enabled:
        parts.append("UIA 未启用")
    elif uia_ok:
        by_type: dict[str, int] = {}
        for e in elements:
            t = e.get("type", "other")
            by_type[t] = by_type.get(t, 0) + 1
        if by_type:
            type_desc = "、".join(f"{n} 个{v}" for v, n in sorted(by_type.items(), key=lambda x: -x[1]))
            parts.append(f"可交互元素 {len(elements)} 个（{type_desc}）")
    else:
        reason = f"（原因：{uia_reason}）" if uia_reason else "（已降级）"
        parts.append(f"UIA 不可用{reason}")
    if not ocr_enabled:
        parts.append("OCR 未启用")
    elif ocr_ok:
        parts.append(f"识别到文本 {len(texts)} 处")
    else:
        reason = f"（原因：{ocr_reason}）" if ocr_reason else "（已降级）"
        parts.append(f"OCR 不可用{reason}")
    return "；".join(parts) + "。"


def analyze(scope: str = "full", with_uia: bool = True, with_ocr: bool = True) -> dict:
    """结构化屏幕理解。

    scope: full=全屏；window=仅前台窗口区域。所有坐标均为物理像素虚拟桌面坐标系，
    可直接用于鼠标工具。
    """
    t0 = time.perf_counter()
    vx, vy, vw, vh = coords.virtual_screen()

    wins = windows.list_windows(with_title_only=True, visible_only=True)
    active = next((w for w in wins if w.get("active")), None)
    scope_rect = active["rect"] if scope == "window" and active else None

    uia_result = {"ok": False, "elements": [], "error": None}
    uia_error: str | None = None
    if with_uia:
        targets = (
            [active["hwnd"]]
            if scope == "window" and active
            else [w["hwnd"] for w in wins]
        )
        all_elements: list[dict] = []
        uia_ok = False
        for hwnd in targets:
            r = uia.get_elements(scope_hwnd=hwnd, max_elements=200)
            if r.get("ok"):
                uia_ok = True
            elif not uia_error:
                uia_error = str(r.get("error") or "")[:200] or None
            all_elements.extend(r.get("elements", []))
        uia_result = {"ok": uia_ok, "elements": all_elements, "error": uia_error}

    ocr_result = {"ok": False, "texts": [], "error": None}
    ocr_error: str | None = None
    if with_ocr:
        if scope == "window" and scope_rect:
            img = screen.grab_region(tuple(scope_rect))
            ocr_result = ocr.ocr_image(img, offset_x=scope_rect[0], offset_y=scope_rect[1])
            ocr_error = str(ocr_result.get("error") or "")[:200] or None
            wins = [active] if active else []
            uia_result["elements"] = [
                e
                for e in uia_result.get("elements", [])
                if _rect_intersect(e.get("rect"), scope_rect)
            ]
        else:
            img = screen.grab()
            ocr_result = ocr.ocr_image(img, offset_x=vx, offset_y=vy)
            ocr_error = str(ocr_result.get("error") or "")[:200] or None

    elements = uia_result.get("elements", [])
    texts = ocr_result.get("texts", [])
    uia_ok = uia_result.get("ok", False)
    ocr_ok = ocr_result.get("ok", False)
    elements = _dedupe(elements)

    return {
        "summary": _build_summary(
            wins, elements, texts, uia_ok, ocr_ok, with_uia, with_ocr, uia_error, ocr_error
        ),
        "screen": {"virtual": [vx, vy, vw, vh]},
        "windows": wins,
        "elements": elements,
        "texts": texts,
        # degraded 只表示「请求了但不可用」；未请求的层用 enabled 标记
        "degraded": {"uia": with_uia and not uia_ok, "ocr": with_ocr and not ocr_ok},
        "enabled": {"uia": with_uia, "ocr": with_ocr},
        "reasons": {"uia": uia_error, "ocr": ocr_error},
        "duration_ms": int((time.perf_counter() - t0) * 1000),
    }


def _rect_intersect(a: list[int], b: list[int]) -> bool:
    if not a or not b or len(a) != 4 or len(b) != 4:
        return False
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1


def _dedupe(elements: list[dict]) -> list[dict]:
    """按 (name, type, rect) 去重（跨窗口查询可能重复）。"""
    seen: set[tuple] = set()
    result: list[dict] = []
    for e in elements:
        key = (e.get("name"), e.get("type"), tuple(e.get("rect", [])))
        if key in seen:
            continue
        seen.add(key)
        result.append(e)
    return result
