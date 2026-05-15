"""ThumbnailPool — 우선순위 큐 + 멀티 워커 단위 테스트.

실제 이미지 디코딩 없이 우선순위 정렬 로직만 검증한다.
"""

from __future__ import annotations

import heapq
from pathlib import Path

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers.thumbnailer import (
    PRIORITY_ACTIVE_SLOT, PRIORITY_BACKGROUND, PRIORITY_CENTER,
    PRIORITY_NEXT_SLOT, ThumbnailPool, _Job,
)


def _item(slot: str, name: str) -> ImageItem:
    return ImageItem(slot=slot, path=Path(f"/tmp/{slot}/{name}.jpeg"), side="ref")


def test_priority_ordering():
    """priority + seq 만으로 정렬되고 다른 필드는 비교에 사용 안 됨."""
    h: list[_Job] = []
    heapq.heappush(h, _Job(priority=3, seq=1, slot="A", item=_item("A", "1")))
    heapq.heappush(h, _Job(priority=0, seq=2, slot="B", item=_item("B", "2")))
    heapq.heappush(h, _Job(priority=2, seq=3, slot="C", item=_item("C", "3")))

    out = []
    while h:
        out.append(heapq.heappop(h).priority)
    assert out == [0, 2, 3]


def test_priority_constants_are_ordered():
    assert PRIORITY_CENTER < PRIORITY_ACTIVE_SLOT < PRIORITY_NEXT_SLOT < PRIORITY_BACKGROUND


def test_enqueue_total_and_pending():
    pool = ThumbnailPool()
    items = [_item("S1", f"f{i}") for i in range(5)]
    pool.enqueue(items, priority=PRIORITY_BACKGROUND)
    assert pool.total() == 5
    assert pool.pending() == 5


def test_reprioritize_moves_target_slot_to_front():
    pool = ThumbnailPool()
    pool.enqueue([_item("S1", "a"), _item("S1", "b")], priority=PRIORITY_BACKGROUND)
    pool.enqueue([_item("S2", "x"), _item("S2", "y")], priority=PRIORITY_BACKGROUND)
    pool.reprioritize_slot("S2", PRIORITY_ACTIVE_SLOT)
    # 첫 두 개를 pop 했을 때 모두 S2 인지.
    first_two_slots = []
    for _ in range(2):
        with pool._lock:
            j = heapq.heappop(pool._heap)
        first_two_slots.append(j.slot)
    assert first_two_slots == ["S2", "S2"]


def test_reprioritize_does_not_lower_priority():
    """이미 더 높은(=숫자가 작은) 우선순위인 작업은 손대지 않는다."""
    pool = ThumbnailPool()
    pool.enqueue([_item("S1", "a")], priority=PRIORITY_CENTER)
    # 같은 슬롯을 NEXT_SLOT 으로 재정렬 시도 → 이미 CENTER 이므로 변경 없음.
    pool.reprioritize_slot("S1", PRIORITY_NEXT_SLOT)
    with pool._lock:
        j = heapq.heappop(pool._heap)
    assert j.priority == PRIORITY_CENTER
