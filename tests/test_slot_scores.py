"""SlotScoreCache + SlotPrecomputeWorker — 사전 계산 캐시 동작."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.similarity.slot_features import (
    SlotFeatureCache, SlotPrecomputeWorker, SlotScoreCache,
)


def _items(slot: str, n: int, side: str = "val") -> List[ImageItem]:
    return [ImageItem(slot=slot, path=Path(f"/tmp/{slot}/{side}_{i}.jpeg"), side=side)
            for i in range(n)]


# ---- SlotScoreCache ------------------------------------------------------
def test_score_cache_put_get(isolated_cache):
    c = SlotScoreCache()
    p1, p2 = Path("/r1"), Path("/v1")
    c.put("S1", p1, p2, 0.85)
    assert c.has_pair("S1", p1, p2) is True
    assert c.get_pair("S1", p1, p2) == 0.85


def test_score_cache_missing(isolated_cache):
    c = SlotScoreCache()
    assert c.has_pair("S1", Path("/r"), Path("/v")) is False
    assert c.get_pair("S1", Path("/r"), Path("/v")) is None


def test_score_cache_has_all_pairs(isolated_cache):
    c = SlotScoreCache()
    r = Path("/r1")
    vals = [Path("/v1"), Path("/v2"), Path("/v3")]
    c.put("S1", r, vals[0], 0.5)
    c.put("S1", r, vals[1], 0.6)
    # v3 누락
    assert c.has_all_pairs("S1", r, vals) is False
    c.put("S1", r, vals[2], 0.7)
    assert c.has_all_pairs("S1", r, vals) is True


def test_score_cache_clear_slot(isolated_cache):
    c = SlotScoreCache()
    c.put("S1", Path("/r"), Path("/v"), 0.5)
    c.put("S2", Path("/r"), Path("/v"), 0.7)
    c.clear_slot("S1")
    assert c.has_slot("S1") is False
    assert c.has_slot("S2") is True


# ---- SlotPrecomputeWorker -----------------------------------------------
def test_precompute_fills_score_cache(isolated_cache, monkeypatch, qtbot=None):
    """워커가 모든 (ref, val) 쌍 점수를 캐시에 채워 넣는지."""
    from aoi_verification.app.similarity import pipeline

    # 가짜 features + 점수 함수
    class _FakeFeat:
        def __init__(self, path):
            self.path = path

    def fake_extract(p, **_kw):
        return _FakeFeat(p)

    def fake_score(a, b, weights=None):
        # ref / val 의 파일명 끝 숫자를 합쳐 결정적 점수.
        return float(
            (hash((str(a.path), str(b.path))) % 100) / 100.0
        )

    monkeypatch.setattr(pipeline, "extract", fake_extract)
    monkeypatch.setattr(pipeline, "score", fake_score)
    # 2 단계 스캔이 1 차로 pHash 점수도 부른다 — 가짜 도 mock.
    monkeypatch.setattr(pipeline, "score_phash_only",
                        lambda a, b: fake_score(a, b))

    slot_cache = SlotFeatureCache()
    score_cache = SlotScoreCache()

    refs = _items("S1", 3, side="ref")
    vals = _items("S1", 4, side="val")

    worker = SlotPrecomputeWorker(
        [("S1", refs, vals)],
        slot_cache=slot_cache, score_cache=score_cache,
    )

    finished = []
    worker.signals.finished.connect(lambda: finished.append(True))
    # QThread 의 run() 을 동기 호출해서 메인 스레드에서 처리.
    worker.run()

    # 3 × 4 = 12 쌍 모두 캐시 hit.
    assert score_cache.size() == 12
    for r in refs:
        assert score_cache.has_all_pairs("S1", r.path, [v.path for v in vals])


def test_precompute_two_stage_shortlist(isolated_cache, monkeypatch):
    """val 이 많으면 1 차 pHash 로 추린 상위 K 만 정밀 score() 호출."""
    from aoi_verification.app.similarity import pipeline

    class _FF:
        def __init__(self, p): self.path = p

    full_calls: list = []
    fast_calls: list = []

    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: _FF(p))
    monkeypatch.setattr(pipeline, "score",
                        lambda a, b, weights=None:
                            (full_calls.append((a.path, b.path)), 0.5)[1])
    monkeypatch.setattr(pipeline, "score_phash_only",
                        lambda a, b:
                            (fast_calls.append((a.path, b.path)),
                             0.5 - len(fast_calls) * 0.001)[1])

    slot_cache = SlotFeatureCache()
    score_cache = SlotScoreCache()
    refs = _items("S1", 2, side="ref")
    vals = _items("S1", 50, side="val")     # > _TWO_STAGE_THRESHOLD (20)

    worker = SlotPrecomputeWorker(
        [("S1", refs, vals)],
        slot_cache=slot_cache, score_cache=score_cache,
    )
    worker.run()

    # 1 차는 모든 쌍 (2 × 50 = 100) 호출.
    assert len(fast_calls) == 100
    # 2 차는 ref 당 max(8, 50*0.4) = 20 개만 → 총 2 × 20 = 40 호출.
    assert len(full_calls) == 2 * 20
    # 캐시는 모든 쌍 (1 차 점수 포함 — 추리지 못한 것은 1 차 점수 그대로) 가짐.
    assert score_cache.size() == 100


def test_precompute_stop_aborts_early(isolated_cache, monkeypatch):
    """stop() 호출 시 워커는 즉시 중단."""
    from aoi_verification.app.similarity import pipeline

    class _FF:
        def __init__(self, p): self.path = p

    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: _FF(p))
    monkeypatch.setattr(pipeline, "score", lambda a, b, weights=None: 0.5)
    monkeypatch.setattr(pipeline, "score_phash_only", lambda a, b: 0.5)

    slot_cache = SlotFeatureCache()
    score_cache = SlotScoreCache()
    refs = _items("S1", 10, side="ref")
    vals = _items("S1", 10, side="val")

    worker = SlotPrecomputeWorker(
        [("S1", refs, vals)],
        slot_cache=slot_cache, score_cache=score_cache,
    )
    worker.stop()                 # run 시작 전에 중단 신호
    worker.run()
    # 거의 아무 것도 처리 안 됐어야.
    assert score_cache.size() < 100
