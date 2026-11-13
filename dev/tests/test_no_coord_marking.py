"""좌표 매칭 중 좌표를 못 읽어 사진 모양(유사도)으로 짝을 찾은 기준 사진 — 검토 화면 표시.

예전엔 앱 안에 목록(``CoordScheduler.classical_refs``)만 남고 화면에는 아무 표시가 없었다.
그 줄의 점수는 거리가 아니라 유사도인데 거리(µm)로 읽혔고, 사용자는 모든 짝이 좌표로
맞춰졌다고 믿었다(PR #83 때 발견).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                       # noqa: E402
from aoi_verification.app.models.result import MatchResult   # noqa: E402
from aoi_verification.app.ui.pages.match_review_page import (  # noqa: E402
    MatchReviewPage)


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    return styled_qapp


_COORD = MatchResult(slot="A", ref_path=Path("/tmp/r1.png"), val_path=Path("/tmp/v1.png"), score=0.6)
_SIM = MatchResult(slot="A", ref_path=Path("/tmp/r2.png"), val_path=Path("/tmp/v2.png"), score=0.83)


def _page(qapp, *, coord_mode=True, classical=(("A", Path("/tmp/r2.png")),)):
    page = MatchReviewPage()
    page.load_state([_COORD, _SIM], coord_mode=coord_mode, tolerance=500.0,
                    coord_classical_refs=classical)
    page.resize(1200, 700)
    page.show()
    for _ in range(5):
        qapp.processEvents()
    return page


def test_banner_counts_the_photos_matched_without_coordinates(qapp):
    page = _page(qapp)
    try:
        assert page.no_coord_banner.isVisibleTo(page)
        assert page.no_coord_banner.text() == i18n.KO.REVIEW_NO_COORD_BANNER_FMT.format(n=1)
    finally:
        page.close()


def test_only_that_row_is_tagged_and_shows_similarity(qapp):
    page = _page(qapp)
    try:
        rows = {r.match.ref_path.name: r for r in page._rows}
        assert rows["r2.png"].no_coord and not rows["r1.png"].no_coord
        # 유사도 줄은 거리(µm)가 아니라 % 로 보인다
        assert "µm" not in rows["r2.png"]._val_score_label.text()
        assert "µm" in rows["r1.png"]._val_score_label.text()
    finally:
        page.close()


@pytest.mark.parametrize("coord_mode, classical", [(True, ()), (False, (("A", Path("/tmp/r2.png")),))])
def test_no_banner_without_fallback_or_outside_coordinate_mode(qapp, coord_mode, classical):
    page = _page(qapp, coord_mode=coord_mode, classical=classical)
    try:
        assert not page.no_coord_banner.isVisibleTo(page)
        assert not any(r.no_coord for r in page._rows)
    finally:
        page.close()
