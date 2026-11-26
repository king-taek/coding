"""고른 사진 표시는 한 모양 — '강조색 2px 테두리 + 옅은 배경 · 모서리 8' (E1a, 현택님 2026-11-24).

기준은 다중 선택 타일(`bulk_select_dialog._SelectTile`)이다.  같은 방식(인스턴스 스타일)으로
칠하는 다른 사진 고르기 화면이 이 값과 갈라지지 않게 못 박는다.
Overkill · 어느 Zone 타일의 링만 모양(E1b · E1c 'B')은 의도라 여기서 보지 않는다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui import theme                                       # noqa: E402
from aoi_verification.app.ui.widgets.bulk_select_dialog import _SelectTile       # noqa: E402
from aoi_verification.app.ui.widgets.slot_mapping_dialog import _list_sel_qss    # noqa: E402
from aoi_verification.app.ui.widgets.unmatched_review_dialog import _CandidateTile  # noqa: E402


def _selected_rule(qss: str) -> str:
    return qss.split(":selected", 1)[1] if ":selected" in qss else qss


@pytest.mark.parametrize("qss", [
    _CandidateTile._sel_style(),
    _selected_rule(_list_sel_qss()),
], ids=["매치 없음 검토 후보", "슬롯 짝짓기 사진 목록"])
def test_selected_photo_looks_like_the_bulk_tile(qss):
    ref = _SelectTile._sel_style()
    for part in (f"2px solid {theme.ACCENT}", f"background: {theme.ACCENT_TINT_SOFT}"):
        assert part in ref, "기준(다중 선택 타일)이 바뀌었다 — 이 테스트도 같이 본다"
        assert part in qss, f"{part!r} 가 없다: {qss}"
    assert "border-radius: 8px" in ref and "8px" in _list_sel_qss()
    assert theme.FOCUS not in qss or theme.FOCUS == theme.ACCENT
