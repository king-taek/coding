"""GL 분석 — 히스토그램·ROI 통계·선 프로파일·임계값 판정·화면 표시용 변환.

모두 **원본 비트심도 그대로** 계산한다.  8 bit 로 줄여 센 값은 쓰지 않는다
(이 앱은 검증 신뢰성이 우선이다).  화면 표시용 8 bit 변환은 ``to_display`` 하나뿐이고,
분석 함수는 그 결과를 보지 않는다.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .roi import CIRCLE, HLINE, VLINE, Roi

LOW_RGB = (40, 128, 255)       # 하한 이하 — 파랑
HIGH_RGB = (255, 44, 36)       # 상한 이상 — 빨강
SCOPE_ALL, SCOPE_ROI, SCOPE_OFF = "all", "roi", "off"


def levels(bits: int) -> int:
    return 1 << int(bits)


# ---------------------------------------------------------------------------
# ROI → 값
# ---------------------------------------------------------------------------
def roi_mask(roi: Roi, shape_hw: tuple[int, int]) -> np.ndarray:
    """ROI 안쪽 픽셀 불리언 마스크(이미지 전체 크기).  원은 **픽셀 중심** 기준으로 판정한다."""
    h, w = shape_hw
    m = np.zeros((h, w), dtype=bool)
    if roi.kind == HLINE:
        if 0 <= roi.pos < h:
            m[roi.pos, :] = True
        return m
    if roi.kind == VLINE:
        if 0 <= roi.pos < w:
            m[:, roi.pos] = True
        return m
    x0, y0 = max(0, roi.x), max(0, roi.y)
    x1, y1 = min(w, roi.x + roi.w), min(h, roi.y + roi.h)
    if x1 <= x0 or y1 <= y0:
        return m
    if roi.kind == CIRCLE:
        cx, cy = roi.x + roi.w / 2.0, roi.y + roi.h / 2.0
        rx, ry = roi.w / 2.0, roi.h / 2.0
        ys, xs = np.mgrid[y0:y1, x0:x1]
        m[y0:y1, x0:x1] = (((xs + 0.5 - cx) / rx) ** 2 + ((ys + 0.5 - cy) / ry) ** 2) <= 1.0
    else:
        m[y0:y1, x0:x1] = True
    return m


def roi_values(img: np.ndarray, roi: Roi) -> np.ndarray:
    """ROI 안의 GL 값 1차원 배열(복사본 아님일 수 있다 — 읽기만 한다)."""
    h, w = img.shape
    if roi.kind == HLINE:
        return img[roi.pos, :] if 0 <= roi.pos < h else img[:0, 0]
    if roi.kind == VLINE:
        return img[:, roi.pos] if 0 <= roi.pos < w else img[:0, 0]
    x0, y0 = max(0, roi.x), max(0, roi.y)
    x1, y1 = min(w, roi.x + roi.w), min(h, roi.y + roi.h)
    if x1 <= x0 or y1 <= y0:
        return img[:0, 0]
    part = img[y0:y1, x0:x1]
    if roi.kind != CIRCLE:
        return part.reshape(-1)
    cx, cy = roi.x + roi.w / 2.0, roi.y + roi.h / 2.0
    ys, xs = np.mgrid[y0:y1, x0:x1]
    inside = (((xs + 0.5 - cx) / (roi.w / 2.0)) ** 2
              + ((ys + 0.5 - cy) / (roi.h / 2.0)) ** 2) <= 1.0
    return part[inside]


# ---------------------------------------------------------------------------
# 히스토그램·통계
# ---------------------------------------------------------------------------
def histogram(values: np.ndarray, bits: int) -> np.ndarray:
    """GL 값별 픽셀 수(길이 ``2**bits``).  범위 밖 값은 맨 끝에 모은다(조용히 버리지 않는다)."""
    n = levels(bits)
    v = np.asarray(values).reshape(-1)
    if v.size == 0:
        return np.zeros(n, dtype=np.int64)
    if int(v.max()) >= n:
        v = np.minimum(v, n - 1)
    return np.bincount(v.astype(np.int64, copy=False), minlength=n).astype(np.int64)


def stats(values: np.ndarray, lo: int, hi: int) -> dict:
    """픽셀 수·평균·표준편차(모집단)·최소·최대·하한 이하·상한 이상.

    비어 있으면 ``n == 0`` 이고 나머지는 ``None``/0 이다."""
    v = np.asarray(values).reshape(-1)
    n = int(v.size)
    if n == 0:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None,
                "below": 0, "above": 0}
    f = v.astype(np.float64)
    return {"n": n, "mean": float(f.mean()), "std": float(f.std()),
            "min": int(v.min()), "max": int(v.max()),
            "below": int((v <= lo).sum()), "above": int((v >= hi).sum())}


def profile(img: np.ndarray, roi: Roi) -> Optional[np.ndarray]:
    """선 위의 위치별 GL.  선이 아니면 ``None``(도형은 프로파일이 없다)."""
    if roi.kind not in (HLINE, VLINE):
        return None
    return np.array(roi_values(img, roi))


def auto_thresholds(hist: np.ndarray, tail: float = 0.005) -> tuple[int, int]:
    """하위·상위 ``tail`` 비율 지점 → (하한, 상한).  하한 < 상한을 보장한다."""
    total = int(hist.sum())
    n = len(hist)
    if total == 0:
        return 0, n - 1
    c = np.cumsum(hist)
    lo = int(np.searchsorted(c, total * tail, side="left"))
    hi = int(np.searchsorted(c, total * (1.0 - tail), side="left"))
    lo = max(0, min(lo, n - 2))
    hi = max(lo + 1, min(hi, n - 1))
    return lo, hi


def clamp_thresholds(lo: int, hi: int, bits: int) -> tuple[int, int]:
    """범위 안으로 가두고 하한 < 상한을 지킨다(어긋나면 상한을 하한+1 로)."""
    top = levels(bits) - 1
    lo = max(0, min(int(lo), top - 1))
    hi = max(lo + 1, min(int(hi), top))
    return lo, hi


# ---------------------------------------------------------------------------
# 화면 표시
# ---------------------------------------------------------------------------
def display_range(img: np.ndarray, bits: int, auto: bool) -> tuple[int, int]:
    """화면 대비 범위.  ``auto`` 면 실제 최소~최대로 늘린다(보기 전용 — 분석 값은 그대로)."""
    if auto and img.size:
        lo, hi = int(img.min()), int(img.max())
        if hi > lo:
            return lo, hi
    return 0, levels(bits) - 1


def to_display(img: np.ndarray, rng: tuple[int, int]) -> np.ndarray:
    """원본 → 화면용 8 bit 회색(uint8).  분석에는 쓰지 않는다."""
    lo, hi = rng
    span = max(1, hi - lo)
    f = (img.astype(np.float32) - lo) * (255.0 / span)
    return np.clip(f, 0, 255).astype(np.uint8)


def overlay_rgb(img: np.ndarray, rng: tuple[int, int], lo: int, hi: int,
                scope: str, rois: list[Roi]) -> np.ndarray:
    """화면용 RGB(H×W×3 uint8): 회색 위에 하한 이하=파랑, 상한 이상=빨강.

    ``scope`` — 전체 / ROI 안쪽만(``rois`` 합집합) / 끄기.  하한<상한이라 두 색은 겹치지 않는다."""
    g = to_display(img, rng)
    rgb = np.repeat(g[:, :, None], 3, axis=2)
    if scope == SCOPE_OFF:
        return rgb
    low = img <= lo
    high = img >= hi
    if scope == SCOPE_ROI:
        inside = np.zeros(img.shape, dtype=bool)
        for r in rois:
            inside |= roi_mask(r, img.shape)
        low &= inside
        high &= inside
    rgb[low] = LOW_RGB
    rgb[high] = HIGH_RGB
    return rgb
