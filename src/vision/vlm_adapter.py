"""外接 VLM 适配器（OpenAI 兼容 chat/completions，适配硅基流动 SiliconFlow）。

本机不运行任何视觉模型；经配置的 HTTP 端点调用外部视觉服务（默认硅基流动
Qwen/Qwen3-VL-30B-A3B-Instruct）。支持：
- describe：全屏 / 区域整体描述；
- locate_objects：结构化目标 / 图标识别（要求模型返回 JSON bbox，坐标为图片像素，
  可由调用方叠加区域偏移换算为虚拟桌面坐标）。

接口契约（SiliconFlow 多模态接口）：
POST {endpoint}
Authorization: Bearer {api_key}
{
  "model": "Qwen/Qwen3-VL-30B-A3B-Instruct",
  "messages": [{"role": "user", "content": [
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
      {"type": "text", "text": "请描述..."}
  ]}],
  "max_tokens": 1024,
  "temperature": 0.1
}
→ {"choices": [{"message": {"content": "..."}}]}

隐私提醒：启用后屏幕截图会被发送到外部端点，仅应在明确知情后启用。
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_MODEL = "Qwen/Qwen3-VL-30B-A3B-Instruct"


def is_configured(endpoint: str | None, api_key: str | None, model: str | None) -> bool:
    """端点、密钥、模型三者齐备才算已配置。"""
    return bool(
        endpoint
        and endpoint.strip()
        and api_key
        and api_key.strip()
        and model
        and model.strip()
    )


def _data_url(image_png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(image_png).decode("ascii")


def _build_payload(
    model: str,
    image_png: bytes,
    prompt: str,
    max_tokens: int = 1024,
    temperature: float = 0.1,
) -> dict:
    """构造 OpenAI 兼容 chat/completions 请求体。"""
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _data_url(image_png), "detail": "auto"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


def _post(
    endpoint: str,
    api_key: str,
    model: str,
    image_png: bytes,
    prompt: str,
    max_tokens: int = 1024,
    timeout_s: int = 60,
) -> dict:
    """发送请求并返回响应 JSON；失败抛异常（由调用方转译为 ok=false）。"""
    payload = _build_payload(model, image_png, prompt, max_tokens=max_tokens)
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            pass
        raise RuntimeError(f"VLM 端点返回 HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"VLM 端点连接失败: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"VLM 端点调用超时（>{timeout_s}s）") from exc


def _extract_text(response: dict) -> str | None:
    """从 OpenAI 兼容响应中提取文本内容。"""
    try:
        content = response["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else str(content)
    except (KeyError, IndexError, TypeError):
        return None


def describe(
    endpoint: str,
    api_key: str,
    model: str,
    image_png: bytes,
    prompt: str | None = None,
    timeout_s: int = 60,
    max_tokens: int = 1024,
) -> dict:
    """对截图（或已裁剪区域）做整体描述。"""
    if not is_configured(endpoint, api_key, model):
        return {
            "ok": False,
            "message": "未配置 VLM：请在 config.json 的 screen.vlm 中配置 endpoint/api_key/model"
            "（注意：启用后截图会发送到该端点）。",
        }
    text_prompt = prompt or "请用中文简要描述这张屏幕截图：整体布局、主要窗口/应用、可见的显著元素与文字。"
    try:
        response = _post(
            endpoint, api_key, model, image_png, text_prompt,
            max_tokens=max_tokens, timeout_s=timeout_s,
        )
    except Exception as exc:
        return {"ok": False, "message": f"调用 VLM 端点失败: {exc}"}
    text = _extract_text(response)
    if not text:
        return {"ok": False, "message": "VLM 端点未返回有效文本", "data": response}
    return {
        "ok": True,
        "text": text,
        "data": {
            "model": response.get("model"),
            "usage": response.get("usage"),
        },
    }


def _extract_json_block(text: str):
    """从模型输出中提取第一个 JSON 对象/数组（容忍 Markdown 代码块与前缀文字）。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def locate_objects(
    endpoint: str,
    api_key: str,
    model: str,
    image_png: bytes,
    image_size: tuple[int, int],
    offset: tuple[int, int] = (0, 0),
    prompt: str | None = None,
    timeout_s: int = 60,
    max_tokens: int = 1024,
) -> dict:
    """结构化目标 / 图标识别：要求模型返回 JSON bbox，并叠加 offset 换算为虚拟桌面坐标。

    返回 {ok, text, objects, parsed}；objects 形如 [{"name","box":[x1,y1,x2,y2]}]。
    API 成功但 JSON 解析失败时 ok=True、parsed=False、objects=[]（原始文本保留）。
    """
    if not is_configured(endpoint, api_key, model):
        return {
            "ok": False,
            "message": "未配置 VLM：请在 config.json 的 screen.vlm 中配置 endpoint/api_key/model。",
        }
    w, h = image_size
    extra = f"\n附加要求：{prompt}" if prompt else ""
    text_prompt = (
        f"请识别这张屏幕截图中最重要的可交互界面元素（按钮/图标/输入框/窗口），最多 15 个，按重要性降序。"
        f"只输出一个 JSON 对象，不要 Markdown，不要额外文字："
        f'{{"objects": [{{"name": "元素名称", "box": [x1, y1, x2, y2]}}]}}'
        f"坐标请使用 0~1000 的归一化坐标（图片左上角为 (0,0)，右下角为 (1000,1000)），"
        f"box 为元素外接矩形。"
        f"{extra}"
    )
    try:
        response = _post(
            endpoint, api_key, model, image_png, text_prompt,
            max_tokens=max_tokens, timeout_s=timeout_s,
        )
    except Exception as exc:
        return {"ok": False, "message": f"调用 VLM 端点失败: {exc}"}
    text = _extract_text(response)
    if not text:
        return {"ok": False, "message": "VLM 端点未返回有效文本", "data": response}

    parsed = False
    objects: list[dict] = []
    data = _extract_json_block(text)
    if isinstance(data, dict) and isinstance(data.get("objects"), list):
        items = [o for o in data["objects"] if isinstance(o, dict) and o.get("name") and isinstance(o.get("box"), list) and len(o["box"]) == 4]
        if items:
            parsed = True
            dx, dy = offset
            # Qwen 系列视觉模型默认输出 0~1000 归一化坐标，换算回图片像素
            sx = lambda v: round(int(v) / 1000 * w)
            sy = lambda v: round(int(v) / 1000 * h)
            for item in items:
                x1, y1, x2, y2 = item["box"]
                try:
                    objects.append(
                        {
                            "name": str(item["name"]),
                            "box": [sx(x1) + dx, sy(y1) + dy, sx(x2) + dx, sy(y2) + dy],
                        }
                    )
                except (TypeError, ValueError):
                    continue
    return {
        "ok": True,
        "text": text,
        "objects": objects,
        "parsed": parsed,
        "data": {"model": response.get("model"), "usage": response.get("usage")},
    }
