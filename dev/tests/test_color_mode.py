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


# ★ 모드 목록을 복제하지 않는다 — theme 에서 읽어야 새 색 모드가
#   추가되는 순간 모든 대비·포커스 계약이 자동으로 걸린다.
_MODES = list(theme.color_mode_keys())


@pytest.fixture(autouse=True)
def _restore_light():
    yield
    theme.set_color_mode("light")


def test_three_modes_available():
    """벨럼(라이트) + 어두운 모드 **둘**(청사진·흑연)."""
    assert set(theme.color_mode_keys()) == {"light", "dark", "graphite"}
    # 사용자에게 보이는 이름이 모든 모드에 있어야 한다(스위처가 이걸 읽는다).
    assert set(theme.COLOR_MODE_LABELS) == set(theme.PALETTES)


def test_dark_key_stays_dark_for_prefs_compat():
    """★ 청사진의 키는 `"dark"` 로 유지한다 — 기존 prefs 가 마이그레이션 없이 동작."""
    assert "dark" in theme.PALETTES
    theme.set_color_mode("dark")
    assert theme.COLORS["bg"] == theme.PALETTES["dark"]["bg"]


def test_two_dark_modes_are_opposite_in_temperature_and_chroma():
    """두 어두운 모드가 서로의 **변주가 아니라 다른 판단**이어야 한다.

    청사진 = 유채·차가움(청색 감광지) / 흑연 = 무채·따뜻함(불 끈 제도지)."""
    def chroma(hexv: str) -> float:
        h = hexv.lstrip("#")
        ch = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
        return (max(ch) - min(ch)) / 255.0

    blue = theme.PALETTES["dark"]["bg"]
    graph = theme.PALETTES["graphite"]["bg"]
    assert chroma(blue) > 0.15, f"청사진 bg 채도가 낮다 ({chroma(blue):.3f})"
    assert chroma(graph) < 0.08, f"흑연 bg 가 무채가 아니다 ({chroma(graph):.3f})"
    # 색 온도가 반대: 청사진은 파랑 우세, 흑연은 빨강 우세.
    def rgb(h):
        h = h.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    br, _, bb = rgb(blue)
    gr, _, gb = rgb(graph)
    assert bb > br, "청사진 바탕이 파랑 쪽이어야 한다"
    assert gr > gb, "흑연 바탕이 따뜻한 쪽이어야 한다"


def test_both_dark_modes_are_actually_dark():
    for key in ("dark", "graphite"):
        h = theme.PALETTES[key]["bg"].lstrip("#")
        assert sum(int(h[i:i + 2], 16) for i in (0, 2, 4)) / 3 < 90, \
            f"{key} 바탕이 어둡지 않다"


@pytest.mark.parametrize("mode", _MODES)
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


@pytest.mark.parametrize("key", ["dark", "graphite"])
def test_dark_is_not_a_naive_inversion(key):
    """어두운 모드는 라이트를 뒤집은 값이 아니어야 한다."""
    light, dark = theme.PALETTES["light"], theme.PALETTES[key]

    def inv(hexv: str) -> str:
        h = hexv.lstrip("#")
        return "#%02X%02X%02X" % tuple(255 - int(h[i:i + 2], 16) for i in (0, 2, 4))

    same_as_inverse = [k for k in light if dark[k].upper() == inv(light[k])]
    assert not same_as_inverse, f"{key}: 단순 반전 값: {same_as_inverse}"


def test_scrim_is_translucent_enough_to_see_through():
    """로딩 스크림이 화면을 '전부 가리지' 않아야 한다(사용자 요청)."""
    for mode in _MODES:
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


# ── 강조는 '자기 종이'와 구분되어야 한다 ─────────────────────────────────────
def _hsv(hexv: str) -> tuple[float, float, float]:
    import colorsys
    h = hexv.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    hh, s, v = colorsys.rgb_to_hsv(r, g, b)
    return hh * 360.0, s, v


@pytest.mark.parametrize("mode", _MODES)
def test_accent_is_distinguishable_from_its_own_paper(mode):
    """★ 강조색이 바탕과 **색상각**으로도 구분되어야 한다.

    청사진 다크의 accent 가 한때 bg 와 색상각 차이 **0.8°** · 채도비 0.54 였다 —
    '강조 하나' 원칙을 명도만으로 버티는 셈이고, 화면 전체가 한 색상이 된다.
    (벨럼 167° / 흑연 166° 와 대조적이었다.)  대비만 재면 이 결함이 안 보인다."""
    theme.set_color_mode(mode)
    c = theme.COLORS
    ah, a_s, _ = _hsv(c["accent"])
    bh, b_s, _ = _hsv(c["bg"])
    dh = abs(ah - bh)
    dh = min(dh, 360.0 - dh)
    assert dh >= 10.0, f"{mode}: accent 와 bg 의 색상각 차 {dh:.1f}° — 같은 색상이다"


# ※ '경고는 강조보다 채도가 낮아야 한다'는 테스트를 넣으려다 **뺐다.**
#    흑연 모드에서 warn(0.485)이 accent(0.389)를 이긴다는 지적 자체는 타당해 그 값은
#    낮췄지만(#E9C478 → #DFC796), 이를 **불변식으로 만들면 거짓**이 된다:
#    이 프로젝트의 규칙은 "ACCENT 는 주요 액션·현재 선택 전용, **상태는 PASS/DANGER/WARN**"
#    이고, 상태색은 문제가 생겼을 때 눈을 끄는 것이 제 역할이다.  실제로 라이트의
#    warn(0.893)·danger(0.819)는 accent(0.672)보다 채도가 높고 그게 의도다.
#    통과하지 않는 규칙을 테스트로 박으면 다음 사람이 잘못된 방향으로 팔레트를 고친다.
