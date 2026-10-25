"""M16 — 후보 점수 계산이 **실패**하면 0% 로 둔갑시키지 않는다.

예전에는 예외를 `s = 0.0` 으로 삼키고 그 값을 `_remember_score` 로 기억해, 다음 방문에도
'유사도 0.0 %' 로 굳었다.  이제는 기록(로그)하고, 기억하지 않고, 타일에 '계산 불가' 로 보이며,
정렬에서는 실제 점수들 뒤로 간다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n                                # noqa: E402
from aoi_verification.app.models.result import MissEntry           # noqa: E402
from aoi_verification.app.models.slot import ImageItem             # noqa: E402
import aoi_verification.app.ui.widgets.unmatched_review_dialog as M  # noqa: E402


def _dialog():
    unmatched = [MissEntry(slot="A1", side="ref", path=Path("/tmp/ref_A1.jpg"))]
    pool = {"A1": [ImageItem(slot="A1", path=Path(f"/tmp/v{i}.jpg"), side="val")
                   for i in range(3)]}
    return M.UnmatchedReviewDialog(unmatched, pool), pool["A1"]


def _boom(*_a, **_k):
    raise RuntimeError("boom")


def test_failed_score_is_not_memoized_and_is_marked_failed(styled_qapp, monkeypatch):
    pipeline = pytest.importorskip("aoi_verification.app.similarity.pipeline")
    monkeypatch.setattr(pipeline, "extract", _boom)
    dlg, cands = _dialog()
    cur = dlg._unmatched[0]

    got = dlg._lookup_or_compute_score(cur, cands[0], allow_compute=True)

    assert got is M._SCORE_FAILED
    assert dlg._known_score(cur.slot, Path(cur.path), Path(cands[0].path)) is None, \
        "실패를 0.0 으로 기억하면 다음 방문에도 0% 로 굳는다"
    scored = dlg._score_candidates(cur, cands, True)
    assert [s for s, _ in scored] == [None, None, None]


def test_failed_candidates_sort_after_real_scores_and_show_label(styled_qapp):
    dlg, cands = _dialog()
    cur = dlg._unmatched[0]
    dlg._score_token += 1
    scored = [(None, cands[0]), (0.3, cands[1]), (0.8, cands[2])]   # 실패 1 + 실점수 2
    dlg._apply_scored(dlg._score_token, cur, ("A1", frozenset()), scored)

    assert [t.item.path for t in dlg._cand_tiles] == \
        [cands[2].path, cands[1].path, cands[0].path]
    texts = [t.score_text() for t in dlg._cand_tiles]
    assert texts[-1] == i18n.KO.SCORE_FAILED
    assert i18n.KO.SCORE_FAILED not in texts[:-1]
    assert "0.0" not in texts[-1]

    # 같은 슬롯 재사용 경로(set_score)도 None 을 견딘다.
    dlg._score_token += 1
    dlg._apply_scored(dlg._score_token, cur, ("A1", frozenset()),
                      [(0.5, cands[0]), (None, cands[1]), (0.1, cands[2])])
    assert dlg._cand_tiles[-1].item.path == cands[1].path
    assert dlg._cand_tiles[-1].score_text() == i18n.KO.SCORE_FAILED
