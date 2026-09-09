"""本地 OCR：RapidOCR（ONNX 推理，支持中文）。

输入 PIL 截图，输出文本 + 坐标（物理像素，可加偏移与虚拟桌面原点对齐）。
OCR 不可用（缺模型/导入失败）时降级返回 ok=False，不影响其他功能。
"""

from __future__ import annotations

import logging

_engine = None
_last_error: str | None = None


def _get_engine():
    global _engine
    if _engine is None:
        logging.getLogger("rapidocr").setLevel(logging.WARNING)
        from rapidocr import RapidOCR

        _engine = RapidOCR()
    return _engine


def available() -> bool:
    """OCR 引擎是否可用。"""
    try:
        _get_engine()
        return True
    except Exception as exc:
        _set_last_error(str(exc))
        return False


def last_error() -> str | None:
    """最近一次失败原因（引擎初始化或识别失败时记录），供自检/日志使用。"""
    return _last_error


def _set_last_error(error: str | None) -> None:
    global _last_error
    _last_error = (error or "")[:500] or None


def _parse_result(result) -> list[dict]:
    """把 RapidOCR 输出解析为 [{text, rect, score}]。

    rapidocr 3.x 返回对象（.txts/.boxes/.scores），旧版本返回 dict，两种都兼容。
    """
    if result is None:
        return []
    if hasattr(result, "txts"):
        txts = list(result.txts)
        boxes = list(result.boxes)
        scores = list(result.scores)
    elif isinstance(result, dict):
        txts = list(result.get("txts") or [])
        boxes = list(result.get("boxes") or [])
        scores = list(result.get("scores") or [])
    else:
        return []
    texts: list[dict] = []
    for txt, box, score in zip(txts, boxes, scores):
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        texts.append(
            {
                "text": str(txt),
                "rect": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
                "score": round(float(score), 4),
            }
        )
    return texts


def ocr_image(image, offset_x: int = 0, offset_y: int = 0) -> dict:
    """识别 PIL Image，返回 {ok, texts, error?}；offset 用于对齐虚拟桌面原点。"""
    try:
        engine = _get_engine()
        result = engine(image)
        texts = _parse_result(result)
        for t in texts:
            t["rect"][0] += offset_x
            t["rect"][1] += offset_y
            t["rect"][2] += offset_x
            t["rect"][3] += offset_y
        return {"ok": True, "texts": texts}
    except Exception as exc:
        _set_last_error(str(exc))
        return {"ok": False, "texts": [], "error": str(exc)}
