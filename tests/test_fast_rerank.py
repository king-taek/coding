"""고속 CPU 재채점 — pipeline.score 부분 컴포넌트 / 경량 추출(headless, 실제 CPU)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from aoi_verification.app.similarity import pipeline as P


def _feat(seed, phash_bits=None):
    rng = np.random.default_rng(seed)
    return P.Feature(
        path=Path(f"/x{seed}.jpg"),
        phash=(phash_bits if phash_bits is not None
               else (rng.random(64) > 0.5).astype("uint8")),
        orb_kp=12, orb_desc=(rng.integers(0, 256, (12, 32))).astype("uint8"),
        roi_gray=(rng.integers(0, 255, (48, 48))).astype("uint8"),
    )


def test_score_full_default_unchanged():
    a, b = _feat(1), _feat(2)
    # components=None(기본)은 전체 항 — 값이 [0,1] 이고 결정적.
    s = P.score(a, b)
    assert 0.0 <= s <= 1.0
    assert P.score(a, b) == s


def test_score_phash_only_matches_phash_similarity():
    from aoi_verification.app.similarity import phash as ph
    bits = (np.arange(64) % 2).astype("uint8")
    a = _feat(3, phash_bits=bits.copy())
    b = _feat(4, phash_bits=bits.copy())          # 동일 해시 → pHash 유사도 1.0
    s = P.score(a, b, components={"phash"})
    assert abs(s - ph.phash_similarity(a.phash, b.phash)) < 1e-9
    assert s == 1.0


def test_score_subset_is_normalized_0_1():
    a, b = _feat(5), _feat(6)
    for comp in ({"phash"}, {"phash", "ssim"}, {"orb", "ssim"}):
        s = P.score(a, b, components=comp)
        assert 0.0 <= s <= 1.0


def test_score_components_skip_orb_ignores_missing_desc():
    # ORB 디스크립터가 없어도(None) pHash/SSIM 부분 채점은 동작.
    a = _feat(7)
    b = P.Feature(path=Path("/n.jpg"), phash=a.phash.copy(), orb_kp=0,
                  orb_desc=None, roi_gray=a.roi_gray.copy())
    s = P.score(a, b, components={"phash", "ssim"})
    assert s > 0.9            # 동일 해시 + 동일 ROI → 높은 유사도
