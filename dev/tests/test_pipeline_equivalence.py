"""요청 1·2 — 파이프라인 경로가 순차 경로와 '동일한 점수/순서' 를 내는지 검증.

정확도 불변(임베딩·점수·결과)이 이 기능의 핵심 요구이므로, 같은 입력에 대해
``_run_pipelined`` 와 ``_run_sequential`` 의 SlotScoreCache 내용과 slot_finished
순서가 완전히 일치함을 회귀로 고정한다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from typing import List

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.models.slot import ImageItem          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(slot: str, n: int, side: str) -> List[ImageItem]:
    return [ImageItem(slot=slot, path=Path(f"/tmp/{slot}/{side}{i}.jpeg"),
                      side=side)
            for i in range(n)]


class _Feat:
    """경로만 들고 있는 가짜 Feature."""
    def __init__(self, p):
        self.path = Path(p)


def _det_score(a, b) -> float:
    """두 경로로부터 결정적 점수 — 파이프라인/순차가 같은 입력에 같은 값."""
    h = (hash(str(a.path)) ^ (hash(str(b.path)) * 31)) & 0xFFFF
    return (h % 1000) / 1000.0


def _drain(scores, tasks):
    """SlotScoreCache 를 (slot, ref, val) → score 평면 dict 로 펼친다."""
    out = {}
    for slot, refs, vals in tasks:
        for r in refs:
            for v in vals:
                out[(slot, r.path, v.path)] = scores.get_pair(slot, r.path, v.path)
    return out


def _run(monkeypatch, pipelined: bool):
    from aoi_verification.app.similarity import pipeline
    from aoi_verification.app.similarity.slot_features import (
        SlotFeatureCache, SlotPrecomputeWorker, SlotScoreCache,
    )

    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: _Feat(p))
    monkeypatch.setattr(pipeline, "score", lambda a, b, **_kw: _det_score(a, b))

    tasks = [
        ("S1", _items("S1", 3, "ref"), _items("S1", 4, "val")),
        ("S2", _items("S2", 2, "ref"), _items("S2", 2, "val")),
        ("S3", _items("S3", 4, "ref"), _items("S3", 3, "val")),
    ]
    cache = SlotFeatureCache(keep_lookahead=False)
    scores = SlotScoreCache()
    worker = SlotPrecomputeWorker(tasks, cache, scores, release_after_slot=True)

    order: list = []
    worker.signals.slot_finished.connect(lambda s, i, t: order.append((s, i, t)))

    if pipelined:
        worker._run_pipelined()
    else:
        worker._run_sequential()
    return _drain(scores, tasks), order, scores.size()


def test_pipelined_matches_sequential(qapp, isolated_cache, monkeypatch):
    seq_scores, seq_order, seq_size = _run(monkeypatch, pipelined=False)
    pipe_scores, pipe_order, pipe_size = _run(monkeypatch, pipelined=True)

    # 슬롯 완료 순서 동일 (idx 오름차순, idx==1 이 첫 슬롯).
    assert seq_order == pipe_order
    assert seq_order[0][1] == 1
    # 모든 (slot, ref, val) 쌍 점수가 bit-identical.
    assert pipe_scores == seq_scores
    assert pipe_size == seq_size == (3 * 4 + 2 * 2 + 4 * 3)


def test_pipelined_empty_slot_still_signals(qapp, isolated_cache, monkeypatch):
    from aoi_verification.app.similarity import pipeline
    from aoi_verification.app.similarity.slot_features import (
        SlotFeatureCache, SlotPrecomputeWorker, SlotScoreCache,
    )
    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: _Feat(p))
    monkeypatch.setattr(pipeline, "score", lambda a, b, **_kw: 0.5)

    tasks = [
        ("S1", _items("S1", 2, "ref"), _items("S1", 2, "val")),
        ("S2", _items("S2", 0, "ref"), _items("S2", 3, "val")),  # 빈 ref
        ("S3", _items("S3", 1, "ref"), _items("S3", 1, "val")),
    ]
    cache = SlotFeatureCache(keep_lookahead=False)
    scores = SlotScoreCache()
    worker = SlotPrecomputeWorker(tasks, cache, scores, release_after_slot=True)
    order: list = []
    worker.signals.slot_finished.connect(lambda s, i, t: order.append((s, i, t)))
    worker._run_pipelined()

    assert order == [("S1", 1, 3), ("S2", 2, 3), ("S3", 3, 3)]
    assert scores.size() == 2 * 2 + 1 * 1
