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


def test_precompute_stop_aborts_early(isolated_cache, monkeypatch):
    """stop() 호출 시 워커는 즉시 중단."""
    from aoi_verification.app.similarity import pipeline

    class _FF:
        def __init__(self, p): self.path = p

    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: _FF(p))
    monkeypatch.setattr(pipeline, "score", lambda a, b, weights=None: 0.5)

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


def test_threaded_scoring_matches_serial_result(isolated_cache, monkeypatch):
    """ThreadPoolExecutor 병렬 (#5) 결과가 직렬 결과와 일치 — score() 가
    결정적이므로 동일 입력엔 동일 출력."""
    from aoi_verification.app.similarity import pipeline

    class _FF:
        def __init__(self, p): self.path = p

    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: _FF(p))
    # 결정적 score: 두 path 의 hash xor 로 0.0~1.0 사이 값.
    monkeypatch.setattr(
        pipeline, "score",
        lambda a, b, weights=None:
            ((hash(str(a.path)) ^ hash(str(b.path))) & 0xFFFF) / 65535.0,
    )

    refs = _items("S1", 5, side="ref")
    vals = _items("S1", 6, side="val")

    # 직렬 결과 (참조용) — score 만 직접 계산.
    expected: dict[tuple, float] = {}
    for r in refs:
        for v in vals:
            expected[(r.path, v.path)] = pipeline.score(_FF(r.path), _FF(v.path))

    # 병렬 워커 — run() 동기 호출.
    slot_cache = SlotFeatureCache()
    score_cache = SlotScoreCache()
    worker = SlotPrecomputeWorker(
        [("S1", refs, vals)], slot_cache=slot_cache, score_cache=score_cache,
    )
    worker.run()

    assert score_cache.size() == len(refs) * len(vals)
    for (rp, vp), exp_val in expected.items():
        got = score_cache.get_pair("S1", rp, vp)
        assert got is not None
        assert abs(got - exp_val) < 1e-9, (
            f"병렬 결과가 직렬 기대값과 다름: {got} vs {exp_val}"
        )
