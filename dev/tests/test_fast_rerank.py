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


def test_score_new_single_component_subsets_normalized():
    # 새 모드(ORB 단독 / SSIM 단독 / pHash+ORB)도 [0,1] 범위로 정규화된다.
    a, b = _feat(11), _feat(12)
    for comp in ({"orb"}, {"ssim"}, {"phash", "orb"}):
        s = P.score(a, b, components=comp)
        assert 0.0 <= s <= 1.0


def test_orb_nfeatures_limits_keypoints():
    # CPU 재채점 고속화 노브 — ORB 특징 수를 줄이면 키포인트가 그만큼 이하로 준다.
    from aoi_verification.app.similarity import orb as O
    rng = np.random.default_rng(7)
    img = (rng.integers(0, 255, (200, 200))).astype("uint8")
    full = O.compute_orb(img)                    # 기본 500
    few = O.compute_orb(img, nfeatures=64)
    assert few.keypoints <= full.keypoints
    assert few.keypoints <= 64                   # 요청한 상한 이내


def test_orb_nfeatures_flows_through_cfg_in_extract(tmp_path):
    # cfg.orb_nfeatures 가 extract → compute_orb 로 전달돼 ORB 특징 수를 제한한다.
    import cv2
    from aoi_verification.app import config as C
    p = tmp_path / "x.png"
    rng = np.random.default_rng(3)
    cv2.imwrite(str(p), (rng.integers(0, 255, (160, 160, 3))).astype("uint8"))
    cfg_full = C.SimilarityConfig(bench_no_cache=True)
    cfg_few = C.SimilarityConfig(bench_no_cache=True, orb_nfeatures=48)
    f_full = P.extract(p, cfg=cfg_full, side="ref")
    f_few = P.extract(p, cfg=cfg_few, side="ref")
    assert f_few.orb_kp <= 48
    assert f_few.orb_kp <= f_full.orb_kp
