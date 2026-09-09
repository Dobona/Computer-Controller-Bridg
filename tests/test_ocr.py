"""OCR 单元测试：结果解析、坐标偏移、降级路径（mock 引擎）。"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from core import ocr


def _fake_engine_result():
    """模拟 rapidocr 3.x 返回对象：txts/boxes/scores。"""
    boxes = np.array(
        [
            [[12.0, 31.0], [109.0, 31.0], [110.0, 88.0], [13.0, 88.0]],
            [[277.0, 32.0], [377.0, 32.0], [377.0, 83.0], [277.0, 83.0]],
        ],
        dtype=np.float32,
    )
    return SimpleNamespace(txts=("你好", "OCR"), boxes=boxes, scores=(0.9999, 0.9998))


def test_parse_attribute_style():
    texts = ocr._parse_result(_fake_engine_result())
    assert len(texts) == 2
    assert texts[0]["text"] == "你好"
    assert texts[0]["rect"] == [12, 31, 110, 88]
    assert texts[0]["score"] == 0.9999


def test_parse_dict_style():
    fake = {"txts": ["abc"], "boxes": [np.array([[0, 0], [10, 0], [10, 5], [0, 5]], dtype=np.float32)], "scores": [0.9]}
    texts = ocr._parse_result(fake)
    assert texts[0]["text"] == "abc"
    assert texts[0]["rect"] == [0, 0, 10, 5]


def test_parse_none():
    assert ocr._parse_result(None) == []


def test_ocr_image_offset(monkeypatch):
    class _FakeEngine:
        def __call__(self, img):
            return _fake_engine_result()

    monkeypatch.setattr(ocr, "_get_engine", lambda: _FakeEngine())
    result = ocr.ocr_image(object(), offset_x=100, offset_y=200)
    assert result["ok"] is True
    assert result["texts"][0]["rect"] == [112, 231, 210, 288]  # 加了虚拟桌面偏移


def test_ocr_image_failure_degrades(monkeypatch):
    def boom(_img):
        raise RuntimeError("模型缺失")

    class _BoomEngine:
        def __call__(self, img):
            raise RuntimeError("模型缺失")

    monkeypatch.setattr(ocr, "_get_engine", lambda: _BoomEngine())
    result = ocr.ocr_image(object())
    assert result["ok"] is False
    assert result["texts"] == []
    assert "error" in result
