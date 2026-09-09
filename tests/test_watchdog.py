"""看门狗单元测试：状态读写、释放、子进程入口（mock 输入引擎）。"""

from __future__ import annotations

import json

import pytest

from core import keyboard, mouse
from safety import watchdog


def test_state_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    watchdog.write_state(state_file, {"held_keys": ["ctrl"], "held_buttons": ["left"]})
    state = watchdog.read_state(state_file)
    assert state["held_keys"] == ["ctrl"]
    assert state["held_buttons"] == ["left"]


def test_read_state_missing(tmp_path):
    assert watchdog.read_state(tmp_path / "nope.json") is None


def test_release_state(monkeypatch):
    released_keys = []
    released_buttons = []
    monkeypatch.setattr(keyboard, "key_up", lambda name: released_keys.append(name))
    monkeypatch.setattr(mouse, "button_up", lambda button: released_buttons.append(button))
    result = watchdog.release_state({"held_keys": ["ctrl", "shift"], "held_buttons": ["left"]})
    assert result == {
        "released_keys": ["ctrl", "shift"],
        "released_buttons": ["left"],
    }
    assert released_keys == ["ctrl", "shift"]
    assert released_buttons == ["left"]


def test_state_writer_writes_and_stops(tmp_path):
    state_file = tmp_path / "state.json"
    writer = watchdog.StateWriter(state_file, interval_ms=50)
    writer.start()
    deadline = __import__("time").time() + 2
    while __import__("time").time() < deadline and not state_file.exists():
        __import__("time").sleep(0.05)
    assert state_file.exists()
    writer.stop()
    assert not state_file.exists()


def test_watchdog_main_releases_after_process_gone(tmp_path, monkeypatch):
    """主进程已不存在时，看门狗立即按状态文件释放并写标记文件。"""
    state_file = tmp_path / "state.json"
    watchdog.write_state(state_file, {"held_keys": ["ctrl"], "held_buttons": ["left"]})
    released_keys = []
    released_buttons = []
    monkeypatch.setattr(keyboard, "key_up", lambda name: released_keys.append(name))
    monkeypatch.setattr(mouse, "button_up", lambda button: released_buttons.append(button))

    rc = watchdog.main(["--watch", "99999999", "--state", str(state_file)])
    assert rc == 0
    marker = state_file.with_suffix(".released")
    assert marker.exists()
    result = json.loads(marker.read_text(encoding="utf-8"))
    assert result["released_keys"] == ["ctrl"]
    assert result["released_buttons"] == ["left"]
    # 看门狗处理完毕后删除自己的状态文件，避免多实例残留
    assert not state_file.exists()
