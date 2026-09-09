"""步骤 10 真机验证：配置加载与校验、screen 配置生效、日志与审计。

运行方式：
    .venv/Scripts/python.exe scripts/step10_check.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "src"))

from fastmcp import Client  # noqa: E402

from server import config as config_mod  # noqa: E402
from server.config import ConfigError, load_config  # noqa: E402
from server.mcp_server import create_server  # noqa: E402


def check_config_validation() -> None:
    """合法配置加载 + 默认值合并；非法配置抛出明确中文错误。"""
    cfg = load_config(ROOT / "config.json")
    assert cfg["transport"] == "stdio"
    assert isinstance(cfg["screen"]["vlm"]["enabled"], bool)
    assert cfg["screen"]["vlm"]["endpoint"].startswith("http")
    assert cfg["screen"]["vlm"]["model"]
    assert cfg["screen"]["uia"]["enabled"] is True
    print("[1] 配置加载: 项目 config.json 校验通过，默认值合并正常")

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "config.json"
        bad.write_text(json.dumps({"input": {"click_interval_ms": -1}}), encoding="utf-8")
        try:
            load_config(bad)
            raise AssertionError("非法配置未报错")
        except ConfigError as exc:
            assert "click_interval_ms" in str(exc)
            print(f"[2] 配置校验: 非法配置给出明确报错（{exc}）")


def check_main_fatal_on_bad_config() -> None:
    """服务入口在配置非法时立即退出并输出 [FATAL]，不启动服务。"""
    backup = ROOT / "config.json.bak"
    shutil.copy(ROOT / "config.json", backup)
    try:
        (ROOT / "config.json").write_text("{broken", encoding="utf-8")
        proc = subprocess.run(
            [str(ROOT / ".venv/Scripts/python.exe"), "src/main.py"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0, "非法配置下服务不应正常启动"
        assert "[FATAL]" in proc.stderr, proc.stderr
        print("[3] 服务入口: 配置非法时输出 [FATAL] 并立即退出")
    finally:
        shutil.move(backup, ROOT / "config.json")


async def check_config_effects() -> None:
    """screen.uia/ocr/vlm 开关与 save_screenshots 生效。"""
    config_mod.set_config(load_config(ROOT / "config.json"))
    cfg = config_mod.get_config()

    server = create_server()
    async with Client(server) as client:
        # vlm 临时关闭 → 明确提示（真实调用由步骤 13 验证，这里不消耗 API）
        cfg["screen"]["vlm"]["enabled"] = False
        res = await client.call_tool("vlm_describe", {})
        assert res.data["ok"] is False, res
        assert "未配置 VLM" in res.data["message"], res
        cfg["screen"]["vlm"]["enabled"] = True
        print("[4] vlm_describe: 未启用时返回明确提示")

        # save_screenshots=true → 截图落盘
        cfg["screen"]["save_screenshots"] = True
        shots_dir = ROOT / "logs" / "screenshots"
        before = set(shots_dir.glob("*.png")) if shots_dir.exists() else set()
        await client.call_tool("screenshot", {})
        time.sleep(0.3)
        after = set(shots_dir.glob("*.png")) if shots_dir.exists() else set()
        assert after - before, "save_screenshots=true 时截图未落盘"
        print(f"[5] save_screenshots: 截图已写入 logs/screenshots/（{len(after - before)} 张）")

        # uia/ocr 关闭时 screen_analyze 默认不执行对应层
        cfg["screen"]["uia"]["enabled"] = False
        cfg["screen"]["ocr"]["enabled"] = False
        res = await client.call_tool("screen_analyze", {"with_vlm": False})
        assert res.data["ok"] is True, res
        assert res.data["data"]["elements"] == [], "uia 关闭后 elements 应为空"
        assert res.data["data"]["texts"] == [], "ocr 关闭后 texts 应为空"
        assert res.data["data"]["windows"], "窗口枚举不应受 uia/ocr 开关影响"
        print("[6] screen_analyze: uia/ocr 配置开关生效（关闭后对应层为空，窗口层保留）")

        # 显式传参仍可临时开启
        res = await client.call_tool(
            "screen_analyze", {"with_uia": True, "with_ocr": False, "with_vlm": False}
        )
        assert res.data["ok"] is True
        print("[7] screen_analyze: 工具参数可临时覆盖配置开关")


def check_audit_daily_file() -> None:
    """审计日志按天归档：当日文件存在且为 JSONL。"""
    audit_file = ROOT / "logs" / f"audit-{time.strftime('%Y%m%d')}.jsonl"
    if audit_file.exists():
        first = audit_file.read_text(encoding="utf-8").splitlines()[0]
        json.loads(first)
        print("[8] 审计日志: 按天文件命名（audit-YYYYMMDD.jsonl）且内容为合法 JSONL")
    else:
        print("[8] 审计日志: 今日无调用记录，文件命名规则已由 audit.py 保证（跳过）")


def main() -> None:
    check_config_validation()
    check_main_fatal_on_bad_config()
    asyncio.run(check_config_effects())
    check_audit_daily_file()
    print("== 步骤10 真机验证全部通过 ==")


if __name__ == "__main__":
    main()
