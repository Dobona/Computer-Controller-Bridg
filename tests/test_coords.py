"""坐标换算与越界校验的单元测试（不触碰真实屏幕/鼠标）。"""

from __future__ import annotations

import pytest

from core import coords


@pytest.fixture
def single_screen(monkeypatch):
    """主屏 2560x1440，原点 (0,0)。"""
    monkeypatch.setattr(coords, "virtual_screen", lambda: (0, 0, 2560, 1440))


@pytest.fixture
def dual_screen(monkeypatch):
    """双屏：左侧副屏 -1920..0，主屏 0..2560，虚拟桌面 4480 宽。"""
    monkeypatch.setattr(coords, "virtual_screen", lambda: (-1920, 0, 4480, 1440))


def test_validate_inside(single_screen):
    coords.validate_coords(0, 0)
    coords.validate_coords(1280, 720)
    coords.validate_coords(2559, 1439)


def test_validate_outside(single_screen):
    for x, y in [(-1, 0), (0, -1), (2560, 0), (0, 1440), (99999, 5), (0, -99999)]:
        with pytest.raises(ValueError, match="越界"):
            coords.validate_coords(x, y)


def test_validate_negative_origin(dual_screen):
    """副屏坐标为负时仍在虚拟桌面范围内。"""
    coords.validate_coords(-1920, 0)
    coords.validate_coords(-100, 700)
    coords.validate_coords(2559, 1439)
    with pytest.raises(ValueError, match="越界"):
        coords.validate_coords(-1921, 0)
    with pytest.raises(ValueError, match="越界"):
        coords.validate_coords(2560, 0)


def test_normalize_corners(single_screen):
    assert coords.normalize_abs(0, 0) == (0, 0)
    assert coords.normalize_abs(2559, 1439) == (65535, 65535)


def test_normalize_middle(single_screen):
    nx, ny = coords.normalize_abs(1280, 720)
    assert nx == 32780  # 1280 * 65535 / 2559 ≈ 32780.4
    assert ny == 32790  # 720 * 65535 / 1439 ≈ 32790.3


def test_normalize_clamps(single_screen):
    """越界坐标被钳制到 0..65535（对外仍应先用 validate_coords 拒绝）。"""
    assert coords.normalize_abs(-100, -100) == (0, 0)
    assert coords.normalize_abs(99999, 99999) == (65535, 65535)


def test_normalize_dual_screen(dual_screen):
    """副屏最左 (-1920,0) → SendInput 0；主屏最右 (2559,1439) → 65535。"""
    assert coords.normalize_abs(-1920, 0) == (0, 0)
    assert coords.normalize_abs(2559, 1439) == (65535, 65535)
    # 主屏中心 (1280,720)：距虚拟桌面原点 3200 像素，约 71.5% 位置
    nx, ny = coords.normalize_abs(1280, 720)
    assert 46815 <= nx <= 46830  # 3200 / 4479 * 65535 ≈ 46823
    assert abs(ny - 32790) <= 1


def _mock_physical(monkeypatch, width: int, height: int, dpi: int) -> None:
    """按物理像素宽高 + DPI 模拟屏幕（DPI 缩放只改变物理分辨率与 scale 值）。"""
    monkeypatch.setattr(coords, "virtual_screen", lambda: (0, 0, width, height))
    monkeypatch.setattr(
        coords.user32,
        "GetSystemMetrics",
        lambda idx: width if idx == coords.SM_CXSCREEN else height,
    )
    monkeypatch.setattr(coords.user32, "GetDpiForSystem", lambda: dpi)


def test_dpi_125(monkeypatch):
    """125% 缩放：逻辑 1920x1080 → 物理 2400x1350。"""
    _mock_physical(monkeypatch, 2400, 1350, 120)
    assert coords.normalize_abs(1200, 675) == (32781, 32791)
    info = coords.screen_info()
    assert info["primary"] == {"width": 2400, "height": 1350}
    assert info["dpi_scale"] == 1.25


def test_dpi_150(monkeypatch):
    """150% 缩放：逻辑 1920x1080 → 物理 2880x1620。"""
    _mock_physical(monkeypatch, 2880, 1620, 144)
    assert coords.normalize_abs(1440, 810) == (32778, 32787)
    assert coords.screen_info()["dpi_scale"] == 1.5


def test_dpi_200(monkeypatch):
    """200% 缩放：逻辑 1920x1080 → 物理 3840x2160。"""
    _mock_physical(monkeypatch, 3840, 2160, 192)
    assert coords.normalize_abs(1920, 1080) == (32776, 32782)
    assert coords.screen_info()["dpi_scale"] == 2.0
