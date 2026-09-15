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


def test_confident_dist_capped_at_half_tolerance():
    """die 가 작은 자재는 tol 을 낮춰 쓴다 — 그때도 차순위가 살아있어야 한다.

    tol 이 CONFIDENT_DIST(20 µm) 이하로 내려가면 '확정' 반경이 tol 을 덮어
    **모든 매치가 후보 1장**이 돼 사용자가 고를 여지가 사라졌다. tol 의 절반으로 묶는다.
    tol=500(기본)에서는 20 그대로라 기존 동작이 바뀌지 않는다."""
    assert cm._confident_dist(TOL) == cm.CONFIDENT_DIST      # 기존 동작 불변
    assert cm._confident_dist(20.0) == 10.0

    within3 = [(_p("a"), 12.0), (_p("b"), 18.0)]
    assert len(_select(within3, 20.0)) == 2                  # 차순위가 남는다
    assert len(_select(within3, TOL)) == 1                   # tol 이 크면 '확정' 1장


# ---------------------------------------------------------------------------
# _match_neighbors — (col,row) ±1 이웃 게이트 (정답 도구 Module_Compare 기준)
# ---------------------------------------------------------------------------
_match = cm._match_neighbors


def test_match_neighbors_allows_off_by_one_row():
    """dev/좌표 확인 실측: KLA(col1,row3,x8653,y39318) ↔ Camtek(col1,row4,x8722,y39216).
    row 가 1 어긋나지만 ±1 게이트로 매칭돼야 한다(과거 정확 일치 게이트는 전멸했음).
    ※ 파서의 row 정규화(6−Row) 이후 정상 데이터는 정확 일치하지만, 장비 간 ±1
    어긋남 방어용으로 게이트는 정답 도구(Module_Compare) 규약대로 ±1 을 유지한다."""
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


# ---------------------------------------------------------------------------
# 좌표 파싱 단계의 진행 보고 (CLAUDE.md 로딩 계약)
# ---------------------------------------------------------------------------
# 증상: 좌표 매칭을 돌리면 '좌표 파싱 중…' 구간에서 진척도가 뜨지 않았다.  원인은
# ``resolve_batch`` 가 이미 갖고 있는 ``progress(done, total)`` 콜백을 **안 넘겨서**,
# 사진 수천 장을 훑는 그 구간 동안 워커가 보고할 것이 없었던 것이다.
class _Coord:
    """``DefectCoord`` 대역 — 스케줄러가 보는 네 필드만 있으면 된다."""

    def __init__(self, col: int, row: int, x: float, y: float) -> None:
        self.col, self.row, self.x, self.y = col, row, x, y


def _items(slot: str, side: str, n: int):
    return [cm.ImageItem(slot, Path(f"/tmp/{slot}_{side}{i}.jpg"), side)
            for i in range(n)]


@pytest.fixture
def scheduler_with_stub_resolve(monkeypatch):
    """``_resolve_batch`` 를 가벼운 더미로 바꿔 실제 파일 없이 ``_run`` 을 돈다."""
    def fake_resolve_batch(paths, progress=None):
        paths = list(paths)
        assert progress is not None, (
            "좌표 파싱에 진행 콜백이 안 넘어왔다 — 바가 그 구간 동안 멈춘다")
        for i, _p in enumerate(paths, start=1):
            progress(i, len(paths))
        return {p: _Coord(1, 1, 0.0, 0.0) for p in paths}

    monkeypatch.setattr(cm, "_resolve_batch", fake_resolve_batch)
    refs, vals = _items("A", "ref", 2), _items("A", "val", 3)
    sched = cm.CoordScheduler([("A", refs, vals)])
    seen: list[tuple[int, int]] = []
    phases: list[str] = []
    sched.signals.progress.connect(lambda d, t: seen.append((d, t)))
    sched.signals.phase.connect(phases.append)
    sched._run()                       # QThread.run() 을 동기 실행
    return seen, phases


def test_parse_phase_reports_progress(scheduler_with_stub_resolve):
    """파싱 구간에서 사진 1장(콜백 1회)마다 진행이 올라간다."""
    seen, phases = scheduler_with_stub_resolve
    total_paths = 5                    # ref 2 + val 3
    parse = seen[:total_paths]
    assert [d for d, _t in parse] == [1, 2, 3, 4, 5], f"진행이 안 올라갔다: {seen}"
    assert all(t == total_paths for _d, t in parse)
    assert phases[0] == cm.i18n.KO.PHASE_COORD_PARSE


def test_progress_restarts_at_the_pair_scale(scheduler_with_stub_resolve):
    """파싱(장수)과 매칭(쌍 수)은 총량이 다르다 — 바가 '파싱 100%' 에 걸리지 않는다."""
    seen, _phases = scheduler_with_stub_resolve
    total_pairs = 2 * 3
    after = seen[5:]
    assert after[0] == (0, total_pairs), (
        f"매칭 단계 첫 보고가 새 범위(0/{total_pairs})가 아니다: {after[:3]}")
    assert after[-1] == (total_pairs, total_pairs)
    # 같은 총량 구간 안에서는 단조 증가 (로딩바가 뒤로 가지 않는다).
    dones = [d for d, _t in after]
    assert dones == sorted(dones)
