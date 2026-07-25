"""'도면' 단일 디자인 테마 — 렌더 무결성·대비(AA 여유)·프로필 기준선 고정."""

from __future__ import annotations

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


def test_qss_renders_fully():
    """템플릿의 모든 ``$token`` 이 치환된다(오타·누락 조기 노출)."""
    out = theme.render_qss(_QSS)
    assert "$" not in out


def test_tokens_cover_colors_and_structure():
    t = theme.TOKENS
    for key in theme.COLORS:
        assert t.get(key), f"{key} 토큰 누락"
    for key in ("font_base_sz", "radius", "chip_radius", "row_radius",
                "pad_control", "focus_w", "check_sz", "row_divider",
                "score_rule", "chip_bg_over", "thumb_frame"):
        assert t.get(key), f"{key} 토큰 누락"


def test_no_old_palette():
    for mode, palette in theme.PALETTES.items():
        for name, hexv in palette.items():
            assert hexv.upper() not in _OLD_HEXES, f"{mode}.{name} = 구팔레트 {hexv}"


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_contrast_has_aa_headroom_both_modes(mode):
    """라이트·다크 **양쪽** 전 기능 색상쌍이 AA 여유(≥5.0) — 포커스 링은 ≥4.5."""
    c = theme.PALETTES[mode]
    fails = []
    checks = [
        ("ink/bg", c["ink"], c["bg"], 7.0),
        ("ink/panel", c["ink"], c["panel"], 7.0),
        ("ink2/panel", c["ink2"], c["panel"], 5.0),
        ("mute/panel", c["mute"], c["panel"], 5.0),
        ("accent/panel", c["accent"], c["panel"], 5.0),
        ("pass/panel", c["pass"], c["panel"], 5.0),
        ("danger/panel", c["danger"], c["panel"], 5.0),
        ("warn/panel", c["warn"], c["panel"], 5.0),
        ("focus/panel", c["focus"], c["panel"], 4.5),
        ("on_accent/accent", c["on_accent"], c["accent"], 4.5),
    ]
    for name, fg, bg, mn in checks:
        r = _ratio(fg, bg)
        if r < mn:
            fails.append(f"{name}: {r:.2f} < {mn}")
    assert not fails, "대비 미달:\n" + "\n".join(fails)


def test_thumb_frame_separates_from_panel():
    """썸네일 프레임이 시트 면과 구분돼야(어두운 다이가 묻히지 않게)."""
    assert _ratio(theme.COLORS["thumb_frame"], theme.COLORS["panel"]) >= 1.6


def test_profile_pins_datum_baseline():
    """'도면' 프로필 리터럴 고정 — 클램프·a2 테스트의 전제(폭 예약 등)를 지킨다."""
    p = theme.PROFILE
    assert (p.thumb_default_px, p.control_h, p.control_h_lg) == (118, 32, 40)
    assert (p.chip_w, p.chip_h, p.toggle_w, p.toggle_h) == (88, 20, 52, 30)
    assert (p.page_margin, p.section_gap) == (32, 28)
    # 모서리는 중간 라운드(8~10px) — '너무 각지다'는 지적 반영.
    assert (p.radius, p.radius_sm, p.chip_radius) == (9, 6, 8)
    assert p.row_radius == 0                       # 하이라인 행은 면이 없어 무의미
    assert (p.title_weight, p.title_tracking) == (300, -1)        # 얇은 표제
    assert p.row_gap == 0                                          # 하이라인 눈금


def test_single_design_no_variant_machinery():
    """디자인은 하나뿐 — 변형 전환 기계가 남아 있지 않아야 한다."""
    for gone in ("VARIANTS", "set_variant", "variant_keys", "CURRENT_VARIANT",
                 "DEFAULT_VARIANT", "Variant"):
        assert not hasattr(theme, gone), f"theme.{gone} 잔존"


def test_mainwindow_has_no_variant_switching():
    import inspect
    from aoi_verification.app.ui import main_window as mw
    src = inspect.getsource(mw.MainWindow)
    assert "def _build_pages" in src
    for gone in ("_on_variant_selected", "_rebuild_pages", "variant_selected"):
        assert gone not in src, f"{gone} 잔존"


def test_apply_to_app_renders(tmp_path, monkeypatch):
    """apply_to_app 렌더 경로가 예외 없이 도는지(QSS 완전 치환)."""
    import pytest
    pytest.importorskip("PyQt6.QtWidgets")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    theme.apply_to_app(app)
    assert "$" not in app.styleSheet()
