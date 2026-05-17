"""‘동일 defect + 촬영 위치만 조금 다름’ 그룹화 (#5 재시도).

사용자 통찰: 동일 장비 + 동일 슬롯에서 같은 defect 은 카메라가 약간만 움직인
거의 동일한 사진으로 여러 장 촬영된다.  즉 ‘작은 평행 이동(translation)’ 만
존재하는 쌍을 묶으면 같은 defect 그룹이 된다.

알고리즘:
1. 슬롯의 모든 이미지에 대해 pHash 를 추출 (디스크 캐시 재사용).
2. pHash 유사도 ≥ ``PHASH_THRESHOLD`` 인 쌍만 후보로.
3. 후보 쌍에 대해 OpenCV phase correlation 으로 (dx, dy, peak) 측정 →
   ‘|dx|, |dy| 가 사진 변의 일정 비율 이하 + peak ≥ 임계’ 면 동일 defect.
4. union-find 로 그룹 병합 → 길이 ≥ 2 인 그룹만 반환 (싱글톤 제외).

성능:
- pHash 는 매우 빠르고 디스크 캐시 hit 이 일반적.
- phase correlation 입력은 256×256 로 다운샘플 → 한 쌍당 수 ms.
- pHash 필터로 거의 모든 무관한 쌍을 sub-millisecond 에 거른다.
- 슬롯당 수십~수백 장 가정에서 O(N²) 도 충분히 빠름 (수십초 이내).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional

import numpy as np

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..models.slot import ImageItem
from . import pipeline as _pipeline
from . import phash as _phash


# 임계값 — 사용자 데이터로 튜닝 가능.
#
# pHash 는 DCT 기반이라 작은 평행 이동에도 hash 가 비교적 큰 변화를 보인다.
# 같은 defect 가 5~10% 이동한 경우 실측 유사도가 0.65~0.85 사이.  너무 엄격하면
# 진짜 동일 defect 도 거른다.  여기선 ‘완전 무관 (≈0.5)’ 만 거르는 0.60 으로
# 느슨하게 두고, 실제 판정은 다음 단계인 phase correlation 에 맡긴다.
PHASH_THRESHOLD = 0.60
PEAK_THRESHOLD = 0.30         # phase correlation peak (정점 신뢰도)
MAX_SHIFT_FRAC = 0.20         # 변 길이의 20% 까지의 평행 이동 허용
DOWNSAMPLE_PX = 256           # phase correlation 입력 정사각 크기


@dataclass
class DefectGroup:
    """한 묶음의 동일 defect 사진들 (슬롯 단위)."""
    slot: str
    items: list[ImageItem]

    @property
    def size(self) -> int:
        return len(self.items)


# ---------------------------------------------------------------------------
# 핵심 — 한 슬롯 안에서 그룹 찾기
# ---------------------------------------------------------------------------
def _load_norm_gray(path: Path) -> Optional[np.ndarray]:
    """phase correlation 용 정규화 그레이 (DOWNSAMPLE_PX 정사각, float32)."""
    try:
        import cv2
    except Exception:
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    img = cv2.resize(img, (DOWNSAMPLE_PX, DOWNSAMPLE_PX),
                     interpolation=cv2.INTER_AREA)
    return img.astype(np.float32)


def _phase_corr_same_defect(gray_a: np.ndarray,
                             gray_b: np.ndarray) -> bool:
    """phase correlation 으로 ‘동일 이미지 + 작은 translation’ 판정."""
    try:
        import cv2
    except Exception:
        return False
    h, w = gray_a.shape
    window = cv2.createHanningWindow((w, h), cv2.CV_32F)
    a = gray_a * window
    b = gray_b * window
    try:
        (dx, dy), peak = cv2.phaseCorrelate(a, b)
    except Exception:
        return False
    max_shift = max(w, h) * MAX_SHIFT_FRAC
    return (abs(dx) < max_shift
            and abs(dy) < max_shift
            and peak >= PEAK_THRESHOLD)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


ProgressFn = Callable[[int, int, str], None]


def group_slot(items: Iterable[ImageItem],
               *,
               progress_cb: Optional[ProgressFn] = None,
               stop_fn: Optional[Callable[[], bool]] = None,
               ) -> list[list[ImageItem]]:
    """한 슬롯의 ImageItem 들을 ‘동일 defect’ 묶음으로 그룹화.

    반환 — 그룹별 list[ImageItem] 의 리스트. 싱글톤 (길이 1) 도 포함.
    호출자가 길이 ≥ 2 만 필터링하도록 권장 (호출자에 따라 다른 의미).
    """
    items_list = list(items)
    n = len(items_list)
    if n < 2:
        return [[it] for it in items_list]

    # 1) pHash 추출 (디스크 캐시 활용).
    phashes: list[Optional[np.ndarray]] = []
    for i, it in enumerate(items_list):
        if stop_fn is not None and stop_fn():
            return [[it] for it in items_list]
        try:
            phashes.append(_pipeline.extract(it.path).phash)
        except Exception:
            phashes.append(None)
        if progress_cb is not None:
            progress_cb(i + 1, n, "phash")

    uf = _UnionFind(n)
    gray_cache: dict[int, Optional[np.ndarray]] = {}

    def _gray(idx: int) -> Optional[np.ndarray]:
        if idx not in gray_cache:
            gray_cache[idx] = _load_norm_gray(items_list[idx].path)
        return gray_cache[idx]

    pair_count = n * (n - 1) // 2
    pair_done = 0
    for i in range(n):
        for j in range(i + 1, n):
            pair_done += 1
            if stop_fn is not None and stop_fn():
                break
            if progress_cb is not None and (pair_done % 20 == 0
                                             or pair_done == pair_count):
                progress_cb(pair_done, pair_count, "pairs")

            # 이미 같은 그룹이면 skip — 비싼 phase correlation 회피.
            if uf.find(i) == uf.find(j):
                continue

            # pHash 필터.
            fa, fb = phashes[i], phashes[j]
            if fa is None or fb is None:
                continue
            if _phash.phash_similarity(fa, fb) < PHASH_THRESHOLD:
                continue

            # phase correlation 검증.
            ga, gb = _gray(i), _gray(j)
            if ga is None or gb is None:
                continue
            if _phase_corr_same_defect(ga, gb):
                uf.union(i, j)
        if stop_fn is not None and stop_fn():
            break

    # 그룹 모으기 (root → items).
    by_root: dict[int, list[ImageItem]] = {}
    for i, it in enumerate(items_list):
        root = uf.find(i)
        by_root.setdefault(root, []).append(it)
    # 그룹 내부는 파일명 정렬, 그룹 자체는 size 내림차순 — 큰 묶음 먼저.
    out = [sorted(g, key=lambda x: x.path.name.lower())
           for g in by_root.values()]
    out.sort(key=lambda g: (-len(g), g[0].path.name.lower()))
    return out


# ---------------------------------------------------------------------------
# 백그라운드 워커
# ---------------------------------------------------------------------------
class GroupingSignals(QObject):
    progress = pyqtSignal(int, int, str)     # done, total, status_msg
    finished = pyqtSignal(list)              # list[DefectGroup]
    failed = pyqtSignal(str)


class GroupingWorker(QThread):
    """주어진 슬롯들의 동일 defect 그룹을 한 번에 계산."""

    def __init__(self,
                 items_by_slot: dict[str, list[ImageItem]],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._items_by_slot = {k: list(v) for k, v in items_by_slot.items()}
        self.signals = GroupingSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        try:
            slots_sorted = sorted(self._items_by_slot.keys())
            n_slots = len(slots_sorted)
            groups_out: list[DefectGroup] = []
            for s_idx, slot in enumerate(slots_sorted, start=1):
                if self._stop:
                    return
                items = self._items_by_slot[slot]
                # 슬롯별 진행률을 ‘슬롯 i/N — pHash/pairs done/total’ 형식으로.

                def _cb(done: int, total: int, stage: str,
                        slot=slot, s_idx=s_idx, n_slots=n_slots) -> None:
                    self.signals.progress.emit(
                        done, total,
                        f"슬롯 {s_idx}/{n_slots} — {slot} ({stage})",
                    )

                groups = group_slot(
                    items, progress_cb=_cb, stop_fn=lambda: self._stop,
                )
                # 의미 있는 묶음만 (≥2). 싱글톤은 ‘그룹 아님’ 으로 제외.
                for g in groups:
                    if len(g) >= 2:
                        groups_out.append(DefectGroup(slot=slot, items=g))
            self.signals.finished.emit(groups_out)
        except Exception as exc:        # pragma: no cover
            self.signals.failed.emit(str(exc))
