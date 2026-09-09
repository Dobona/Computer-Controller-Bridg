"""步骤 14 真机验证：按《优化方案.md》实施的优化项回归。

运行方式：
    .venv/Scripts/python.exe scripts/step14_check.py [--with-vlm]

说明：
- 默认只验证本地链路（screen_analyze / self_test / 坐标标注结构），不调用外部 VLM；
- 加 --with-vlm 会做 1 次小的结构化 VLM 调用（消耗少量 API 额度），
  用于验证坐标系标注与短时缓存命中；
- 覆盖点：
  1) screen_analyze 缺省不再自动触发 VLM，且本地 UIA/OCR 正常时不再误报「降级」；
  2) 未请求的层显示「未启用」而非「不可用（已降级）」，降级时带原因；
  3) self_test 返回 uia/ocr 原因与急停热键状态；
  4) vlm_describe 返回坐标系标注（structured=绝对物理像素 / describe=区域相对）；
  5) VLM 短时缓存命中（同一 region+prompt 第二次调用接近 0s）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "src"))

from fastmcp import Client  # noqa: E402

from server.mcp_server import create_server  # noqa: E402


async def call(client, name: str, args: dict) -> dict:
    res = await client.call_tool(name, args)
    return res.data


async def check_default_analyze(client) -> None:
    """缺省 screen_analyze：不触发 VLM；UIA/OCR 正常时 degraded=False。"""
    t0 = time.perf_counter()
    res = await call(client, "screen_analyze", {})
    dt = time.perf_counter() - t0
    data = res["data"]
    assert res["ok"] is True, res
    assert "vlm" not in data, "缺省 screen_analyze 不应附加 VLM 层"
    assert data["enabled"] == {"uia": True, "ocr": True}
    assert data["degraded"]["uia"] is False and data["degraded"]["ocr"] is False, (
        f"本地 UIA/OCR 应可用，degraded={data['degraded']} reasons={data['reasons']}"
    )
    assert "不可用（已降级）" not in data["summary"]
    print(f"[1] screen_analyze 缺省: {dt:.2f}s，无 VLM 层，UIA/OCR 正常，摘要: {data['summary'][:80]}…")


async def check_disabled_layers_summary(client) -> None:
    """未请求的层显示「未启用」而非「降级」。"""
    res = await call(client, "screen_analyze", {"with_uia": False, "with_ocr": False})
    data = res["data"]
    assert "UIA 未启用" in data["summary"], data["summary"]
    assert "OCR 未启用" in data["summary"], data["summary"]
    assert data["degraded"] == {"uia": False, "ocr": False}
    assert data["reasons"] == {"uia": None, "ocr": None}
    print(f"[2] 未启用层语义: {data['summary']}")


async def check_self_test(client) -> None:
    res = await call(client, "self_test", {"dry_run": True})
    data = res["data"]
    assert "uia_reason" in data and "ocr_reason" in data
    assert "panic_hotkey" in data
    print(
        f"[3] self_test: uia={data['uia']} ocr={data['ocr']} "
        f"uia_reason={data['uia_reason']!r} ocr_reason={data['ocr_reason']!r} "
        f"panic_hotkey={data['panic_hotkey']}"
    )


async def check_vlm_coords_and_cache(client) -> None:
    """结构化返回绝对坐标标注；描述返回区域相对标注；短时缓存命中。"""
    region = [0, 0, 480, 270]
    prompt = "标注这个区域中的按钮和输入框"
    t0 = time.perf_counter()
    res = await call(client, "vlm_describe", {"region": region, "prompt": prompt, "structured": True})
    first_dt = time.perf_counter() - t0
    data = res["data"]
    assert res["ok"] is True and data.get("objects"), res
    assert data["coords"]["space"] == "absolute", data["coords"]
    assert data["coords"]["unit"] == "physical_pixels", data["coords"]
    for obj in data["objects"]:
        box = obj["box"]
        assert box[0] < box[2] and box[1] < box[3], box
    t0 = time.perf_counter()
    res2 = await call(client, "vlm_describe", {"region": region, "prompt": prompt, "structured": True})
    cached_dt = time.perf_counter() - t0
    assert res2["data"]["ok"] is True
    assert cached_dt < 1.0, f"缓存未命中: {cached_dt:.2f}s"
    print(
        f"[4] vlm_describe structured: 首次 {first_dt:.1f}s / 缓存 {cached_dt:.3f}s，"
        f"解析 {len(data['objects'])} 个元素，coords={data['coords']['space']}"
    )

    res3 = await call(client, "vlm_describe", {"region": region, "prompt": "描述这个区域", "structured": False})
    data3 = res3["data"]
    assert data3["coords"]["space"] == "region_relative", data3["coords"]
    assert "不可直接用于鼠标工具" in data3["coords"]["note"]
    print(f"[5] vlm_describe 描述模式 coords: {data3['coords']['space']}（区域相对，已标注不可直接点击）")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-vlm", action="store_true", help="调用 1 次外部 VLM 验证坐标标注与缓存")
    args = parser.parse_args()

    results: list[bool] = []
    server = create_server()
    async with Client(server) as client:
        for name, fn in [
            ("缺省 screen_analyze 不触发 VLM / 本地引擎正常", check_default_analyze),
            ("未启用层语义与降级原因字段", check_disabled_layers_summary),
            ("self_test 返回原因与热键状态", check_self_test),
        ]:
            try:
                await fn(client)
                results.append(True)
                print(f"  PASS: {name}")
            except AssertionError as exc:
                results.append(False)
                print(f"  FAIL: {name}: {exc}")
        if args.with_vlm:
            try:
                await check_vlm_coords_and_cache(client)
                results.append(True)
                print("  PASS: VLM 坐标标注与缓存")
            except AssertionError as exc:
                results.append(False)
                print(f"  FAIL: VLM 坐标标注与缓存: {exc}")
        else:
            print("  SKIP: VLM 坐标标注与缓存（加 --with-vlm 启用，需已配置 screen.vlm）")

    ok = all(results)
    print(f"\n步骤 14 验证：{'全部通过' if ok else '存在失败'}（{sum(results)}/{len(results)}）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
