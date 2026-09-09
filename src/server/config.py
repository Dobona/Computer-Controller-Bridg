"""配置加载与校验：读取项目根目录 config.json，非法配置给出明确报错。

缺失字段使用默认值（与《项目方案.md》4.6 节一致），
未知字段容忍（不影响运行），已知字段类型/取值范围严格校验。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / "config.json"


class ConfigError(ValueError):
    """配置错误（message 为面向用户的中文说明）。"""


DEFAULT_CONFIG = {
    "transport": "stdio",
    "http": {"host": "127.0.0.1", "port": 8765, "token": ""},
    "input": {
        "backend": "sendinput",
        "move_step_ms": 10,
        "click_interval_ms": 50,
        "max_action_duration_s": 30,
        "max_click_rate_per_s": 20,
    },
    "safety": {
        "panic_hotkey": ["ctrl", "alt", "shift", "esc"],
        "release_keys_on_exit": True,
        "confirm_mode": False,
    },
    "screen": {
        "save_screenshots": False,
        "uia": {"enabled": True},
        "ocr": {"enabled": True},
        "vlm": {
            "enabled": False,
            "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
            "api_key": "",
            "model": "Qwen/Qwen3-VL-32B-Instruct",
            "timeout_s": 180,
            "max_tokens": 2048,
            # 同一 region+prompt 的结果短时缓存（秒），0=关闭；用于避免同一布局反复识别
            "cache_ttl_s": 5,
        },
    },
    "log": {"level": "info", "audit": True},
}

_config: dict | None = None


def _deep_merge(defaults: dict, user: dict) -> dict:
    """把用户配置合并到默认配置（缺失字段用默认值）。"""
    result = dict(defaults)
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _fail(path: str, message: str) -> None:
    raise ConfigError(f"config.json 配置错误: {path} {message}")


def _walk(cfg, path: str):
    """沿点分路径逐层取值；中间节点不是 dict 时抛 ConfigError（含出错路径）。"""
    node = cfg
    parts = path.split(".")
    for i, part in enumerate(parts):
        if not isinstance(node, dict):
            _fail(".".join(parts[:i]), f"必须是 JSON 对象，实际为 {node!r}")
        if part not in node:
            return None
        node = node[part]
    return node


def _require_dict(cfg, path: str) -> None:
    """校验路径指向的值是 JSON 对象（dict）。"""
    if not isinstance(_walk(cfg, path), dict):
        _fail(path, "必须是 JSON 对象")


def _require_int(cfg, path: str, minimum=None, maximum=None) -> None:
    """校验路径指向的值是整数且在 [minimum, maximum] 区间。"""
    node = _walk(cfg, path)
    if not isinstance(node, int) or isinstance(node, bool):
        _fail(path, f"必须是整数，实际为 {node!r}")
    if minimum is not None and node < minimum:
        _fail(path, f"必须 >= {minimum}，实际为 {node}")
    if maximum is not None and node > maximum:
        _fail(path, f"必须 <= {maximum}，实际为 {node}")


def _require_bool(cfg, path: str) -> None:
    node = _walk(cfg, path)
    if not isinstance(node, bool):
        _fail(path, f"必须是布尔值 true/false，实际为 {node!r}")


def _require_str(cfg, path: str) -> None:
    node = _walk(cfg, path)
    if not isinstance(node, str):
        _fail(path, f"必须是字符串，实际为 {node!r}")


def _validate(cfg: dict) -> None:
    # 先做整体类型检查：已知 section 必须是对象，否则后续 .get() 会裸崩
    for section in ("http", "input", "safety", "screen", "log"):
        _require_dict(cfg, section)
    _require_dict(cfg, "screen.uia")
    _require_dict(cfg, "screen.ocr")
    _require_dict(cfg, "screen.vlm")
    if cfg.get("transport") not in ("stdio",):
        _fail("transport", f"本期仅支持 stdio，实际为 {cfg.get('transport')!r}")
    _require_str(cfg, "http.host")
    _require_str(cfg, "http.token")
    _require_int(cfg, "http.port", 1, 65535)
    if cfg.get("input", {}).get("backend") not in ("sendinput",):
        _fail("input.backend", f"本期仅支持 sendinput，实际为 {cfg.get('input', {}).get('backend')!r}")
    _require_int(cfg, "input.move_step_ms", 1, 10000)
    _require_int(cfg, "input.click_interval_ms", 0, 60000)
    _require_int(cfg, "input.max_action_duration_s", 1, 3600)
    _require_int(cfg, "input.max_click_rate_per_s", 1, 1000)
    _require_bool(cfg, "safety.release_keys_on_exit")
    _require_bool(cfg, "safety.confirm_mode")
    hotkey = cfg.get("safety", {}).get("panic_hotkey")
    if not isinstance(hotkey, list) or not hotkey:
        _fail("safety.panic_hotkey", "必须是非空列表，如 [\"ctrl\", \"alt\", \"shift\", \"esc\"]")
    try:
        from safety.panic import _parse_hotkey

        _parse_hotkey(hotkey)
    except ValueError as exc:
        _fail("safety.panic_hotkey", str(exc))
    _require_bool(cfg, "screen.save_screenshots")
    _require_bool(cfg, "screen.uia.enabled")
    _require_bool(cfg, "screen.ocr.enabled")
    _require_bool(cfg, "screen.vlm.enabled")
    _require_str(cfg, "screen.vlm.endpoint")
    _require_str(cfg, "screen.vlm.api_key")
    _require_str(cfg, "screen.vlm.model")
    _require_int(cfg, "screen.vlm.timeout_s", 1, 600)
    _require_int(cfg, "screen.vlm.max_tokens", 1, 32768)
    _require_int(cfg, "screen.vlm.cache_ttl_s", 0, 3600)
    endpoint = cfg.get("screen", {}).get("vlm", {}).get("endpoint", "")
    if endpoint and not endpoint.startswith(("http://", "https://")):
        _fail("screen.vlm.endpoint", f"必须以 http:// 或 https:// 开头，实际为 {endpoint!r}")
    if cfg.get("log", {}).get("level") not in ("debug", "info", "warning", "error"):
        _fail("log.level", f"可选 debug/info/warning/error，实际为 {cfg.get('log', {}).get('level')!r}")
    _require_bool(cfg, "log.audit")


def load_config(path: str | Path | None = None) -> dict:
    """加载并校验配置。文件缺失/JSON 非法/字段非法时抛 ConfigError（含中文原因）。"""
    config_path = Path(path) if path else CONFIG_FILE
    if not config_path.exists():
        raise ConfigError(f"未找到配置文件 {config_path}，请确认项目根目录存在 config.json")
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json 不是合法 JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.json 顶层必须是 JSON 对象")
    cfg = _deep_merge(DEFAULT_CONFIG, raw)
    _validate(cfg)
    return cfg


def get_config() -> dict:
    """获取（缓存的）配置；未加载时自动加载。"""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(cfg: dict | None) -> None:
    """设置/清除缓存配置（测试用）。"""
    global _config
    _config = cfg
