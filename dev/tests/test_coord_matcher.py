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


# ---------------------------------------------------------------------------
# _match_neighbors — (col,row) ±1 이웃 게이트 (정답 도구 Module_Compare 기준)
# ---------------------------------------------------------------------------
_match = cm._match_neighbors


def test_match_neighbors_allows_off_by_one_row():
    """dev/좌표 확인 실측: KLA(col1,row3,x8653,y39318) ↔ Camtek(col1,row4,x8722,y39216).
    row 가 1 어긋나지만 ±1 게이트로 매칭돼야 한다(과거 정확 일치 게이트는 전멸했음)."""
    cam = _p("camtek")
    vmap = {(1, 4): [(cam, 8722.0, 39216.0)]}
    out = _match(8653.0, 39318.0, 1, 3, vmap, TOL)
    assert len(out) == 1 and out[0][0] == cam
    # 거리 ≈ hypot(69,102) ≈ 123 ≤ tol(500) → 양수 score.
    assert out[0][1] > 0


def test_match_neighbors_same_die():
    cam = _p("same")
    out = _match(100.0, 100.0, 2, 2, {(2, 2): [(cam, 110.0, 90.0)]}, TOL)
    assert len(out) == 1 and out[0][0] == cam


def test_match_neighbors_excludes_far_die():
    """col/row 가 2 이상 어긋난 die 후보는 모이지 않는다(±1 초과)."""
    far = _p("far")
    out = _match(8653.0, 39318.0, 1, 3, {(3, 3): [(far, 8653.0, 39318.0)]}, TOL)
    assert out == []


def test_match_neighbors_diagonal_neighbor_included():
    cam = _p("diag")
    out = _match(100.0, 100.0, 1, 1, {(2, 2): [(cam, 105.0, 95.0)]}, TOL)
    assert len(out) == 1 and out[0][0] == cam


def test_match_neighbors_distance_beyond_3tol_fails():
    far = _p("toofar")
    # 같은 die 지만 die-내부 거리 > 3×tol → 매치 실패.
    out = _match(0.0, 0.0, 1, 1, {(1, 1): [(far, 9000.0, 0.0)]}, TOL)
    assert out == []
