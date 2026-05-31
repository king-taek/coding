"""ORB 키포인트 매칭 점수.

조명/회전/contrast 변화에 강해서 호기 간 차이를 잘 흡수한다.

``center_strength`` 로 **중앙(defect) 근접 키포인트의 매치에 가중**할 수 있다 — AOI
이미지는 defect 이 정중앙이라, 중앙 매치를 키우면 배경(반복 패턴) 매치의 영향을 줄여
defect 판별력을 높이는 단일-패스 변형(사용자 제안의 올바른 구현).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np


@dataclass
class OrbDescriptor:
    keypoints: int
    descriptors: np.ndarray | None     # (N, 32) uint8
    coords: Optional[np.ndarray] = None   # (N, 2) float32 키포인트 (x, y) — 중앙가중용
    shape: tuple = (0, 0)                 # (h, w) ROI 크기 — 중앙 기준


def _orb_create(nfeatures: int = 500):
    import cv2
    return cv2.ORB_create(
        nfeatures=int(nfeatures) if nfeatures and nfeatures > 0 else 500,
        scaleFactor=1.2,
        nlevels=6,
        edgeThreshold=10,
        WTA_K=2,
        scoreType=0,           # HARRIS_SCORE
        patchSize=21,
    )


def compute_orb(roi_gray: np.ndarray, *, nfeatures: int = 0) -> OrbDescriptor:
    """ROI 에서 ORB 키포인트와 descriptor 계산.

    ``nfeatures`` >0 이면 검출 특징 수를 그 값으로 제한한다(0=기본 500).  특징을
    줄이면 검출/정합 비용이 작아져 CPU 재채점이 빨라진다(개발자 벤치마크 고속 변형).
    키포인트 (x,y) 좌표도 함께 보관해 중앙-가중 채점에 쓴다."""
    import cv2
    orb = _orb_create(nfeatures)
    kp, desc = orb.detectAndCompute(roi_gray, None)
    if desc is None:
        return OrbDescriptor(keypoints=0, descriptors=None, coords=None,
                             shape=tuple(roi_gray.shape[:2]))
    coords = np.array([k.pt for k in kp], dtype=np.float32) if kp else None
    return OrbDescriptor(keypoints=len(kp), descriptors=desc, coords=coords,
                         shape=tuple(roi_gray.shape[:2]))


def centrality_weights(coords: np.ndarray, shape: Sequence[int],
                       strength: float) -> np.ndarray:
    """키포인트별 중앙 근접 가중치 — 중앙=1.0, 가장자리=1-strength (클램프 [0,1]).

    순수 함수(헤드리스 테스트).  ``strength`` 0 이면 전부 1.0(가중 없음)."""
    coords = np.asarray(coords, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[0] == 0:
        return np.ones((0,), dtype=np.float64)
    h, w = (shape[0], shape[1]) if shape and shape[0] else (
        float(coords[:, 1].max()) + 1.0, float(coords[:, 0].max()) + 1.0)
    cx, cy = w / 2.0, h / 2.0
    maxr = math.hypot(cx, cy) or 1.0
    d = np.hypot(coords[:, 0] - cx, coords[:, 1] - cy) / maxr      # [0, ~1]
    return np.clip(1.0 - float(strength) * d, 0.0, 1.0)


def centrality_weighted_ratio(good_query_idx: Sequence[int], coords: Optional[np.ndarray],
                              shape: Sequence[int], strength: float, base: int) -> float:
    """중앙-가중 매치 비율.  strength=0 또는 좌표 없음이면 단순 good/base 와 동일.

    good 매치를 query 키포인트의 중앙 근접도로 가중하고, 전체 키포인트의 평균 가중으로
    정규화해 [0,1] 스케일·strength=0 등가성을 유지한다(순수 함수)."""
    base = max(int(base), 1)
    if strength <= 0 or coords is None or np.asarray(coords).size == 0:
        return max(0.0, min(1.0, len(good_query_idx) / float(base)))
    cw = centrality_weights(coords, shape, strength)
    if cw.size == 0:
        return max(0.0, min(1.0, len(good_query_idx) / float(base)))
    mean_cw = float(cw.mean())
    if mean_cw <= 0:
        return max(0.0, min(1.0, len(good_query_idx) / float(base)))
    gi = [i for i in good_query_idx if 0 <= i < cw.size]
    good_w = float(cw[gi].sum()) if gi else 0.0
    return max(0.0, min(1.0, good_w / (mean_cw * float(base))))


def orb_score(a: OrbDescriptor, b: OrbDescriptor, *, center_strength: float = 0.0) -> float:
    """매칭된 ‘좋은 매치’ 비율 (0.0 ~ 1.0).

    ``center_strength`` >0 이면 중앙(defect) 근접 매치에 가중한다(a 의 키포인트 기준)."""
    if a.descriptors is None or b.descriptors is None:
        return 0.0
    if a.descriptors.size == 0 or b.descriptors.size == 0:
        return 0.0

    import cv2
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    try:
        knn = bf.knnMatch(a.descriptors, b.descriptors, k=2)
    except cv2.error:
        return 0.0

    good_q: list[int] = []
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        # Lowe's ratio test
        if m.distance < 0.75 * n.distance:
            good_q.append(int(m.queryIdx))

    base = max(min(a.keypoints, b.keypoints), 1)
    if center_strength > 0:
        return centrality_weighted_ratio(good_q, a.coords, a.shape, center_strength, base)
    return max(0.0, min(1.0, len(good_q) / float(base)))
