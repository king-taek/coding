"""좌표 매칭 후보 선택(_select_coord_candidates) 단위 테스트.

검토 화면에 보여줄 후보 규칙을 순수 로직으로 검증한다:
  · 최소 거리 ≤ CONFIDENT_DIST  → 가장 가까운 1장만.
  · 그 외                        → tol×3 이내 후보 전부(거리 오름차순).

coord_matcher 는 PyQt6/numpy 등 무거운 의존성을 import 하므로 importorskip 으로 게이트한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

cm = pytest.importorskip("aoi_verification.app.workers.coord_matcher")

_select = cm._select_coord_candidates
TOL = 500.0


def _p(name: str) -> Path:
    return Path(f"/tmp/{name}.jpg")


def test_confident_returns_single():
    """최소 거리 ≤ 20 이면 후보 다수여도 가장 가까운 1장만 반환."""
    within3 = [(_p("a"), 10.0), (_p("b"), 200.0), (_p("c"), 800.0)]
    out = _select(within3, TOL)
    assert len(out) == 1
    assert out[0][0] == _p("a")
    # dist=10 ≤ tol → 양수 score = 1 - 10/500
    assert out[0][1] == pytest.approx(1.0 - 10.0 / TOL)


def test_ambiguous_returns_all_sorted():
    """20 이하가 없으면 tol×3 이내 후보를 전부 거리 오름차순으로 반환."""
    within3 = [(_p("far"), 900.0), (_p("near"), 300.0), (_p("mid"), 600.0)]
    out = _select(within3, TOL)
    assert [p for p, _ in out] == [_p("near"), _p("mid"), _p("far")]
    # tol 내(300)는 양수, tol 초과(600,900)는 음수 score → '허용범위 초과' 표식
    assert out[0][1] == pytest.approx(1.0 - 300.0 / TOL)   # > 0
    assert out[1][1] == pytest.approx(-(600.0 / TOL))       # < 0
    assert out[2][1] == pytest.approx(-(900.0 / TOL))       # < 0


def test_score_roundtrips_to_distance():
    """score → 거리 역산(_RunnerUpTile 규칙)이 원래 거리와 일치(round-trip)."""
    within3 = [(_p("x"), 300.0), (_p("y"), 750.0)]
    out = _select(within3, TOL)
    for (_, score), dist in zip(out, (300.0, 750.0)):
        recovered = (1.0 - score) * TOL if score >= 0 else (-score) * TOL
        assert recovered == pytest.approx(dist)


def test_empty_input():
    assert _select([], TOL) == []


def test_boundary_exactly_confident_dist():
    """경계값: 최소 거리 == CONFIDENT_DIST 도 '1장만' 쪽으로 본다(≤)."""
    within3 = [(_p("a"), cm.CONFIDENT_DIST), (_p("b"), 400.0)]
    out = _select(within3, TOL)
    assert len(out) == 1
    assert out[0][0] == _p("a")
