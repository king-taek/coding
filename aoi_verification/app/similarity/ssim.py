"""Structural Similarity Index (SSIM) — 중심 ROI 에 대해 계산."""

from __future__ import annotations

import numpy as np

try:
    from skimage.metrics import structural_similarity as _ssim  # type: ignore
    _HAS_SKIMAGE = True
except Exception:  # pragma: no cover
    _HAS_SKIMAGE = False


# 고정 비교 해상도 — 두 이미지를 항상 같은 크기로 맞춰 비교 (작은 쪽으로
# 맞추던 기존 방식은 종횡비가 다를 때 왜곡/오정렬을 유발했다, #2/#5).
_CMP_PX = 256


def ssim_score(a_gray: np.ndarray, b_gray: np.ndarray) -> float:
    """구조 유사도 점수 — 0.0 ~ 1.0.

    정확도 강화 (#5): 두 이미지를 동일한 고정 해상도(256²)로 맞춘 뒤 SSIM 과
    정규화 상호상관(NCC)을 함께 사용해 블렌딩한다.  SSIM 은 국소 구조를, NCC 는
    전역 패턴 정합을 보므로 조명/노이즈 변화에 더 강인하다.
    """
    if a_gray.size == 0 or b_gray.size == 0:
        return 0.0

    try:
        import cv2  # local import — heavy
        a2 = cv2.resize(a_gray, (_CMP_PX, _CMP_PX), interpolation=cv2.INTER_AREA)
        b2 = cv2.resize(b_gray, (_CMP_PX, _CMP_PX), interpolation=cv2.INTER_AREA)
    except Exception:
        a2, b2 = a_gray, b_gray
        if a2.shape != b2.shape:                # cv2 없을 때 최소 보정
            h = min(a2.shape[0], b2.shape[0])
            w = min(a2.shape[1], b2.shape[1])
            a2 = a2[:h, :w]
            b2 = b2[:h, :w]

    if _HAS_SKIMAGE:
        s_ssim = float(_ssim(a2, b2, data_range=255))
    else:
        s_ssim = _ssim_fallback(a2, b2)
    s_ncc = _ncc(a2, b2)
    # SSIM(국소 구조) 70% + NCC(전역 상관) 30% 블렌딩.
    score = 0.7 * s_ssim + 0.3 * s_ncc
    return max(0.0, min(1.0, score))


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    """정규화 상호상관 (Normalized Cross-Correlation) → [-1,1] → [0,1] 클램프."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    denom = float(np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
    if denom <= 1e-9:
        return 0.0
    return max(0.0, min(1.0, float((a * b).sum() / denom)))


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
