"""테마 토큰/QSS 템플릿 — 렌더 무결성 + 구팔레트(네온) hex 재유입 가드."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_UI_DIR = _REPO / "aoi_verification" / "app" / "ui"

# 구(네온) 팔레트 — 새 코드/QSS 에 다시 들어오면 안 되는 값들.
_OLD_HEXES = (
    "#39FF14", "#FF2D55", "#FFD600", "#00FFA3", "#7FB3D5", "#0A0E1A",
    "#0E1424", "#1F2A3F", "#050810", "#E5F4FF", "#9CC5E8", "#00C853",
    "#FF6B35", "#111827",
)


def _render():
    theme = pytest.importorskip("aoi_verification.app.ui.theme")
    qss = (_UI_DIR / "style.qss").read_text(encoding="utf-8")
    return theme, theme.render_qss(qss)


def test_render_substitutes_every_token():
    _, out = _render()
    assert "$" not in out, "치환 안 된 $token 잔존"


def test_render_contains_new_palette_and_focus_ring():
    theme, out = _render()
    assert theme.ACCENT in out
    assert theme.FOCUS in out
    # 포커스 링 규칙이 실제로 존재해야 한다 (접근성 회귀 방지).
    assert re.search(r"QPushButton:focus\s*\{[^}]*" + re.escape(theme.FOCUS), out)


def test_qss_template_has_no_old_palette():
    qss = (_UI_DIR / "style.qss").read_text(encoding="utf-8")
    hits = [h for h in _OLD_HEXES if h.lower() in qss.lower()]
    assert not hits, f"style.qss 에 구팔레트 잔존: {hits}"


# NOTE: ui/ 파이썬 인라인 hex 가드는 인라인 색 일괄 치환 커밋에서 추가된다
# (그 전까지는 기존 페이지들이 구팔레트를 여전히 들고 있어 의도적으로 보류).
