"""步骤 13 真机验证：外接 VLM（硅基流动 Qwen3-VL）深度集成。

运行方式：
    .venv/Scripts/python.exe scripts/step13_check.py

说明：会真实调用外部 VLM（消耗少量 API 额度），需要 config.json 中已配置
screen.vlm（endpoint/api_key/model）。
"""

from __future__ import annotations

import asyncio
import sys

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "src"))

from fastmcp import Client  # noqa: E402

from core.coords import set_dpi_awareness, virtual_screen  # noqa: E402
from server import config as config_mod  # noqa: E402
from server.mcp_server import create_server  # noqa: E402


async def call(client, name: str, args: dict) -> dict:
    res = await client.call_tool(name, args)
    return res.data


async def check_describe_full(client) -> None:
    res = await call(client, "vlm_describe", {})
    assert res["ok"] is True, res
    assert res["data"]["text"], "未返回描述文本"
    print(f"[1] vlm_describe 全屏: {res['data']['text'][:80]}…")


async def check_describe_region(client) -> None:
    vx, vy, vw, vh = virtual_screen()
    region = [vx + 60, vy + 80, vx + 900, vy + 700]
    res = await call(client, "vlm_describe", {"region": region})
    assert res["ok"] is True, res
    assert res["data"]["text"], "区域描述为空"
    print(f"[2] vlm_describe 区域: {res['data']['text'][:80]}…")


async def check_structured(client) -> None:
    res = await call(client, "vlm_describe", {"structured": True})
    assert res["ok"] is True, res
    assert res["data"]["text"], "结构化识别未返回文本"
    objects = res["data"].get("objects", [])
    assert objects, f"未解析到 objects（parsed={res['data'].get('parsed')}），原始文本见 data.text"
    # 坐标必须是物理像素整数，且落在合理范围
    for obj in objects[:5]:
        box = obj["box"]
        assert len(box) == 4 and all(isinstance(v, int) for v in box), box
        assert box[0] < box[2] and box[1] < box[3], box
    print(f"[3] vlm_describe structured: 解析到 {len(objects)} 个元素（示例：{objects[0]}）")


async def check_screen_analyze_vlm(client) -> None:
    res = await call(client, "screen_analyze", {"scope": "full", "with_uia": True, "with_ocr": False, "with_vlm": True})
    assert res["ok"] is True, res
    vlm = res["data"].get("vlm")
    assert vlm and vlm.get("ok") is True and vlm.get("text"), vlm
    assert res["data"]["windows"], "窗口层仍应正常返回"
    print(f"[4] screen_analyze with_vlm: 附加 VLM 描述层（{vlm['text'][:60]}…）")


async def check_unconfigured_message(client) -> None:
    """未配置时返回明确提示（用临时清空配置模拟）。"""
    cfg = config_mod.get_config()
    saved = dict(cfg["screen"]["vlm"])
    cfg["screen"]["vlm"]["enabled"] = False
    try:
        server2 = create_server()
        async with Client(server2) as client2:
            res = await client2.call_tool("vlm_describe", {})
        assert res.data["ok"] is False
        assert "未配置 VLM" in res.data["message"], res
        print("[5] 未启用时 vlm_describe 返回明确提示")
    finally:
        cfg["screen"]["vlm"].update(saved)


async def run_all() -> None:
    vlm_cfg = config_mod.get_config()["screen"]["vlm"]
    assert vlm_cfg["enabled"], "config.json 中 screen.vlm.enabled 应为 true"
    assert vlm_cfg["api_key"], "config.json 中 screen.vlm.api_key 未配置"
    print(f"模型: {vlm_cfg['model']}，端点: {vlm_cfg['endpoint']}")
    server = create_server()
    async with Client(server) as client:
        await check_describe_full(client)
        await check_describe_region(client)
        await check_structured(client)
        await check_screen_analyze_vlm(client)
        await check_unconfigured_message(client)
        print("== 步骤13 真机验证全部通过 ==")


def main() -> None:
    set_dpi_awareness()
    asyncio.run(run_all())


if __name__ == "__main__":
    main()
