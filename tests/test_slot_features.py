"""SlotFeatureCache — RAM 캐시 / 활성 슬롯 / lookahead 정리."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.similarity.slot_features import SlotFeatureCache


def _items(slot: str, n: int) -> List[ImageItem]:
    return [ImageItem(slot=slot, path=Path(f"/tmp/{slot}/{i}.jpeg"), side="val")
            for i in range(n)]


def test_empty_cache_state(isolated_cache):
    c = SlotFeatureCache()
    assert c.active_slot() is None
    assert c.has("S1") is False
    assert c.size() == 0
    assert c.get_features("S1") is None


def test_build_failure_returns_empty_dict(isolated_cache, monkeypatch):
    """모든 sim.extract 가 실패하면 빈 dict."""
    from aoi_verification.app.similarity import pipeline

    def boom(*_a, **_kw):
        raise RuntimeError("no image")

    monkeypatch.setattr(pipeline, "extract", boom)
    c = SlotFeatureCache()
    out = c.build("S1", _items("S1", 3))
    assert out == {}
    assert c.has("S1") is True   # 슬롯은 존재 (빈 dict 로 표시)


def test_build_uses_pipeline_extract(isolated_cache, monkeypatch):
    """build 가 path 마다 extract 를 호출하고 dict 에 저장."""
    from aoi_verification.app.similarity import pipeline

    captured = []

    class FakeFeat:
        def __init__(self, p):
            self.path = p

    def fake_extract(p, **_kw):
        captured.append(p)
        return FakeFeat(p)

    monkeypatch.setattr(pipeline, "extract", fake_extract)
    c = SlotFeatureCache()
    items = _items("S1", 3)
    out = c.build("S1", items)
    assert len(out) == 3
    assert all(it.path in out for it in items)
    # 두 번째 build 는 멱등 — extract 가 다시 호출되지 않아야.
    captured.clear()
    out2 = c.build("S1", items)
    assert captured == []
    assert len(out2) == 3


def test_set_active_drops_other_slots(isolated_cache, monkeypatch):
    from aoi_verification.app.similarity import pipeline
    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: object())

    c = SlotFeatureCache(keep_lookahead=False)
    c.build("S1", _items("S1", 2))
    c.build("S2", _items("S2", 2))
    assert sorted(c.known_slots()) == ["S1", "S2"]
    c.set_active("S2")
    assert c.known_slots() == ["S2"]
    assert c.active_slot() == "S2"


def test_lookahead_kept_when_enabled(isolated_cache, monkeypatch):
    from aoi_verification.app.similarity import pipeline
    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: object())

    c = SlotFeatureCache(keep_lookahead=True)
    c.build("A", _items("A", 1))
    c.build("B", _items("B", 1))
    c.set_lookahead("B")
    c.build("C", _items("C", 1))
    c.set_active("C")
    # active=C + lookahead=B 유지, A 제거.
    assert sorted(c.known_slots()) == ["B", "C"]


def test_clear_resets_everything(isolated_cache, monkeypatch):
    from aoi_verification.app.similarity import pipeline
    monkeypatch.setattr(pipeline, "extract", lambda p, **_kw: object())

    c = SlotFeatureCache()
    c.build("S1", _items("S1", 2))
    assert c.size() == 2
    c.clear()
    assert c.size() == 0
    assert c.active_slot() is None
    assert c.known_slots() == []
