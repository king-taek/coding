"""매치 검토 — 이미지 확대 시 행 폭을 뷰포트로 클램프해 가로 넘침/버튼 잘림 방지."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.models.result import MatchResult          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _row(qapp):
    from aoi_verification.app.ui.pages.match_review_page import _MatchRow
    m = MatchResult(slot="S1", ref_path=Path("/tmp/r.jpg"),
                    val_path=Path("/tmp/v.jpg"), score=0.9)
    return _MatchRow(m, runners_up=[], thumb_px=140)


def test_clamp_keeps_row_within_narrow_width(qapp):
    from aoi_verification.app.ui.pages.match_review_page import _SIZE_MIN_PX
    row = _row(qapp)
    row._row_width = lambda: 900          # 좁은 뷰포트로 고정
    row.set_thumb_size(360)               # 최대 요청
    # 적용 크기는 360 보다 작게 클램프됨.
    assert row._thumb_px < 360
    assert row._thumb_px >= _SIZE_MIN_PX
    # 슬롯 + 이미지2 + 화살표/버튼/여백(reserved) 이 행 폭 이내 → 버튼 안 잘림.
    assert 2 * row._thumb_px + row._reserved_fixed_px() <= 900
    row.deleteLater()


def test_no_clamp_on_wide_width(qapp):
    row = _row(qapp)
    row._row_width = lambda: 1600         # 넓은 뷰포트
    row.set_thumb_size(360)
    # 충분히 넓으면 요청값 그대로 적용.
    assert row._thumb_px == 360
    row.deleteLater()


def test_inline_first_cols_can_be_zero_when_large(qapp):
    row = _row(qapp)
    row._row_width = lambda: 760          # 두 이미지로 거의 꽉 차는 폭
    row.set_thumb_size(360)               # 클램프된 큰 이미지
    # 인라인 차순위 자리가 없으면 0 (가로 넘침 방지).
    assert row._first_cols() == 0
    row.deleteLater()


def test_requested_size_remembered_for_reclamp(qapp):
    row = _row(qapp)
    row._row_width = lambda: 900
    row.set_thumb_size(360)
    assert row._requested_thumb_px == 360   # 요청값 보존(리사이즈 시 재클램프용)
    narrow_applied = row._thumb_px
    # 넓어지면 요청값 기준으로 더 크게 재적용 가능.
    row._row_width = lambda: 1600
    row.set_thumb_size(row._requested_thumb_px)
    assert row._thumb_px > narrow_applied
    row.deleteLater()


def test_lot_counts_label_says_slot():
    """#1 문구: 'LOT' 이 아니라 'Slot' 으로 표기."""
    from aoi_verification.app import i18n
    assert i18n.KO.LOT_COUNTS_PREFIX.startswith("Slot")
