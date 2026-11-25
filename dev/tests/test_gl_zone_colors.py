"""GL 상태줄의 하한·상한 글자색 = 차트 색 (E2b, 현택님 2026-11-24).

차트는 하한 이하를 (47,111,224) 파랑 · 상한 이상을 (229,50,42) 빨강으로 칠하는데, 아래
상태줄은 강조색·위험색을 써 같은 '하한' 이 두 파랑으로 보였다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui.widgets import gl_charts                      # noqa: E402

_QSS = (Path(__file__).resolve().parents[2]
        / "aoi_verification" / "app" / "ui" / "style.qss").read_text(encoding="utf-8")


def _color_of(role: str) -> str:
    m = re.search(r'QLabel\[role="%s"\]\s*\{[^}]*?color:\s*([^;]+);' % role, _QSS)
    assert m, role
    return m.group(1).strip().upper()


def test_status_line_zone_colors_match_the_chart():
    assert _color_of("glZoneLow") == gl_charts._LOW.name().upper()
    assert _color_of("glZoneHigh") == gl_charts._HIGH.name().upper()
