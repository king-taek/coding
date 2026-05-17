"""‘동일 defect (촬영 위치/배율/조명 차)’ 그룹화 — ORB+RANSAC 버전.

사용자 통찰: 동일 장비 + 동일 슬롯에서 같은 defect 사진들은 같은 회로
영역을 보여주되 카메라 위치, 배율 (해상도), 또는 조명/화이트밸런스가
달라질 수 있다 (실제 예시: 1000×1000 vs 600×600 동일 defect, 또는
1000×1000 같은 장비/슬롯이지만 ~12% 평행 이동 + 주황 vs 흰빛 화이트밸런스).

알고리즘 (이전 phase correlation 버전을 ORB+RANSAC 으로 교체):
1. 슬롯의 모든 이미지에 대해 pHash 를 추출 (디스크 캐시 재사용).
2. pHash 유사도 ≥ ``PHASH_THRESHOLD`` 인 쌍만 후보로 — scale/조명
   변형으로 pHash 가 더 떨어지므로 ‘완전 무관 (≈0.5)’ 만 거른다.
3. 후보 쌍에 대해 ORB 특징점 매칭 (BFMatcher + Lowe ratio test) →
   ``cv2.estimateAffinePartial2D`` 로 similarity transform (translation
   + rotation + uniform scale) 추정.  변환의 scale/rotation/translation
   이 모두 ‘허용 범위’ 내 + inlier 수/비율 충분하면 동일 defect.
4. union-find 로 그룹 병합.

ORB 가 scale + rotation + 조명 변화에 robust 하므로 phase correlation
(평행이동만 가능) 의 한계를 극복.

성능:
- ORB keypoints+descriptors 는 grouping 1 세션 동안 로컬 dict 캐시
  (Feature.npz 포맷 변경 없이 호환).
- 한 이미지당 ORB 계산 ~5~10ms.  슬롯당 수십~수백 장 → 1 ~ 수 초.
- pHash 1 차 필터로 무관 쌍을 sub-ms 에 거른다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

import numpy as np

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..models.slot import ImageItem
from . import pipeline as _pipeline
from . import phash as _phash


# 임계값 — 사용자 데이터로 튜닝 가능.
#
# pHash 는 DCT 기반이라 작은 평행이동 / 스케일 / 화이트밸런스 변형에도
# hash 가 흔들린다 (사용자 예시 2 의 주황 vs 흰빛 케이스에서 0.48 까지
# 떨어짐).  실제 판정은 ORB+RANSAC 가 담당하므로 pHash 는 ‘분명히 다른
# 화면 (그레이/구조 완전 다름)’ 만 빠르게 걸러내는 매우 약한 필터로 사용.
PHASH_THRESHOLD = 0.45

# ORB + RANSAC 임계.
ORB_NFEATURES = 800           # 검출할 keypoint 수 (default 500 보다 풍부)
ORB_RATIO_TEST = 0.75         # Lowe ratio test
ORB_MIN_INLIERS = 12          # RANSAC inlier 최소 매치 수
ORB_INLIER_RATIO = 0.30       # inliers / good_matches 최소 비율
ORB_SCALE_RANGE = (0.5, 2.0)  # 배율 차이 0.5x ~ 2x 허용 (예시 1: 1.67x)
ORB_MAX_ROT_DEG = 20.0        # 회전 ±20°
ORB_MAX_TRANS_FRAC = 0.30     # 평행이동 ≤ 입력 변의 30%
ORB_INPUT_PX = 384            # 입력 이미지를 한 정사각 캔버스로 정규화


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
def _load_gray_normalized(path: Path) -> Optional[np.ndarray]:
    """``ORB_INPUT_PX`` 정사각 그레이 (uint8) — ORB 정확도/속도 균형."""
    try:
        import cv2
    except Exception:
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    # CLAHE 로 조명/contrast 변화 흡수 (예시 2 의 주황 vs 흰빛 화이트밸런스).
    try:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        img = clahe.apply(img)
    except Exception:
        pass
    return cv2.resize(img, (ORB_INPUT_PX, ORB_INPUT_PX),
                      interpolation=cv2.INTER_AREA)


def _orb_kp_desc(gray: np.ndarray):
    """ORB keypoints + descriptors (positions 포함) — None 시 매칭 불가."""
    try:
        import cv2
    except Exception:
        return None, None
    orb = cv2.ORB_create(
        nfeatures=ORB_NFEATURES,
        scaleFactor=1.2,
        nlevels=8,
        edgeThreshold=15,
        WTA_K=2,
        scoreType=cv2.ORB_HARRIS_SCORE,
        patchSize=21,
    )
    kp, desc = orb.detectAndCompute(gray, None)
    if desc is None or len(kp) < ORB_MIN_INLIERS:
        return None, None
    return kp, desc


def _orb_ransac_same_defect(kp_a, desc_a, kp_b, desc_b) -> bool:
    """ORB BFMatcher (Lowe ratio) + RANSAC similarity transform.

    True 면 ‘동일 defect (translation + 작은 회전 + 배율차)’ 로 판정.
    """
    try:
        import cv2
    except Exception:
        return False
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    try:
        knn = bf.knnMatch(desc_a, desc_b, k=2)
    except cv2.error:
        return False
    good = []
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ORB_RATIO_TEST * n.distance:
            good.append(m)
    if len(good) < ORB_MIN_INLIERS:
        return False

    pts_a = np.float32([kp_a[m.queryIdx].pt for m in good])
    pts_b = np.float32([kp_b[m.trainIdx].pt for m in good])
    M, inliers = cv2.estimateAffinePartial2D(
        pts_a, pts_b,
        method=cv2.RANSAC,
        ransacReprojThreshold=3.0,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if M is None or inliers is None:
        return False
    n_inliers = int(inliers.sum())
    if n_inliers < ORB_MIN_INLIERS:
        return False
    if n_inliers / float(len(good)) < ORB_INLIER_RATIO:
        return False

    # 2×3 partial affine: M = [[s·cosθ, -s·sinθ, tx],
    #                          [s·sinθ,  s·cosθ, ty]]
    a, _b, tx = float(M[0, 0]), float(M[0, 1]), float(M[0, 2])
    c, _d, ty = float(M[1, 0]), float(M[1, 1]), float(M[1, 2])
    scale = float(np.hypot(a, c))
    if scale <= 0:
        return False
    angle_deg = abs(float(np.degrees(np.arctan2(c, a))))
    if angle_deg > 180.0:
        angle_deg = 360.0 - angle_deg
    if not (ORB_SCALE_RANGE[0] <= scale <= ORB_SCALE_RANGE[1]):
        return False
    if angle_deg > ORB_MAX_ROT_DEG:
        return False
    max_t = ORB_INPUT_PX * ORB_MAX_TRANS_FRAC
    if abs(tx) > max_t or abs(ty) > max_t:
        return False
    return True


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
    # ORB keypoints+descriptors 는 한 이미지당 한 번만 계산해 로컬 dict 캐시.
    # (Feature.npz 포맷 변경 없이 grouping 세션 동안만 RAM 보관.)
    orb_cache: dict[int, Tuple[object, Optional[np.ndarray]]] = {}

    def _orb(idx: int) -> Tuple[object, Optional[np.ndarray]]:
        if idx not in orb_cache:
            gray = _load_gray_normalized(items_list[idx].path)
            if gray is None:
                orb_cache[idx] = (None, None)
            else:
                orb_cache[idx] = _orb_kp_desc(gray)
        return orb_cache[idx]

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

            # 이미 같은 그룹이면 skip — 비싼 ORB 매칭 회피.
            if uf.find(i) == uf.find(j):
                continue

            # pHash 1 차 필터.
            fa, fb = phashes[i], phashes[j]
            if fa is None or fb is None:
                continue
            if _phash.phash_similarity(fa, fb) < PHASH_THRESHOLD:
                continue

            # ORB + RANSAC 검증.
            kp_a, desc_a = _orb(i)
            kp_b, desc_b = _orb(j)
            if desc_a is None or desc_b is None:
                continue
            if _orb_ransac_same_defect(kp_a, desc_a, kp_b, desc_b):
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
