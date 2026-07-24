"""테마 변형(Variant/Profile) — 렌더 무결성·토큰 키셋·대비(AA)·기준선 고정."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aoi_verification.app.ui import theme

_REPO = Path(__file__).resolve().parents[2]
_QSS = (_REPO / "aoi_verification" / "app" / "ui" / "style.qss").read_text(encoding="utf-8")

_OLD_HEXES = (
    "#39FF14", "#FF2D55", "#FFD600", "#00FFA3", "#7FB3D5", "#0A0E1A",
    "#0E1424", "#1F2A3F", "#050810", "#E5F4FF", "#9CC5E8", "#00C853",
    "#FF6B35", "#111827",
)


@pytest.fixture(autouse=True)
def _restore_default():
    yield
    theme.set_variant("instrument")


# ── WCAG 상대휘도/대비 (순수) ────────────────────────────────────────────────
def _lin(c):
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lum(hexv):
    h = hexv.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_every_variant_renders_qss_fully():
    for key in theme.variant_keys():
        theme.set_variant(key)
        out = theme.render_qss(_QSS)
        assert "$" not in out, f"{key}: 치환 안 된 토큰"


def test_variant_token_keys_identical():
    keysets = {}
    for key in theme.variant_keys():
        theme.set_variant(key)
        keysets[key] = frozenset(theme.TOKENS.keys())
    base = keysets["instrument"]
    for key, ks in keysets.items():
        assert ks == base, f"{key}: 토큰 키셋 불일치 {ks ^ base}"


def test_tokens_mutated_in_place():
    ref = theme.TOKENS
    theme.set_variant("instrument")
    assert ref is theme.TOKENS


def test_no_old_palette_in_any_variant():
    for key in theme.variant_keys():
        v = theme.VARIANTS[key]
        for name, hexv in v.colors.items():
            assert hexv.upper() not in _OLD_HEXES, f"{key}.{name} = 구팔레트 {hexv}"


def test_contrast_all_variants_aa():
    """전 변형 전 색상쌍 WCAG AA (본문 ≥4.5, 포커스 링 ≥3)."""
    fails = []
    for key in theme.variant_keys():
        theme.set_variant(key)
        c = theme.VARIANTS[key].colors
        checks = [
            ("ink/bg", c["ink"], c["bg"], 4.5),
            ("ink/panel", c["ink"], c["panel"], 4.5),
            ("ink2/panel", c["ink2"], c["panel"], 4.5),
            ("mute/panel", c["mute"], c["panel"], 4.5),
            ("on_accent/accent", c["on_accent"], c["accent"], 4.5),
            ("danger/panel", c["danger"], c["panel"], 4.5),
            ("pass/panel", c["pass"], c["panel"], 4.5),
            ("warn/panel", c["warn"], c["panel"], 4.5),
            ("focus/panel", c["focus"], c["panel"], 3.0),
        ]
        for name, fg, bg, mn in checks:
            r = _ratio(fg, bg)
            if r < mn:
                fails.append(f"{key} {name}: {r:.2f} < {mn}")
    assert not fails, "대비 미달:\n" + "\n".join(fails)


def test_instrument_profile_pins_baseline():
    """instrument 프로필 = 현행 리터럴 — 기존 클램프·a2 테스트 무수정 통과 보장."""
    p = theme.VARIANTS["instrument"].profile
    assert (p.thumb_default_px, p.control_h, p.control_h_lg) == (140, 34, 46)
    assert (p.chip_w, p.chip_h, p.toggle_w, p.toggle_h) == (74, 24, 44, 40)
    assert (p.page_margin, p.section_gap) == (40, 20)
    assert p.chip_style == "pill" and p.row_style == "card"
