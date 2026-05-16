"""같은 슬롯 안에서 유사한 사진들을 묶는 그룹 매니저.

목적 (#15):
- Stage 1 에서 거의 똑같은 사진이 여러 장 나오는 피로를 줄임.
- 그룹의 대표 1장만 ‘결정할 사진’ 으로 큐에 들어가고, 나머지는 사용자가 결정한
  방향을 그대로 따라간다.
- 단, **사용자는 그룹 전체를 미리 볼 수 있어야** 하고, 특정 사진을 그룹에서
  빼서 따로 결정하게 할 수 있어야 한다.

알고리즘:
- pHash 단일 점수만 쓰면 같은 결함의 다른 각도/조명 사진이 묶이지 않는 경우가
  많았다. ``pipeline.score()`` (pHash + ORB + SSIM + 선택적 CNN) 의 가중 평균
  유사도로 ‘매치라고 부를 정도로 닮은 사진’ 끼리 그룹화한다.  feature 추출은
  디스크 캐시 (.npz) 를 그대로 활용한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..models.slot import ImageItem


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

    # ------------------------------------------------------------------
    def detach(self, item: ImageItem) -> list[ImageItem]:
        """그룹에서 ``item`` 을 빼고 ``representatives`` 에도 반영.

        반환값: 큐에 새로 추가된 ImageItem 들의 리스트.
        - sibling 분리 → [item]
        - rep 분리   → group 의 siblings 전부 (rep 자체는 이미 큐에 있음)
        - 그룹에 속하지 않은 item → []
        """
        g = self.item_to_group.get(item.key)
        if g is None:
            return []
        existing_keys = {r.key for r in self.representatives}
        if item.key == g.rep.key:
            siblings_snapshot = list(g.siblings)
            self.remove_from_group(item)
            added: list[ImageItem] = []
            for s in siblings_snapshot:
                if s.key not in existing_keys:
                    self.representatives.append(s)
                    existing_keys.add(s.key)
                    added.append(s)
            return added
        # sibling 분리
        self.remove_from_group(item)
        if item.key not in existing_keys:
            self.representatives.append(item)
            return [item]
        return []

    def remaining_groups(self) -> list[PhotoGroup]:
        """현재까지 살아있는 그룹들 (rep 가 dissolve 되지 않은 그룹만)."""
        return list(self.by_rep.values())


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------
def cluster(slots_items: dict[str, Iterable[ImageItem]],
            *,
            similarity_threshold: float = 0.45,
            min_group_size: int = 2) -> GroupingResult:
    """슬롯별 그룹화 — pipeline.score() (pHash+ORB+SSIM+CNN) 기반 greedy.

    매 anchor 별로 미할당 사진들과 ``pipeline.score()`` 점수를 계산해 임계치
    이상인 것들을 한 그룹으로 묶는다. pHash 단일 점수보다 ‘같은 결함의 다른
    각도/조명’ 사진을 훨씬 안정적으로 묶는다 (특히 ORB/SSIM 항이 결합돼).

    ``similarity_threshold`` 의 의미는 매칭 임계치와 동일하다 — ‘매치라고 부를
    수준의 유사도’ 이상이면 같은 그룹. ``min_group_size`` 가 1 이면 단독 사진도
    그룹으로 본다 (특수 용도).
    """
    # ``pipeline`` import 는 함수 내부 — 모듈 import cycle 회피.
    from ..similarity import pipeline as _pipeline
    from ..similarity.pipeline import Feature

    reps: list[ImageItem] = []
    by_rep: dict[str, PhotoGroup] = {}
    item_to_group: dict[str, PhotoGroup] = {}

    for slot, items in slots_items.items():
        items = list(items)
        if not items:
            continue
        # 슬롯의 모든 이미지 feature 추출 — 디스크 캐시 (.npz) 가 있으면 매우 빠름.
        feats: dict[str, Optional[Feature]] = {}
        for it in items:
            try:
                feats[it.key] = _pipeline.extract(it.path)
            except Exception:
                feats[it.key] = None

        # 파일명 순으로 정렬해 안정적인 rep 선택.
        ordered = sorted(items, key=lambda x: x.path.name.lower())
        assigned: set[str] = set()
        for i, anchor in enumerate(ordered):
            if anchor.key in assigned:
                continue
            af = feats.get(anchor.key)
            if af is None:
                # feature 추출 실패 → 단독 처리.
                reps.append(anchor)
                assigned.add(anchor.key)
                continue
            members: list[ImageItem] = []
            for other in ordered[i + 1:]:
                if other.key in assigned:
                    continue
                of = feats.get(other.key)
                if of is None:
                    continue
                try:
                    s = _pipeline.score(af, of)
                except Exception:
                    continue
                if s >= similarity_threshold:
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
                reps.append(anchor)
                assigned.add(anchor.key)

    return GroupingResult(
        representatives=reps, by_rep=by_rep, item_to_group=item_to_group,
    )
