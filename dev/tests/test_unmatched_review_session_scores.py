"""실패 검토 창의 점수 **보관 수명**과 **출처 분류** — C-1·C-2 의 반례들.

`ad8de88` 이 고친 방향(이 창이 계산한 점수를 Stage 2 의 공유 ``SlotScoreCache``
에 섞지 않는다)은 옳지만, 보관처를 **창 인스턴스**에 매단 탓에 두 가지가 깨졌다.
`result_page` 는 [실패 검토] 를 누를 때마다 창을 **새로** 만들기 때문이다.

C-1a **차순위 후보 소실** — 좌표 모드에서 이웃을 못 찾은 ref 는 `coord_matcher` 가
      ``results[key] = []`` (빈 목록 = falsy)를 넣고 실패 목록에 올린다.  그 ref 를
      실패 검토에서 확정하면, 검토 화면이 후보를 만드는
      ``match_page.build_candidates_by_ref`` 는 falsy 인 선계산 결과를 지나쳐
      점수 캐시로 내려오는데 거기엔 아무것도 없다 → 실측 후보 6 → 0,
      화면 차순위 5 → 0.

C-1b **재계산 비용** — 같은 창을 다시 열면 같은 쌍을 전부 다시 계산한다
      (실측: 후보 40장 재계산 112 ms, 쌍당 2.8 ms).

C-2  **"계산 안 함" 이 진짜 유사도까지 싸잡는다** — 좌표 세션이라도 좌표가 없는
     ref 는 `coord_matcher` 가 **고전 유사도**로 폴백 채점해 같은
     ``_fast_results`` 에 넣는다.  실측 0.83·0.61(=83%·61%)이 '계산 안 함' 으로
     표시되고 요약에도 "좌표 거리 순" 이라 거짓으로 적혔다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                                # noqa: E402
from aoi_verification.app.models.result import MatchResult, MissEntry  # noqa: E402
from aoi_verification.app.models.slot import ImageItem               # noqa: E402
from aoi_verification.app.ui.pages.match_page import (                # noqa: E402
    MatchPage, Stage2State)
from aoi_verification.app.ui.pages.match_review_page import (         # noqa: E402
    MatchReviewPage)
import aoi_verification.app.ui.widgets.unmatched_review_dialog as M   # noqa: E402


_REF = Path("/tmp/sess_ref_A1.jpg")


def _fake_pipeline(monkeypatch, value: float = 0.5) -> list:
    """무거운 cv2 경로 대신 '이 창이 계산한 값' — 계산 횟수도 센다."""
    from aoi_verification.app.similarity import pipeline as P
    calls: list = []
    monkeypatch.setattr(P, "extract", lambda src, **kw: src)
    monkeypatch.setattr(P, "score",
                        lambda a, b, **kw: calls.append((a, b)) or value)
    return calls


def _vals(n: int) -> list[ImageItem]:
    return [ImageItem(slot="A1", path=Path(f"/tmp/sess_v{i}.jpg"), side="val")
            for i in range(n)]


def _coord_page(qapp, vals: list[ImageItem]) -> MatchPage:
    """좌표 매칭이 방금 끝난 Stage 2 — 이 ref 는 이웃을 못 찾았다(빈 목록)."""
    page = MatchPage()
    page._state = Stage2State(queue=[], val_pool={"A1": list(vals)})
    page._coord_mode = True
    page._fast_results[("A1", _REF)] = []
    return page


def _open_review(page: MatchPage, vals: list[ImageItem]):
    """`result_page` 와 **같은 배선**으로 실패 검토 창을 연다."""
    ctx = page.review_context()
    dlg = M.UnmatchedReviewDialog(
        [MissEntry(slot="A1", side="ref", path=_REF)],
        {"A1": list(vals)},
        score_cache=ctx.score_cache,
        fast_results=ctx.fast_results,
        coord_mode=ctx.coord_mode,
        review_scores=ctx.review_scores,
        coord_classical_refs=ctx.coord_classical_refs,
    )
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()
    return dlg


# ---------------------------------------------------------------------------
# C-1a — 확정한 매치의 차순위 후보가 살아남는다
# ---------------------------------------------------------------------------
def test_runners_up_survive_after_the_review_window_is_gone(qapp, monkeypatch):
    """창이 사라져도 그 창이 계산한 점수는 검토 화면이 찾을 수 있어야 한다."""
    _fake_pipeline(monkeypatch, 0.5)
    vals = _vals(6)
    page = _coord_page(qapp, vals)
    try:
        dlg = _open_review(page, vals)
        assert len(dlg._cand_tiles) == 6, "창 안에서는 6장이 보였다"
        dlg.close()
        dlg.deleteLater()

        match = MatchResult(slot="A1", ref_path=_REF,
                            val_path=vals[0].path, score=0.5)
        cbr = page.build_candidates_by_ref([match])
        assert len(cbr[("A1", _REF.name)]) == 6, \
            "실패 검토로 확정한 매치의 후보가 사라졌다(실측 6 → 0)"

        review = MatchReviewPage()
        try:
            review._candidates_by_ref = cbr
            assert len(review._lookup_runners_up(
                match, page._score_cache, {"A1": vals})) == 5, \
                "화면 차순위가 사라졌다(실측 5 → 0)"
            # 선계산 후보 목록이 없을 때 타는 폴백 경로도 같은 곳을 봐야 한다.
            review._candidates_by_ref = None
            review._review_scores = page.review_context().review_scores
            assert len(review._lookup_runners_up(
                match, page._score_cache, {"A1": vals})) == 5, \
                "폴백 경로에서만 차순위가 사라진다"
        finally:
            review.deleteLater()
    finally:
        page.deleteLater()


def test_the_window_still_keeps_its_scores_out_of_the_shared_cache(
        qapp, monkeypatch):
    """C-1 의 **진짜 수리**는 그대로다 — 공유 캐시는 오염되지 않는다.

    오염되면 ``match_page._launch_matcher`` 의 ``has_all_pairs`` 가 뒤집혀
    같은 세션의 2회차 매칭이 이 창의 값을 정답으로 서빙한다."""
    _fake_pipeline(monkeypatch, 0.5)
    vals = _vals(4)
    page = _coord_page(qapp, vals)
    try:
        dlg = _open_review(page, vals)
        dlg.close()
        dlg.deleteLater()
        assert page._score_cache.size() == 0, \
            "실패 검토 점수가 Stage 2 의 공유 캐시에 섞였다"
        assert page._score_cache.has_all_pairs(
            "A1", _REF, [v.path for v in vals]) is False
        # 그러나 세션 보관처에는 있다 — 그래야 차순위·재계산이 산다.
        assert page._review_scores.get_pair("A1", _REF, vals[0].path) == \
            pytest.approx(0.5)
    finally:
        page.deleteLater()


# ---------------------------------------------------------------------------
# C-1b — 창을 다시 열어도 재계산이 없다
# ---------------------------------------------------------------------------
def test_reopening_the_window_recomputes_nothing(qapp, monkeypatch):
    """[실패 검토] 는 누를 때마다 창을 새로 만든다 — 비용이 매번 들면 안 된다."""
    calls = _fake_pipeline(monkeypatch, 0.5)
    vals = _vals(20)
    page = _coord_page(qapp, vals)
    try:
        dlg = _open_review(page, vals)
        first = len(calls)
        assert first == 20, f"첫 열기는 전부 계산한다: {first}"
        dlg.close()
        dlg.deleteLater()

        dlg2 = _open_review(page, vals)          # 새 창 인스턴스
        assert len(dlg2._cand_tiles) == 20
        dlg2.close()
        dlg2.deleteLater()
        assert len(calls) == first, \
            f"두 번째 열기가 {len(calls) - first} 쌍을 다시 계산했다"
    finally:
        page.deleteLater()


# ---------------------------------------------------------------------------
# C-2 — 고전 폴백 점수는 진짜 유사도다
# ---------------------------------------------------------------------------
def _big_coord_dialog(*, classical: bool):
    """후보 350장(≥300) → CPU 재계산 금지.  선계산 점수를 그대로 쓴다."""
    vals = _vals(350)
    # 0.83 / 0.61 — 좌표 폴백이 매긴 **고전 유사도**(실측값).
    fres = {("A1", _REF): [(vals[0].path, 0.83), (vals[1].path, 0.61)]}
    dlg = M.UnmatchedReviewDialog(
        [MissEntry(slot="A1", side="ref", path=_REF)],
        {"A1": vals}, score_cache=None, fast_results=fres, coord_mode=True,
        coord_classical_refs=(frozenset({("A1", _REF)}) if classical
                              else frozenset()),
    )
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()
    return dlg


def test_classical_fallback_scores_are_shown_as_similarity(qapp):
    """좌표 세션이어도 폴백 채점된 ref 의 83%·61% 는 백분율로 보여야 한다."""
    dlg = _big_coord_dialog(classical=True)
    try:
        texts = [t._score_label.text() for t in dlg._cand_tiles]
        assert texts and all("%" in t for t in texts), \
            f"진짜 유사도를 '계산 안 함' 으로 표시했다: {texts}"
        assert i18n.KO.SCORE_NOT_COMPUTED not in texts
        assert "83" in texts[0] and "61" in texts[1]
        assert dlg.candidates_summary.text() == \
            i18n.KO.UNMATCHED_CAND_COUNT_FMT.format(n=2), \
            "순서 기준을 '좌표 거리 순' 이라고 사실과 다르게 적었다"
    finally:
        dlg.deleteLater()


def test_coord_distance_scores_are_still_not_shown_as_a_percent(qapp):
    """같은 세션의 **좌표 거리** ref 는 종전대로 '계산 안 함' 이다(C-2 회귀 방지)."""
    dlg = _big_coord_dialog(classical=False)
    try:
        texts = [t._score_label.text() for t in dlg._cand_tiles]
        assert all(t == i18n.KO.SCORE_NOT_COMPUTED for t in texts), texts
        assert dlg.candidates_summary.text() == \
            i18n.KO.UNMATCHED_CAND_COUNT_COORD_FMT.format(n=2)
    finally:
        dlg.deleteLater()


def test_predicate_splits_the_two_score_kinds():
    """순수 헬퍼 — 고전 폴백 표식이 있는 ref 만 백분율 쪽으로 간다."""
    fres = {("A1", _REF): [(Path("/tmp/sess_v0.jpg"), 0.83)]}
    assert M._reuses_coord_scores(True, False, fres, "A1", _REF) is True
    assert M._reuses_coord_scores(True, False, fres, "A1", _REF,
                                  frozenset({("A1", _REF)})) is False
    # 다른 ref 의 표식은 영향이 없다.
    assert M._reuses_coord_scores(True, False, fres, "A1", _REF,
                                  frozenset({("A1", Path("/tmp/other.jpg"))})) \
        is True


def test_coord_scheduler_marks_which_refs_were_scored_classically(monkeypatch):
    """표식의 출처 — `CoordScheduler` 가 폴백으로 채점한 ref 만 담는다.

    ★ **점수 값은 건드리지 않는다**(순수 메타데이터).  좌표 있는 ref 는 거리
    인코딩, 없는 ref 는 고전 유사도 — 값 자체는 종전 그대로여야 한다."""
    from aoi_verification.app.workers import coord_matcher as CM

    with_coord = ImageItem(slot="A1", path=Path("/tmp/r_has.jpg"), side="ref")
    no_coord = ImageItem(slot="A1", path=Path("/tmp/r_none.jpg"), side="ref")
    val = ImageItem(slot="A1", path=Path("/tmp/v.jpg"), side="val")

    class _C:
        def __init__(self, col, row, x, y):
            self.col, self.row, self.x, self.y = col, row, x, y

    monkeypatch.setattr(CM, "_resolve_batch", lambda paths: {
        with_coord.path: _C(1, 1, 0.0, 0.0),
        val.path: _C(1, 1, 10.0, 0.0),
    })

    class _Cand:
        def __init__(self, item, score):
            self.item, self.score = item, score

    monkeypatch.setattr(CM, "score_ref_classical",
                        lambda ref, vals, **kw: [_Cand(vals[0], 0.83)])

    results: dict = {}
    sched = CM.CoordScheduler([("A1", [with_coord, no_coord], [val])],
                              results=results)
    sched._run()

    assert sched.classical_refs == {("A1", no_coord.path)}
    assert ("A1", with_coord.path) not in sched.classical_refs
    # 값 불변 — 좌표 쪽은 거리 인코딩(dist 10, tol 200 → 0.95), 폴백은 0.83.
    assert results[("A1", with_coord.path)] == [(val.path, pytest.approx(0.95))]
    assert results[("A1", no_coord.path)] == [(val.path, pytest.approx(0.83))]
