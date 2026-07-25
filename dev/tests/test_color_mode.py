"""색 모드(라이트/다크) — 전환·토큰·대비·prefs 왕복·세션 중 전환 차단."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect
from pathlib import Path

import pytest

from aoi_verification.app.ui import theme
from aoi_verification.app.utils import prefs

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _restore_light():
    yield
    theme.set_color_mode("light")


def test_two_modes_available():
    assert set(theme.color_mode_keys()) == {"light", "dark"}


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_qss_renders_fully_in_both_modes(mode):
    theme.set_color_mode(mode)
    out = theme.render_qss(_QSS)
    assert "$" not in out


def test_token_keysets_identical():
    theme.set_color_mode("light")
    light = set(theme.TOKENS)
    theme.set_color_mode("dark")
    dark = set(theme.TOKENS)
    assert light == dark, f"토큰 키셋 불일치: {light ^ dark}"


def test_tokens_mutated_in_place():
    """이미 TOKENS 참조를 들고 있는 쪽이 끊기지 않아야 한다."""
    ref = theme.TOKENS
    theme.set_color_mode("dark")
    assert ref is theme.TOKENS
    assert ref["panel"] == theme.PALETTES["dark"]["panel"]


def test_globals_follow_mode():
    theme.set_color_mode("dark")
    assert theme.INK == theme.PALETTES["dark"]["ink"]
    assert theme.PANEL == theme.PALETTES["dark"]["panel"]
    assert theme.COLOR_MODE == "dark"
    theme.set_color_mode("light")
    assert theme.INK == theme.PALETTES["light"]["ink"]


def test_unknown_mode_falls_back():
    theme.set_color_mode("chartreuse")
    assert theme.COLOR_MODE == theme.DEFAULT_COLOR_MODE


def test_dark_is_not_a_naive_inversion():
    """다크는 라이트를 뒤집은 값이 아니어야 한다(청사진 컨셉의 야간판)."""
    light, dark = theme.PALETTES["light"], theme.PALETTES["dark"]

    def inv(hexv: str) -> str:
        h = hexv.lstrip("#")
        return "#%02X%02X%02X" % tuple(255 - int(h[i:i + 2], 16) for i in (0, 2, 4))

    same_as_inverse = [k for k in light if dark[k].upper() == inv(light[k])]
    assert not same_as_inverse, f"단순 반전 값: {same_as_inverse}"
    # 다크 바탕은 라이트 바탕보다 확실히 어둡고, 파랑 쪽으로 기울어 있다(청사진).
    dh = dark["bg"].lstrip("#")
    r, g, b = (int(dh[i:i + 2], 16) for i in (0, 2, 4))
    assert b > r, "청사진 야간판이라면 바탕이 파랑 쪽이어야 한다"


def test_scrim_is_translucent_enough_to_see_through():
    """로딩 스크림이 화면을 '전부 가리지' 않아야 한다(사용자 요청)."""
    for mode in theme.color_mode_keys():
        theme.set_color_mode(mode)
        alpha = theme.SCRIM_RGBA[3]
        assert 60 <= alpha <= 130, f"{mode}: 스크림 알파 {alpha}"


def test_prefs_round_trip(isolated_cache):
    prefs.save(prefs.UiPrefs(color_mode="dark"))
    assert prefs.load().color_mode == "dark"


def test_switch_is_guarded_to_pre_session():
    """세션 중 색 모드 전환은 막혀 있어야 한다(진행 상태 보호)."""
    from aoi_verification.app.ui import main_window as mw
    src = inspect.getsource(mw.MainWindow._on_appearance_changed)
    assert "PHASE_NONE" in src
    assert "_recreate_pages" in inspect.getsource(mw.MainWindow)


def test_no_design_variant_machinery_resurrected():
    """색 모드는 '경쟁 디자인 스위처'가 아니다 — 옛 기계 이름은 계속 금지."""
    for gone in ("VARIANTS", "set_variant", "variant_keys", "CURRENT_VARIANT",
                 "DEFAULT_VARIANT", "Variant"):
        assert not hasattr(theme, gone), f"theme.{gone} 부활"
