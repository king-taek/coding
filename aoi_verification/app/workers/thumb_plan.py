"""어떤 사진의 썸네일을 언제 만들지 — 순수 로직(Qt 없음, 헤드리스 테스트 대상).

좌표 매칭은 썸네일을 보지 않는다(좌표는 파일명·INI·.001 에서 읽는다).  썸네일은 화면
전용이므로, 좌표 매칭일 때는 검증 사진 썸네일을 [검증 시작] 에서 기다리지 않고 필요한
것만 뒤에서 만든다(사용자 결정 2026-10-26 — 검증 장비 결함이 기준보다 훨씬 많으면
안 쓸 사진 수천 장을 만드느라 시작이 오래 걸렸다).

  · 시작: 기준 사진만(`startup_items`).
  · 매칭: 슬롯 하나가 끝날 때마다 그 슬롯의 후보로 나온 사진만(`candidate_items`).
  · 교차검증 2차: 1차 매칭이 끝나면 2차 큐에 들어갈 검증 사진을 천천히
    (`cross_b_prefetch_items`), 2차 선별에 들어갈 때 남은 것만 전속력으로.

이미지 매칭(기본·고효율)은 지금처럼 시작 때 전부 만든다(사용자 결정 — 좌표 매칭만 바꾼다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..models import cross_check
from ..models.slot import ImageItem


def startup_items(slots: dict, names: Iterable[str], *,
                  lazy_val: bool) -> list[ImageItem]:
    """[검증 시작] 에서 다 만들 때까지 기다릴 사진 — ``lazy_val`` 이면 기준 사진만."""
    out: list[ImageItem] = []
    for name in names:
        slot = slots[name]
        out.extend(slot.ref_images)
        if not lazy_val:
            out.extend(slot.val_images)
    return out


def candidate_items(results: dict, slot: str,
                    pool_items: Iterable[ImageItem]) -> list[ImageItem]:
    """매칭 결과(``results[(slot, ref_path)] = [(cand_path, score), ...]``)에서 그
    슬롯의 후보로 나온 사진 — 후보 풀 순서대로, 중복 없이."""
    paths: set[Path] = set()
    for (s, _ref), cands in results.items():
        if s != slot:
            continue
        for cand in cands or ():
            paths.add(Path(cand[0]))
    return [it for it in pool_items if Path(it.path) in paths]


def cross_b_prefetch_items(val_by_slot: dict, a_matches) -> list[ImageItem]:
    """교차검증 2차 큐에 들어갈 검증 사진(1차에서 짝지은 것은 빠진다) — 슬롯명 순."""
    by_slot = cross_check.reverse_queue(val_by_slot, a_matches)
    return [it for n in sorted(by_slot) for it in by_slot[n]]
