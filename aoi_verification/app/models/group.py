"""같은 슬롯 안의 거의 동일한 사진들을 pHash 로 묶는 그룹 매니저.

목적 (#15):
- Stage 1 에서 거의 똑같은 사진이 여러 장 나오는 피로를 줄임.
- 그룹의 대표 1장만 ‘결정할 사진’ 으로 큐에 들어가고, 나머지는 사용자가 결정한
  방향을 그대로 따라간다.
- 단, **사용자는 그룹 전체를 미리 볼 수 있어야** 하고, 특정 사진을 그룹에서
  빼서 따로 결정하게 할 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ..models.slot import ImageItem
from ..similarity import phash as _phash
from ..utils import image_io


@dataclass
class PhotoGroup:
    """한 그룹 — 대표(rep) + 같은 그룹의 다른 사진들(siblings)."""
    slot: str
    rep: ImageItem
    siblings: list[ImageItem] = field(default_factory=list)

    def all_items(self) -> list[ImageItem]:
        return [self.rep] + list(self.siblings)

    def size(self) -> int:
        return 1 + len(self.siblings)


@dataclass
class GroupingResult:
    """``cluster()`` 의 출력 — 큐에 들어갈 대표 + lookup 헬퍼."""
    representatives: list[ImageItem]                    # 큐에 들어가는 사진들
    by_rep: dict[str, PhotoGroup] = field(default_factory=dict)   # rep.key → group
    item_to_group: dict[str, PhotoGroup] = field(default_factory=dict)
    # ImageItem.key → 자신이 속한 그룹

    def group_for(self, item: ImageItem) -> Optional[PhotoGroup]:
        return self.item_to_group.get(item.key)

    def remove_from_group(self, item: ImageItem) -> Optional[ImageItem]:
        """``item`` 을 자신의 그룹에서 분리해 독립 ImageItem 으로 만든다.

        - item 이 그룹의 sibling 이면 siblings 에서 제거 → 호출자가 큐에
          새로 끼워넣어야 한다 (반환값이 그 item).
        - item 이 rep 이면 그룹이 dissolve — 모든 siblings 가 독립 ImageItem
          으로 풀리고 (호출자가 큐에 삽입), 반환값은 None.
        """
        g = self.item_to_group.get(item.key)
        if g is None:
            return None
        if item.key == g.rep.key:
            # rep 해제 → 그룹 자체 해체
            for sib in g.siblings:
                self.item_to_group.pop(sib.key, None)
            self.item_to_group.pop(g.rep.key, None)
            self.by_rep.pop(g.rep.key, None)
            return None
        # sibling 만 빠짐
        g.siblings = [s for s in g.siblings if s.key != item.key]
        self.item_to_group.pop(item.key, None)
        if not g.siblings:
            # rep 만 남으면 그룹을 해체 (단독 사진으로 환원)
            self.item_to_group.pop(g.rep.key, None)
            self.by_rep.pop(g.rep.key, None)
        return item


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------
def cluster(slots_items: dict[str, Iterable[ImageItem]],
            *,
            similarity_threshold: float = 0.92,
            min_group_size: int = 3) -> GroupingResult:
    """슬롯별로 pHash 기반 그룹화. 그룹 크기가 min_group_size 미만이면 묶지 않음.

    Greedy clustering: 각 사진에 대해 미할당 사진들 중 pHash 유사도가
    ``similarity_threshold`` 이상인 것들을 모두 한 그룹으로 묶음.
    파일명 오름차순으로 첫 사진이 대표가 된다.
    """
    reps: list[ImageItem] = []
    by_rep: dict[str, PhotoGroup] = {}
    item_to_group: dict[str, PhotoGroup] = {}

    for slot, items in slots_items.items():
        items = list(items)
        if not items:
            continue
        # 각 사진의 pHash 미리 계산 (한 슬롯만 — 적은 비용)
        hashes: dict[str, np.ndarray] = {}
        for it in items:
            try:
                gray = image_io.center_roi_gray(it.path)
                hashes[it.key] = _phash.compute_phash(gray)
            except Exception:
                hashes[it.key] = np.zeros(0, dtype=np.uint8)

        # 파일명 순으로 정렬해 안정적인 rep 선택
        ordered = sorted(items, key=lambda x: x.path.name.lower())
        assigned: set[str] = set()
        for i, anchor in enumerate(ordered):
            if anchor.key in assigned:
                continue
            ha = hashes.get(anchor.key)
            if ha is None or ha.size == 0:
                # 해시 실패 → 단독 처리, 그룹 없음
                reps.append(anchor)
                assigned.add(anchor.key)
                continue
            members: list[ImageItem] = []
            for other in ordered[i + 1:]:
                if other.key in assigned:
                    continue
                hb = hashes.get(other.key)
                if hb is None or hb.size == 0:
                    continue
                if _phash.phash_similarity(ha, hb) >= similarity_threshold:
                    members.append(other)
            if len(members) + 1 >= min_group_size:
                grp = PhotoGroup(slot=slot, rep=anchor, siblings=members)
                by_rep[anchor.key] = grp
                item_to_group[anchor.key] = grp
                for m in members:
                    item_to_group[m.key] = grp
                    assigned.add(m.key)
                reps.append(anchor)
                assigned.add(anchor.key)
            else:
                # 그룹 미달 — 그냥 개별 큐로 보냄
                reps.append(anchor)
                assigned.add(anchor.key)

    return GroupingResult(
        representatives=reps, by_rep=by_rep, item_to_group=item_to_group,
    )
