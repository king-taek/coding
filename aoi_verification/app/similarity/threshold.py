"""임계치 자동 추천 — 시작 직후 작은 샘플을 측정해 분포의 갭에서 임계치 제안.

스캔이 완료된 직후 호출. 각 슬롯에서 최대 K 장의 ref 를 뽑아 같은 슬롯 val 과
다른 슬롯 val 의 점수 분포를 측정 → 둘 사이 갭 중앙을 임계치로 제안.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Optional

from ..models.slot import ScanResult, Slot
from ..similarity import pipeline as _pipe


@dataclass
class ThresholdSuggestion:
    suggested: float          # 추천 임계치 (0~1)
    same_median: float        # 같은 슬롯 점수의 중앙값
    same_min: float
    diff_median: float        # 다른 슬롯 점수의 중앙값
    diff_max: float
    margin: float             # same_min - diff_max
    n_same: int               # 측정된 same-slot 쌍 수
    n_diff: int               # 측정된 diff-slot 쌍 수


def suggest_threshold(scan: ScanResult,
                      *,
                      per_slot_max: int = 3,
                      diff_pairs_max: int = 24,
                      seed: int = 0) -> Optional[ThresholdSuggestion]:
    """가벼운 샘플 추출 → 같은/다른 슬롯 점수 분포 → 임계치 제안.

    측정량을 줄이기 위해 슬롯당 ref 최대 ``per_slot_max`` 장, val 도 같은 수만
    뽑고, diff-slot 쌍은 ``diff_pairs_max`` 개에서 잘라낸다.
    """
    slots = [s for s in scan.slots.values() if s.has_both]
    if len(slots) < 2:
        return None

    rng = random.Random(seed)
    same_scores: list[float] = []
    diff_scores: list[float] = []

    # same-slot
    for slot in slots:
        refs = slot.ref_images[:per_slot_max]
        vals = slot.val_images[:per_slot_max]
        for r in refs:
            try:
                rf = _pipe.extract(r.path)
            except Exception:
                continue
            for v in vals:
                try:
                    same_scores.append(_pipe.score(rf, _pipe.extract(v.path)))
                except Exception:
                    pass

    # diff-slot — 랜덤으로 diff_pairs_max 개만 측정
    diff_pairs: list[tuple] = []
    for i, slot in enumerate(slots):
        for j, slot2 in enumerate(slots):
            if i == j:
                continue
            for r in slot.ref_images[:1]:
                for v in slot2.val_images[:1]:
                    diff_pairs.append((r.path, v.path))
    rng.shuffle(diff_pairs)
    for rp, vp in diff_pairs[:diff_pairs_max]:
        try:
            diff_scores.append(_pipe.score(_pipe.extract(rp), _pipe.extract(vp)))
        except Exception:
            pass

    if not same_scores or not diff_scores:
        return None

    same_scores.sort()
    diff_scores.sort()
    same_min = same_scores[0]
    same_median = same_scores[len(same_scores) // 2]
    diff_max = diff_scores[-1]
    diff_median = diff_scores[len(diff_scores) // 2]
    margin = same_min - diff_max

    if margin > 0:
        # 두 분포가 분리 — 중간점을 권장
        suggested = (same_min + diff_max) / 2
    else:
        # 분포가 겹침 — same 의 25% 분위수와 diff 의 75% 분위수 중간점 시도
        s_lo = same_scores[max(0, len(same_scores) // 4)]
        d_hi = diff_scores[min(len(diff_scores) - 1, 3 * len(diff_scores) // 4)]
        suggested = (s_lo + d_hi) / 2
    # 너무 극단으로 가지 않도록 클램프
    suggested = max(0.25, min(0.85, suggested))

    return ThresholdSuggestion(
        suggested=round(suggested, 2),
        same_median=round(same_median, 3),
        same_min=round(same_min, 3),
        diff_median=round(diff_median, 3),
        diff_max=round(diff_max, 3),
        margin=round(margin, 3),
        n_same=len(same_scores),
        n_diff=len(diff_scores),
    )
