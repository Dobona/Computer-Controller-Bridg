"""配置加载与校验单元测试（步骤 10）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server import config as config_mod
from server.config import ConfigError, load_config, set_config


@pytest.fixture(autouse=True)
def _reset_config():
    set_config(None)
    yield
    set_config(None)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_valid_with_defaults(tmp_path):
    """缺失字段自动使用默认值。"""
    cfg = load_config(_write(tmp_path, {"transport": "stdio"}))
    assert cfg["transport"] == "stdio"
    assert cfg["input"]["click_interval_ms"] == 50
    assert cfg["input"]["max_action_duration_s"] == 30
    assert cfg["screen"]["uia"]["enabled"] is True
    assert cfg["screen"]["ocr"]["enabled"] is True
    assert cfg["screen"]["vlm"]["endpoint"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert cfg["screen"]["vlm"]["timeout_s"] == 180
    assert cfg["screen"]["vlm"]["max_tokens"] == 2048
    assert cfg["screen"]["vlm"]["cache_ttl_s"] == 5
    assert cfg["log"]["level"] == "info"


def test_missing_file():
    with pytest.raises(ConfigError, match="未找到配置文件"):
        load_config(Path("Z:/no/such/config.json"))


def test_invalid_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="不是合法 JSON"):
        load_config(path)


def test_bad_transport(tmp_path):
    with pytest.raises(ConfigError, match="transport"):
        load_config(_write(tmp_path, {"transport": "http"}))


def test_bad_int_range(tmp_path):
    with pytest.raises(ConfigError, match="click_interval_ms"):
        load_config(_write(tmp_path, {"input": {"click_interval_ms": -5}}))


def test_bad_int_type(tmp_path):
    with pytest.raises(ConfigError, match="http.port"):
        load_config(_write(tmp_path, {"http": {"port": "abc"}}))


def test_bad_hotkey(tmp_path):
    with pytest.raises(ConfigError, match="panic_hotkey"):
        load_config(_write(tmp_path, {"safety": {"panic_hotkey": ["ctrl", "x"]}}))


def test_bad_log_level(tmp_path):
    with pytest.raises(ConfigError, match="log.level"):
        load_config(_write(tmp_path, {"log": {"level": "verbose"}}))


def test_bad_bool(tmp_path):
    with pytest.raises(ConfigError, match="save_screenshots"):
        load_config(_write(tmp_path, {"screen": {"save_screenshots": "yes"}}))


def test_unknown_keys_tolerated(tmp_path):
    cfg = load_config(_write(tmp_path, {"future_option": 123}))
    assert cfg["future_option"] == 123


def test_set_config_cache():
    set_config({"transport": "stdio", "marker": 1})
    assert config_mod.get_config()["marker"] == 1


def test_full_config_template_valid():
    """项目根目录配置模板本身必须能通过校验（仓库内使用 config.example.json）。"""
    from server.config import CONFIG_FILE

    template = CONFIG_FILE if CONFIG_FILE.exists() else CONFIG_FILE.with_name("config.example.json")
    assert template.exists(), "项目根目录缺少 config.json / config.example.json"
    cfg = load_config(template)
    assert isinstance(cfg["screen"]["vlm"]["enabled"], bool)
    assert isinstance(cfg["screen"]["vlm"]["endpoint"], str)
    assert cfg["screen"]["vlm"]["model"]
    assert isinstance(cfg["screen"]["vlm"]["api_key"], str)


def test_bad_vlm_fields(tmp_path):
    cases = [
        ({"screen": {"vlm": {"api_key": 123}}}, "api_key"),
        ({"screen": {"vlm": {"model": []}}}, "model"),
        ({"screen": {"vlm": {"max_tokens": 0}}}, "max_tokens"),
        ({"screen": {"vlm": {"cache_ttl_s": -1}}}, "cache_ttl_s"),
        ({"screen": {"vlm": {"cache_ttl_s": "x"}}}, "cache_ttl_s"),
        ({"screen": {"vlm": {"endpoint": "ftp://x"}}}, "screen.vlm.endpoint"),
    ]
    for data, expect in cases:
        with pytest.raises(ConfigError, match=expect):
            load_config(_write(tmp_path, data))


def test_null_section_friendly_error(tmp_path):
    """section 为 null 时应抛 ConfigError 而不是裸 AttributeError。"""
    for data, expect in [
        ({"screen": None}, "screen"),
        ({"input": None}, "input"),
        ({"safety": None}, "safety"),
        ({"log": None}, "log"),
    ]:
        with pytest.raises(ConfigError, match=expect):
            load_config(_write(tmp_path, data))


def test_non_dict_section_friendly_error(tmp_path):
    with pytest.raises(ConfigError, match="screen"):
        load_config(_write(tmp_path, {"screen": []}))


def test_null_nested_section_friendly_error(tmp_path):
    with pytest.raises(ConfigError, match="screen.vlm"):
        load_config(_write(tmp_path, {"screen": {"vlm": None}}))


def test_null_field_friendly_error(tmp_path):
    """字段为 null 时报字段路径而非崩溃。"""
    cases = [
        ({"screen": {"vlm": {"api_key": None}}}, "screen.vlm.api_key"),
        ({"input": {"move_step_ms": None}}, "input.move_step_ms"),
        ({"log": {"audit": None}}, "log.audit"),
    ]
    for data, expect in cases:
        with pytest.raises(ConfigError, match=expect):
            load_config(_write(tmp_path, data))
