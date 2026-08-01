"""매치 검토 화면의 점수 표시 — 엔진에 맞는 이름 + 사진 밑 표기.

두 가지 회귀:
1. 구형(유사도) 엔진에서도 점수 열 머리글이 '거리(µm)' 로 고정돼 있었다.
   `_build_list_header` 가 `coord_mode` 를 보지 않았고, 헤더는 생성자에서 만들어지는데
   모드는 `load_state` 에서야 정해진다.
2. 1위 매치(검증) 사진 밑에는 점수가 없고 오른쪽 열에만 있었다.  차순위 후보 타일은
   처음부터 사진 밑에 점수를 달아서, 같은 종류의 값을 두 군데서 다르게 읽어야 했다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")


from aoi_verification.app import i18n                       # noqa: E402
from aoi_verification.app.models.result import MatchResult   # noqa: E402
from aoi_verification.app.ui import theme                    # noqa: E402
from aoi_verification.app.ui.pages.match_review_page import (  # noqa: E402
    MatchReviewPage)


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    """테마 적용된 앱 — conftest 가 `setStyleSheet` 을 세션당 1회만 부른다."""
    return styled_qapp


def _page(qapp, *, coord_mode: bool, score: float, tolerance: float = 500.0):
    page = MatchReviewPage()
    match = MatchResult(slot="A", ref_path=Path("/tmp/r.png"),
                        val_path=Path("/tmp/v.png"), score=score)
    page.load_state([match], coord_mode=coord_mode, tolerance=tolerance)
    page.resize(1200, 700)
    page.show()
    for _ in range(5):
        qapp.processEvents()
    return page


# ---------------------------------------------------------------------------
# 1) 점수 열 이름이 엔진을 따른다
# ---------------------------------------------------------------------------
def test_header_says_distance_in_coordinate_mode(qapp):
    page = _page(qapp, coord_mode=True, score=0.6)
    try:
        assert page._hdr_metric.text() == i18n.KO.COL_DISTANCE
    finally:
        page.close()


def test_header_says_similarity_in_legacy_mode(qapp):
    """구형(유사도) 엔진에서는 '거리(µm)' 가 아니라 '유사도(%)'."""
    page = _page(qapp, coord_mode=False, score=0.873)
    try:
        assert page._hdr_metric.text() == i18n.KO.COL_SIMILARITY
        assert "거리" not in page._hdr_metric.text()
    finally:
        page.close()


def test_header_follows_mode_when_reloaded(qapp):
    """같은 페이지를 다른 엔진으로 다시 로드하면 머리글도 따라 바뀐다."""
    page = _page(qapp, coord_mode=True, score=0.6)
    try:
        assert page._hdr_metric.text() == i18n.KO.COL_DISTANCE
        match = MatchResult(slot="A", ref_path=Path("/tmp/r.png"),
                            val_path=Path("/tmp/v.png"), score=0.5)
        page.load_state([match], coord_mode=False, tolerance=500.0)
        for _ in range(3):
            qapp.processEvents()
        assert page._hdr_metric.text() == i18n.KO.COL_SIMILARITY
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 2) 1위 검증 사진 밑에도 점수가 나온다
# ---------------------------------------------------------------------------
def test_val_photo_has_score_underneath(qapp):
    page = _page(qapp, coord_mode=True, score=0.6)
    try:
        row = page._rows[0]
        assert row._val_score_label.text() == "200 µm"      # (1-0.6)*500
        # 오른쪽 열도 그대로 유지된다(요청: '밑에도').
        assert row._metric_label.text() == "200 µm"
    finally:
        page.close()


def test_val_score_uses_similarity_in_legacy_mode(qapp):
    page = _page(qapp, coord_mode=False, score=0.873)
    try:
        row = page._rows[0]
        assert row._val_score_label.text() == "87.3 %"
    finally:
        page.close()


def test_val_score_marks_over_tolerance(qapp):
    """허용 초과(score<0)는 차순위 타일과 같은 위험색 + 툴팁."""
    page = _page(qapp, coord_mode=True, score=-1.5)
    try:
        row = page._rows[0]
        assert row._val_score_label.text() == "750 µm"      # 1.5*500
        assert theme.DANGER.lower() in row._val_score_label.styleSheet().lower()
        assert "허용범위 초과" in row._val_score_label.toolTip()
    finally:
        page.close()


def test_val_score_container_tracks_thumb_width(qapp):
    """썸네일 크기를 바꾸면 컨테이너 폭도 따라간다(헤더 정렬 유지)."""
    page = _page(qapp, coord_mode=True, score=0.6)
    try:
        row = page._rows[0]
        row.set_thumb_size(120)
        for _ in range(3):
            qapp.processEvents()
        assert row._val_host.width() == row._thumb_px
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 3) 유사도 문구가 한 곳에서 온다 (네 곳에 복제돼 갈라져 있었다)
# ---------------------------------------------------------------------------
def test_similarity_format_is_shared():
    """포맷터는 이제 한 곳(`ui/score_fmt`)뿐이다 — 네 곳에 복제돼 갈라져 있었다."""
    from aoi_verification.app.ui.score_fmt import fmt_score
    assert fmt_score(0.873, False, 500.0) == \
        i18n.KO.SCORE_SIMILARITY_FMT.format(pct=87.3)


def test_coord_score_round_trip_boundary():
    """★ 경계값 ``dist == tol`` 은 score == 0 이고 **허용 내**다.

    coord_matcher 의 인코딩(score >= 0 → 허용 내)을 그대로 역산해야 한다.
    판정을 ``> 0`` 으로 바꾸면 그 한 점이 '초과' 로 뒤집혀 CLAUDE.md 의 좌표
    round-trip 계약이 깨진다."""
    from aoi_verification.app.ui.score_fmt import fmt_score
    assert fmt_score(0.0, True, 500.0) == i18n.KO.SCORE_DIST_FMT.format(dist=500.0)
    assert "초과" not in fmt_score(0.0, True, 500.0)
    # 음수 = 허용범위 초과 (verbose 면 접미어까지)
    assert "초과" in fmt_score(-1.0, True, 500.0)
    assert "초과" not in fmt_score(-1.0, True, 500.0, verbose=False)
    # 허용 오차가 0 이하면 500 으로 클램프 — 옛 구현과 같은 규약.
    assert fmt_score(0.0, True, 0.0) == fmt_score(0.0, True, 500.0)
