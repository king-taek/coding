"""ORB 키포인트 매칭 점수.

조명/회전/contrast 변화에 강해서 호기 간 차이를 잘 흡수한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class OrbDescriptor:
    keypoints: int
    descriptors: np.ndarray | None     # (N, 32) uint8


def _orb_create():
    import cv2
    return cv2.ORB_create(
        nfeatures=500,
        scaleFactor=1.2,
        nlevels=6,
        edgeThreshold=10,
        WTA_K=2,
        scoreType=0,           # HARRIS_SCORE
        patchSize=21,
    )


def compute_orb(roi_gray: np.ndarray) -> OrbDescriptor:
    """ROI 에서 ORB 키포인트와 descriptor 계산."""
    import cv2
    orb = _orb_create()
    kp, desc = orb.detectAndCompute(roi_gray, None)
    if desc is None:
        return OrbDescriptor(keypoints=0, descriptors=None)
    return OrbDescriptor(keypoints=len(kp), descriptors=desc)


def orb_score(a: OrbDescriptor, b: OrbDescriptor) -> float:
    """매칭된 ‘좋은 매치’ 비율 (0.0 ~ 1.0)."""
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

    good = 0
    for pair in knn:
        if len(pair) < 2:
            continue
        m, n = pair
        # Lowe's ratio test
        if m.distance < 0.75 * n.distance:
            good += 1

    base = max(min(a.keypoints, b.keypoints), 1)
    score = good / float(base)
    return max(0.0, min(1.0, score))
