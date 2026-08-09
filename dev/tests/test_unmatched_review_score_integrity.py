"""실패 검토 화면의 점수 정합성 세 가지 — C-1 · C-2 · C-5.

셋 다 **화면에 보이는 점수와 그 출처**를 다루므로 한 파일에 모은다.

C-1  이 창이 그 자리에서 계산한 점수가 Stage 2 의 공유 ``SlotScoreCache`` 에
     섞여 들어갔다.  그 통은 ``match_page._launch_matcher`` 의 ``has_all_pairs``
     가 **정답으로 읽는 통**이라, 같은 세션에서 매칭을 다시 돌리면 Stage 2 를
     건너뛰고 이 창이 남긴 값을 서빙한다.  고친 방향은 보관처를 나누는 것 —
     읽기 순서(공유 캐시 → 실패 검토 보관처)를 그대로 두었으므로 **표시되는
     점수는 종전과 같다**(아래 두 번째 테스트가 못 박는다).

     ※ 한때 이 자리에 "Stage 2 는 cfg 를 주고 이 창은 안 줘서 특징 캐시 키가
       갈라진다" 고 적혀 있었으나 **오늘 코드에서는 재현되지 않는다**:
       ``_make_sim_cfg`` 가 만드는 두 cfg 모두 ``cache_extra()`` 가 빈 문자열이고
       ``SlotPrecomputeWorker`` 도 ``pipeline.score`` 를 cfg 없이 부른다.
     ※ 보관처의 **수명**은 창이 아니라 세션이다 — 창에 매달았더니 차순위 후보가
       사라지고 재계산이 매번 돌았다(반례와 회귀 가드:
       ``test_unmatched_review_session_scores.py``).

C-2  후보가 300장 이상이면 CPU 재계산을 건너뛰고 선계산 결과를 그대로 쓰는데,
     좌표 세션에서 그 값은 ``coord_matcher`` 의 거리 인코딩이다.  그걸 유사도
     백분율로 찍어 실측 tol=200 µm · 거리 480 µm 후보가 **"-240.0 %"** 로 보였다.

C-5  후보 1장마다 ``SlotScoreCache.get_pair`` 를 3회 불렀다(``_count_recompute``
     + 캐시 여부 확인 + ``_lookup_or_compute_score``).  후보 299장이면 락을 897번
     잡는다 — 값은 같으니 조용히 느리기만 했다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                              # noqa: E402
from aoi_verification.app.models.result import MissEntry           # noqa: E402
from aoi_verification.app.models.slot import ImageItem             # noqa: E402
import aoi_verification.app.ui.widgets.unmatched_review_dialog as M  # noqa: E402


class _CountingCache:
    """``SlotScoreCache`` 흉내 — 조회 횟수와 저장 시도를 그대로 기록한다."""

    def __init__(self, value=None) -> None:
        self._value = value
        self.gets: dict[tuple, int] = {}
        self.puts: list[tuple] = []

    def get_pair(self, slot, ref, val):
        key = (slot, Path(ref), Path(val))
        self.gets[key] = self.gets.get(key, 0) + 1
        return self._value

    def put(self, slot, ref, val, score):
        self.puts.append((slot, Path(ref), Path(val), float(score)))


def _dialog(n_cand: int = 4, *, cache=None, coord_mode: bool = False,
            fast_results=None, review_scores=None):
    unmatched = [MissEntry(slot="A1", side="ref", path=Path("/tmp/ref_A1.jpg"))]
    val_pool = {"A1": [ImageItem(slot="A1", path=Path(f"/tmp/v{i}.jpg"),
                                 side="val") for i in range(n_cand)]}
    dlg = M.UnmatchedReviewDialog(unmatched, val_pool, score_cache=cache,
                                  fast_results=fast_results,
                                  coord_mode=coord_mode,
                                  review_scores=review_scores)
    return dlg, unmatched[0], val_pool["A1"]


def _fake_pipeline(monkeypatch, value: float = 0.42) -> list:
    """무거운 cv2 경로를 타지 않고 '이 창이 계산한 값' 을 만든다.

    ``_lookup_or_compute_score`` 가 함수 안에서 지연 import 하므로 모듈 객체를
    바꿔 두면 그대로 걸린다."""
    from aoi_verification.app.similarity import pipeline as P
    calls: list = []
    monkeypatch.setattr(P, "extract", lambda src, **kw: calls.append(src) or src)
    monkeypatch.setattr(P, "score", lambda a, b, **kw: value)
    return calls


# ---------------------------------------------------------------------------
# C-1 — 이 창이 계산한 점수는 공유 캐시에 들어가지 않는다
# ---------------------------------------------------------------------------
def test_computed_score_never_lands_in_the_shared_cache(styled_qapp, monkeypatch):
    """cfg 없이 뽑은 값이 Stage 2 의 통에 섞이면 재매칭·차순위가 오염된다."""
    from aoi_verification.app.similarity.slot_features import SlotScoreCache
    _fake_pipeline(monkeypatch, 0.42)
    cache = _CountingCache(value=None)          # 캐시 miss → 그 자리 계산
    store = SlotScoreCache()                    # 세션 수명 보관처(주입)
    dlg, cur, cands = _dialog(cache=cache, review_scores=store)

    s = dlg._lookup_or_compute_score(cur, cands[0])
    assert s == pytest.approx(0.42)
    assert cache.puts == [], "이 창이 계산한 점수가 공유 SlotScoreCache 에 들어갔다"
    # 그래도 재방문은 빨라야 한다 — 주입된 보관처에서 나온다(추가 계산 없음).
    assert store.get_pair("A1", Path(cur.path), Path(cands[0].path)) == \
        pytest.approx(0.42), "계산한 점수를 어디에도 보관하지 않았다"
    dlg.deleteLater()


def test_shared_cache_value_still_wins_so_shown_scores_do_not_change(
        styled_qapp, monkeypatch):
    """C-1 은 **표시 점수를 바꾸지 않는** 수정이다 — 공유 캐시가 있으면 그 값 그대로."""
    calls = _fake_pipeline(monkeypatch, 0.42)
    cache = _CountingCache(value=0.77)          # Stage 2 가 채워 둔 값
    dlg, cur, cands = _dialog(cache=cache)

    assert dlg._lookup_or_compute_score(cur, cands[0]) == pytest.approx(0.77)
    assert calls == [], "캐시에 값이 있는데 다시 계산했다"
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# C-5 — 쌍당 캐시 조회는 1회
# ---------------------------------------------------------------------------
def test_each_pair_is_looked_up_once_per_render(styled_qapp):
    """캐시 hit 경로 — 렌더 1회에 쌍당 get_pair 1회."""
    cache = _CountingCache(value=0.5)
    dlg, _cur, cands = _dialog(n_cand=4, cache=cache)
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()

    assert len(cache.gets) == len(cands)
    assert max(cache.gets.values()) == 1, \
        f"쌍당 조회가 1회를 넘었다: {cache.gets}"
    assert len(dlg._cand_tiles) == len(cands)
    dlg.deleteLater()


def test_each_pair_is_looked_up_once_even_on_cache_miss(styled_qapp, monkeypatch):
    """캐시 miss 경로도 1회 — 계산으로 내려가기 전에 다시 묻지 않는다."""
    _fake_pipeline(monkeypatch, 0.31)
    cache = _CountingCache(value=None)
    dlg, _cur, cands = _dialog(n_cand=4, cache=cache)
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()

    assert max(cache.gets.values()) == 1, \
        f"쌍당 조회가 1회를 넘었다: {cache.gets}"
    dlg.deleteLater()


# ---------------------------------------------------------------------------
# C-2 — 좌표 거리 점수를 유사도 %로 찍지 않는다
# ---------------------------------------------------------------------------
def test_coord_reuse_predicate_is_narrow():
    """순수 헬퍼 — '좌표 세션 + 재계산 금지 + 선계산 결과 있음' 셋이 모두 참일 때만."""
    ref = Path("/tmp/ref_A1.jpg")
    fres = {("A1", ref): [(Path("/tmp/v0.jpg"), -2.4)]}
    assert M._reuses_coord_scores(True, False, fres, "A1", ref) is True
    # <300 이면 그 자리에서 유사도를 다시 계산하므로 %가 맞다.
    assert M._reuses_coord_scores(True, True, fres, "A1", ref) is False
    # 고효율 모드의 선계산 점수는 **진짜 유사도**다 — 여기 걸리면 안 된다.
    assert M._reuses_coord_scores(False, False, fres, "A1", ref) is False
    # 선계산 결과가 없으면 캐시 hit 만 쓰므로 좌표 점수가 아니다.
    assert M._reuses_coord_scores(True, False, {}, "A1", ref) is False


def _coord_dialog(styled_qapp, *, coord_mode: bool):
    """후보 350장(≥300) + 선계산 점수 — 좌표 매칭이 낸 거리 인코딩 그대로."""
    ref = Path("/tmp/ref_A1.jpg")
    unmatched = [MissEntry(slot="A1", side="ref", path=ref)]
    val_pool = {"A1": [ImageItem(slot="A1", path=Path(f"/tmp/v{i}.jpg"),
                                 side="val") for i in range(350)]}
    # tol=200 µm 에서 dist 480 µm → score −2.4 (허용범위 초과), dist 40 µm → 0.8.
    fres = {("A1", ref): [(Path("/tmp/v0.jpg"), 0.8),
                          (Path("/tmp/v1.jpg"), -2.4)]}
    dlg = M.UnmatchedReviewDialog(unmatched, val_pool, score_cache=None,
                                  fast_results=fres, coord_mode=coord_mode)
    dlg.resize(1000, 700)
    dlg.show()
    dlg._idx = 0
    dlg._render_current()
    return dlg


def test_reused_coord_distance_is_not_shown_as_a_similarity_percent(styled_qapp):
    """"-240.0 %" 는 존재하지 않는 유사도다 — 수치 대신 계산 안 함을 보여 준다."""
    dlg = _coord_dialog(styled_qapp, coord_mode=True)
    texts = [t._score_label.text() for t in dlg._cand_tiles]
    assert texts and all(t == i18n.KO.SCORE_NOT_COMPUTED for t in texts), \
        f"좌표 거리 점수를 그대로 표시했다: {texts}"
    assert "%" not in "".join(texts)
    # 크게보기 캡션도 같은 문구여야 한다(한 곳에서 만든다).
    assert dlg._cand_tiles[0].score_text() == i18n.KO.SCORE_NOT_COMPUTED
    # 순서 기준도 좌표 거리라고 밝힌다 — '유사도 순' 은 이 경우 거짓이다.
    assert dlg.candidates_summary.text() == \
        i18n.KO.UNMATCHED_CAND_COUNT_COORD_FMT.format(n=len(dlg._cand_tiles))
    # 정렬은 그대로(점수 내림차순 = 거리 오름차순).
    assert [t.score for t in dlg._cand_tiles] == [0.8, -2.4]
    dlg.deleteLater()


def test_efficiency_reuse_still_shows_the_percent(styled_qapp):
    """좌표 세션이 아니면 선계산 점수는 진짜 유사도다 — 백분율을 뺏으면 안 된다."""
    dlg = _coord_dialog(styled_qapp, coord_mode=False)
    texts = [t._score_label.text() for t in dlg._cand_tiles]
    assert texts and all("%" in t for t in texts), texts
    assert dlg.candidates_summary.text() == \
        i18n.KO.UNMATCHED_CAND_COUNT_FMT.format(n=len(dlg._cand_tiles))
    dlg.deleteLater()
