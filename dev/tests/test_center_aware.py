"""중앙-인식(center-aware) 채점 — 헤드리스 단위 테스트.

defect 이 정중앙인 AOI 이미지 특성을 활용하는 부분: ``config`` 의 center_ratio 가 ROI
비율과 캐시 키에 반영되는지, ``similarity/orb`` 의 중앙-가중 로직이 중앙 매치를 더 높게
치는지를 무거운 의존성 없이 본다.

※ 개발자 벤치마크(레시피·영역융합·캐스케이드)를 다루던 테스트는 그 기능과 함께 제거됐다.
"""

from __future__ import annotations

from dataclasses import replace

from aoi_verification.app.config import SimilarityConfig


# ── config: center_ratio 가 ROI 비율/캐시 키에 반영 ───────────────────────
def test_center_ratio_cache_key_and_ratio():
    c25 = replace(SimilarityConfig(center_crop=True), center_ratio=0.25)
    assert c25._center_crop_ratio() == 0.25
    assert c25.cache_extra("ref") == "c25"          # side 별 키 분리
    assert c25.cache_extra(None) == ""              # side 미지정 → crop 안 함
    legacy = SimilarityConfig(center_crop=True)      # ratio 0 → 레거시 0.3
    assert legacy._center_crop_ratio() == 0.3 and legacy.cache_extra("val") == "c30"


# ── 중앙-가중 ORB(단일 패스) — 순수 가중 로직(cv2 불필요) ────────────────────
def test_centrality_weights_center_high_edge_low():
    import numpy as np
    from aoi_verification.app.similarity import orb
    coords = np.array([[50, 50], [0, 0], [100, 100]], dtype=float)  # 중앙·모서리·모서리
    w = orb.centrality_weights(coords, (100, 100), 1.0)
    assert abs(w[0] - 1.0) < 1e-9 and w[1] < 0.1 and w[2] < 0.1


def test_centrality_weighted_ratio_strength0_equals_plain_and_upweights_center():
    import numpy as np
    from aoi_verification.app.similarity import orb
    coords = np.array([[50, 50], [0, 0], [100, 100]], dtype=float)
    # strength=0 → 단순 good/base
    assert abs(orb.centrality_weighted_ratio([0, 1], coords, (100, 100), 0.0, 3) - 2 / 3) < 1e-9
    # 좌표 없음 → 폴백(good/base)
    assert abs(orb.centrality_weighted_ratio([0, 1], None, (100, 100), 0.5, 4) - 0.5) < 1e-9
    # strength>0 → 중앙 매치가 가장자리 매치보다 높은 점수
    c = orb.centrality_weighted_ratio([0], coords, (100, 100), 0.8, 3)
    e = orb.centrality_weighted_ratio([1], coords, (100, 100), 0.8, 3)
    assert c > e


def test_orb_descriptor_carries_coords():
    from aoi_verification.app.similarity import orb
    od = orb.OrbDescriptor(keypoints=0, descriptors=None)   # 기본값 — 좌표/shape 옵션
    assert od.coords is None and od.shape == (0, 0)
