"""外接 VLM 适配器单元测试（mock HTTP，OpenAI 兼容 chat/completions）。"""

from __future__ import annotations

import base64
import json

import pytest
import urllib.error

from vision import vlm_adapter

EP = "https://api.siliconflow.cn/v1/chat/completions"
KEY = "sk-test"
MODEL = "Qwen/Qwen3-VL-30B-A3B-Instruct"


def _fake_response(payload: dict):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return FakeResponse()


def test_not_configured():
    for args in [
        ("", KEY, MODEL),
        (EP, "", MODEL),
        (EP, KEY, ""),
    ]:
        result = vlm_adapter.describe(*args, b"png")
        assert result["ok"] is False
        assert "未配置 VLM" in result["message"]


def test_describe_success(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_response(
            {"model": MODEL, "choices": [{"message": {"content": "屏幕上有一个计算器窗口"}}], "usage": {"total_tokens": 100}}
        )

    monkeypatch.setattr(vlm_adapter.urllib.request, "urlopen", fake_urlopen)
    result = vlm_adapter.describe(EP, KEY, MODEL, b"PNGDATA", prompt="描述屏幕")
    assert result["ok"] is True
    assert result["text"] == "屏幕上有一个计算器窗口"
    assert result["data"]["model"] == MODEL
    assert captured["url"] == EP
    assert captured["auth"] == f"Bearer {KEY}"
    content = captured["body"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()
    assert content[1]["text"] == "描述屏幕"
    assert captured["body"]["model"] == MODEL
    assert captured["body"]["max_tokens"] == 1024


def test_describe_no_content(monkeypatch):
    monkeypatch.setattr(
        vlm_adapter.urllib.request,
        "urlopen",
        lambda req, timeout=60: _fake_response({"choices": [{"message": {"content": ""}}]}),
    )
    result = vlm_adapter.describe(EP, KEY, MODEL, b"PNGDATA")
    assert result["ok"] is False
    assert "未返回有效文本" in result["message"]


def test_describe_http_error(monkeypatch):
    def boom(req, timeout=60):
        raise urllib.error.HTTPError(EP, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(vlm_adapter.urllib.request, "urlopen", boom)
    result = vlm_adapter.describe(EP, KEY, MODEL, b"PNGDATA")
    assert result["ok"] is False
    assert "HTTP 401" in result["message"]


def test_describe_timeout(monkeypatch):
    def boom(req, timeout=60):
        raise TimeoutError("timeout")

    monkeypatch.setattr(vlm_adapter.urllib.request, "urlopen", boom)
    result = vlm_adapter.describe(EP, KEY, MODEL, b"PNGDATA", timeout_s=5)
    assert result["ok"] is False
    assert "超时" in result["message"]


def test_locate_objects_success(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"objects": [{"name": "计算器", "box": [100, 200, 300, 400]}]}\n```'
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(vlm_adapter.urllib.request, "urlopen", fake_urlopen)
    result = vlm_adapter.locate_objects(
        EP, KEY, MODEL, b"PNGDATA", image_size=(1000, 800), offset=(50, 30)
    )
    assert result["ok"] is True
    assert result["parsed"] is True
    # 0~1000 归一化坐标换算：x1=100→100px, y1=200→160px, x2=300→300px, y2=400→320px，再叠加 offset
    assert result["objects"] == [{"name": "计算器", "box": [150, 190, 350, 350]}]
    prompt = captured["body"]["messages"][0]["content"][1]["text"]
    assert "最多 15 个" in prompt
    assert '"objects"' in prompt


def test_locate_objects_parse_failure_keeps_text(monkeypatch):
    monkeypatch.setattr(
        vlm_adapter.urllib.request,
        "urlopen",
        lambda req, timeout=60: _fake_response(
            {"choices": [{"message": {"content": "我看到了一个按钮。"}}]}
        ),
    )
    result = vlm_adapter.locate_objects(EP, KEY, MODEL, b"PNGDATA", image_size=(800, 600))
    assert result["ok"] is True
    assert result["parsed"] is False
    assert result["objects"] == []
    assert result["text"] == "我看到了一个按钮。"
