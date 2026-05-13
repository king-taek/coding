"""Perceptual Hash (pHash) — 빠른 1차 필터링."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

try:
    import imagehash  # type: ignore
    _HAS_IMAGEHASH = True
except Exception:  # pragma: no cover
    imagehash = None  # type: ignore
    _HAS_IMAGEHASH = False


_HASH_SIZE = 16   # 16x16 = 256-bit hash


def compute_phash(roi_gray: np.ndarray) -> np.ndarray:
    """중심 ROI(그레이) 로부터 pHash 비트벡터를 만든다.

    `imagehash` 가 있으면 그 결과를, 없으면 DCT-기반 자체 구현을 사용.
    반환은 항상 길이 N 의 0/1 NumPy uint8 배열.
    """
    if _HAS_IMAGEHASH:
        pil = Image.fromarray(roi_gray, mode="L")
        ih = imagehash.phash(pil, hash_size=_HASH_SIZE)
        return np.asarray(ih.hash.flatten(), dtype=np.uint8)
    return _phash_fallback(roi_gray, _HASH_SIZE)


def _phash_fallback(gray: np.ndarray, hash_size: int) -> np.ndarray:
    """DCT 없이도 동작하는 단순 차분-기반 fallback."""
    img = Image.fromarray(gray, mode="L").resize(
        (hash_size + 1, hash_size), Image.LANCZOS,
    )
    arr = np.asarray(img, dtype=np.int16)
    diff = (arr[:, 1:] > arr[:, :-1]).astype(np.uint8)
    return diff.flatten()


def phash_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Hamming 유사도 — 0.0 ~ 1.0 (1.0 이 가장 비슷)."""
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    dist = int(np.count_nonzero(a != b))
    return 1.0 - dist / float(a.size)
