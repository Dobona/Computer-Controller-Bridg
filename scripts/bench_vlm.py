"""VLM 模型对比基准：在同一张真实截图上测各候选模型的延迟 / 命中 / 成本。

运行方式：
    .venv/Scripts/python.exe scripts/bench_vlm.py

说明：
- 需要 config.json 已配置 screen.vlm（api_key）；
- 会真实调用外部 VLM（每个模型 × 每个目标一次，默认 4 模型 × 2 目标，约 8 次）；
- 命中判定：模型返回的 box 中心落在本地 OCR 对目标文本的框内；
- 成本按硅基流动官网（人民币 / 百万 token）估算，仅供选型参考。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "src"))

from core import ocr, screen  # noqa: E402
from server import config as config_mod  # noqa: E402
from vision import vlm_adapter  # noqa: E402

# 硅基流动（api.siliconflow.cn）人民币每百万 token 价格，仅用于估算
PRICES_CNY = {
    "Qwen/Qwen3-VL-8B-Instruct": (0.5, 2.0),
    "Qwen/Qwen3-VL-30B-A3B-Instruct": (0.7, 2.8),
    "Qwen/Qwen3-VL-32B-Instruct": (1.0, 4.0),
    "PaddlePaddle/PaddleOCR-VL-1.5": (0.0, 0.0),
}

MODELS = [
    "Qwen/Qwen3-VL-8B-Instruct",
    "Qwen/Qwen3-VL-30B-A3B-Instruct",
    "Qwen/Qwen3-VL-32B-Instruct",
    "PaddlePaddle/PaddleOCR-VL-1.5",
]

TARGETS = ["文件", "开始"]


def _center(box) -> tuple[int, int]:
    return ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)


def _in_rect(point, rect) -> bool:
    x, y = point
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]


def main() -> int:
    cfg = config_mod.load_config()
    vlm = cfg["screen"]["vlm"]
    api_key = vlm["api_key"] or ""

    img = screen.grab()
    ocr_res = ocr.ocr_image(img)
    assert ocr_res.get("ok"), ocr_res
    texts = ocr_res["texts"]
    ground = {}
    for target in TARGETS:
        rects = [t["rect"] for t in texts if target in t["text"]]
        ground[target] = rects
        if rects:
            print(f"OCR 基准: 『{target}』 {len(rects)} 处（示例 {rects[0]}）")
        else:
            print(f"OCR 基准: 『{target}』 未找到（屏幕上没有该文本，跳过命中判定）")

    from io import BytesIO
    from PIL import Image as PILImage

    max_dim = 1280
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        img2 = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), PILImage.LANCZOS)
    else:
        img2 = img
    buf = BytesIO()
    img2.convert("RGB").save(buf, format="PNG")
    png = buf.getvalue()

    rows = []
    for model in MODELS:
        price = PRICES_CNY.get(model, (None, None))
        for target in TARGETS:
            prompt = (
                f"请定位截图中的文本『{target}』：把它的完整外接矩形作为 box，"
                f'name 设为"{target}"。只输出该文本，不要其他元素。'
            )
            t0 = time.perf_counter()
            try:
                res = vlm_adapter.locate_objects(
                    endpoint=vlm["endpoint"],
                    api_key=api_key,
                    model=model,
                    image_png=png,
                    image_size=img.size,
                    offset=(0, 0),
                    prompt=prompt,
                    timeout_s=90,
                    max_tokens=vlm["max_tokens"],
                )
            except Exception as exc:
                rows.append({"model": model, "target": target, "latency_s": round(time.perf_counter() - t0, 1), "hit": "ERROR", "detail": repr(exc)})
                continue
            latency = round(time.perf_counter() - t0, 1)
            usage = (res.get("data") or {}).get("usage") or {}
            in_tok = usage.get("prompt_tokens", 0)
            out_tok = usage.get("completion_tokens", 0)
            cost_cny = (in_tok / 1e6 * price[0] + out_tok / 1e6 * price[1]) if price[0] is not None else None
            hit = False
            rects = ground.get(target, [])
            if rects:
                for obj in res.get("objects", []):
                    box = obj.get("box")
                    if box and any(_in_rect(_center(box), r) for r in rects):
                        hit = True
                        break
            rows.append(
                {
                    "model": model.split("/")[-1],
                    "target": target,
                    "latency_s": latency,
                    "parsed": res.get("parsed"),
                    "objects": len(res.get("objects", [])),
                    "hit": hit if rects else "N/A",
                    "tok_in": in_tok,
                    "tok_out": out_tok,
                    "cost_cny": round(cost_cny, 6) if cost_cny is not None else 0.0,
                    "detail": res.get("text", "")[:120].replace("\n", " "),
                }
            )

    print("\n=== 结果 ===")
    print(f"{'模型':<24}{'目标':<6}{'延迟s':<8}{'解析':<6}{'元素':<6}{'命中':<6}{'tok':<8}{'成本¥':<10}")
    for r in rows:
        print(
            f"{r['model']:<24}{r['target']:<6}{r['latency_s']:<8}{str(r.get('parsed')):<6}"
            f"{r.get('objects'):<6}{str(r.get('hit')):<6}"
            f"{r.get('tok_in', 0) + r.get('tok_out', 0):<8}{r.get('cost_cny', 0):<10}"
        )
    print("\n详细返回（前 120 字符）：")
    for r in rows:
        print(f"- {r['model']} / {r['target']}: {r.get('detail', r.get('detail'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
