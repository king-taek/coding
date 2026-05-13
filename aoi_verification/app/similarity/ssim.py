"""Structural Similarity Index (SSIM) — 중심 ROI 에 대해 계산."""

from __future__ import annotations

import numpy as np

try:
    from skimage.metrics import structural_similarity as _ssim  # type: ignore
    _HAS_SKIMAGE = True
except Exception:  # pragma: no cover
    _HAS_SKIMAGE = False


def ssim_score(a_gray: np.ndarray, b_gray: np.ndarray) -> float:
    """SSIM 점수 — 0.0 ~ 1.0 (음수가 나올 수 있어 0 으로 클램프)."""
    if a_gray.size == 0 or b_gray.size == 0:
        return 0.0

    # 모양을 맞춰주기 위해 작은 쪽으로 리사이즈
    if a_gray.shape != b_gray.shape:
        import cv2  # local import — heavy
        h = min(a_gray.shape[0], b_gray.shape[0])
        w = min(a_gray.shape[1], b_gray.shape[1])
        a_gray = cv2.resize(a_gray, (w, h), interpolation=cv2.INTER_AREA)
        b_gray = cv2.resize(b_gray, (w, h), interpolation=cv2.INTER_AREA)

    if _HAS_SKIMAGE:
        score = float(_ssim(a_gray, b_gray, data_range=255))
    else:
        score = _ssim_fallback(a_gray, b_gray)
    return max(0.0, min(1.0, score))


def _ssim_fallback(a: np.ndarray, b: np.ndarray) -> float:
    """skimage 없을 때를 위한 단순 SSIM (글로벌)."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    mu_a = a.mean(); mu_b = b.mean()
    va = a.var(); vb = b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    num = (2 * mu_a * mu_b + c1) * (2 * cov + c2)
    den = (mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2)
    if den == 0:
        return 0.0
    return num / den
