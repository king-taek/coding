"""중앙-인식(center-aware) 채점 + 린 프리셋 정리 + NPU 진단 — 헤드리스 단위 테스트.

defect 이 정중앙인 AOI 이미지 특성을 활용하는 신규 레시피(영역융합 A·캐스케이드 B)와,
개발자 모드 테스트 목록 정리(린 기본 + faceoff), 영역 점수 융합/캐스케이드 순수 로직,
config 의 center_ratio 캐시 키를 무거운 의존성 없이 검증한다.
"""

from __future__ import annotations

from dataclasses import replace

from aoi_verification.app.config import SimilarityConfig
from aoi_verification.app.dev import benchmark as bm
from aoi_verification.app.dev import recipes as rx


# ── 순수 함수: 영역 점수 융합 / 캐스케이드 선택 ─────────────────────────────
def test_blend_region_scores_weighted():
    cmap = {"a": 0.9, "b": 0.2}
    fmap = {"a": 0.4, "b": 0.8}
    out = bm.blend_region_scores(cmap, fmap, 0.6)
    assert abs(out["a"] - (0.6 * 0.9 + 0.4 * 0.4)) < 1e-9
    assert abs(out["b"] - (0.6 * 0.2 + 0.4 * 0.8)) < 1e-9


def test_blend_handles_missing_and_clamps_weight():
    out = bm.blend_region_scores({"a": 1.0}, {"b": 1.0}, 2.0)   # w>1 → 1.0 로 클램프
    assert out["a"] == 1.0 and out["b"] == 0.0


def test_cascade_survivors_keeps_top_center_scores():
    cmap = {"a": 0.9, "b": 0.2, "c": 0.5, "d": 0.7}
    assert bm.cascade_survivors(cmap, 2) == ["a", "d"]
    assert bm.cascade_survivors(cmap, 99) == ["a", "d", "c", "b"]
    assert len(bm.cascade_survivors(cmap, 0)) == 1            # 최소 1


# ── 신규 레시피 등록 + 실제 채점 경로 배선용 필드 ──────────────────────────
def test_center_aware_recipes_registered_and_wired():
    g = {r.key: r for r in rx.group("center")}
    assert len(g) >= 6
    a = g["center_fusion_r25_w60"]
    assert a.region_fusion and not a.cascade
    assert a.center_ratio == 0.25 and a.center_weight == 0.6
    b = g["center_cascade_r25_k8"]
    assert b.cascade and not b.region_fusion and b.cascade_keep == 8
    # 베이스는 현행과 동일(정확도 비교 공정) — GPU 융합.
    assert a.recall == rx.RECALL_GPU and a.scoring == rx.SCORE_FUSION


def test_center_recipe_to_cfg_passes_center_ratio():
    cfg = rx.by_key("center_fusion_r20_w60").to_cfg()
    assert cfg.center_ratio == 0.20


# ── config: center_ratio 가 ROI 비율/캐시 키에 반영 ───────────────────────
def test_center_ratio_cache_key_and_ratio():
    c25 = replace(SimilarityConfig(center_crop=True), center_ratio=0.25)
    assert c25._center_crop_ratio() == 0.25
    assert c25.cache_extra("ref") == "c25"          # side 별 키 분리
    assert c25.cache_extra(None) == ""              # side 미지정 → crop 안 함
    legacy = SimilarityConfig(center_crop=True)      # ratio 0 → 레거시 0.3
    assert legacy._center_crop_ratio() == 0.3 and legacy.cache_extra("val") == "c30"


# ── 개발자 모드 테스트 목록 정리 — 린 기본 + faceoff + 아카이브 ──────────────
def test_quick_is_survivors_only_no_dead_ends():
    # center-aware 가 비효율로 입증된 뒤, 옵션은 '생존자'만 남겼다(center 는 quick 에서 제외).
    quick = [r.key for r in rx.select("quick")]
    assert len(quick) <= 12                         # 린(작게)
    # 추천 엔진이 production 대비 speedup 을 계산할 수 있게 현행/기준선 항상 포함.
    assert rx.PRODUCTION_SPEED_KEY in quick
    assert rx.BASELINE_ACCURACY_KEY in quick
    assert "rr_parallel" in quick                    # ×3.95 생존자
    # 입증된 사패(임베딩 장치 교체·center-aware)는 옵션/quick 에서 빠진다.
    assert "npu_mbnet_cpu_fuse" not in quick
    assert not any(k.startswith("center_") for k in quick)


def test_main_options_are_anchors_plus_survivors():
    main = [r.key for r in rx.select("main")]
    assert rx.BASELINE_ACCURACY_KEY in main and rx.PRODUCTION_SPEED_KEY in main
    assert set(rx.SURVIVOR_KEYS) <= set(main)
    # 사패는 메인 옵션에 없다(아카이브 all+ 로만).
    for dead in ("npu_mbnet_cpu_fuse", "gpu_fusion_topk20", "cpu_rr_phash",
                 "center_fusion_r25_w60", "rr_npu_phash_parallel"):
        assert dead not in main
        assert dead in set(rx.all_extended_keys())   # 기록은 보존(all+)


def test_faceoff_preset_resolves_and_is_skip_exempt():
    face = [r.key for r in rx.select("faceoff")]
    assert rx.PRODUCTION_SPEED_KEY in face and "cpu_rr_phash_orb" in face
    # faceoff 는 '개별 명시'로 취급돼 대상 장비에서 스킵되지 않아야 한다.
    exempt = rx.explicit_keys("faceoff")
    assert set(rx.FACEOFF_KEYS) <= exempt


def test_center_group_selectable_and_in_all_extended():
    cen = {r.key for r in rx.select("center")}
    assert "center_cascade_r25_k8_par" in cen
    allx = set(rx.all_extended_keys())
    assert cen <= allx                              # 아카이브/전체에 포함


# ── NPU 배치 정확도 진단 — openvino 미가용 환경에서 안전 동작 ────────────────
def test_npu_diag_returns_error_without_openvino():
    rep = bm.diagnose_npu_embedding([], max_images=4)
    assert "error" in rep                            # 빈 입력/미가용 → 에러 메시지(크래시 X)


def test_cosine_helper():
    assert abs(bm._cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert abs(bm._cosine([1, 0], [0, 1])) < 1e-9
    assert bm._cosine([0, 0], [1, 1]) == 0.0


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


def test_center_orb_recipes_registered_and_wired():
    g = {r.key: r for r in rx.group("orb-center")}
    assert set(rx.CENTER_ORB_KEYS) == set(g)
    for r in g.values():
        cfg = r.to_cfg()
        assert cfg.orb_center_weight > 0          # 중앙 가중 켜짐
        assert r.rerank_workers >= 2              # 병렬(단일 패스·생존자 속도)
        assert r.recall == rx.RECALL_GPU          # 현행과 동일 베이스
    # 옵션(MAIN)에 노출돼 측정 가능.
    assert set(rx.CENTER_ORB_KEYS) <= set(rx.MAIN_KEYS)


# ── 확정·고착: 운영 채점기는 이미 재채점을 병렬화한다(직렬로 회귀 금지) ─────────
def test_production_rerank_is_parallel():
    import pytest
    pytest.importorskip("PyQt6")
    from aoi_verification.app.workers import efficiency_matcher as em
    # 운영 FusionScheduler 는 ref 별 재채점을 코어 수만큼 풀로 돌린다(>=2).
    assert em._rerank_workers() >= 2
    # 회귀 가드: 스케줄러가 풀에 재채점을 submit 하는 코드가 유지돼야 한다.
    import inspect
    src = inspect.getsource(em.EfficiencyScheduler._consume_slot)
    assert "_pool.submit" in src and "_rerank_one" in src


# ── NPU 병렬 보조기 — N신호 z-융합(순수) + 레시피 배선 ──────────────────────
def test_fuse_zscore_signals_combines_and_ignores_bad():
    f = bm.fuse_zscore_signals([[0.9, 0.1, 0.5], [0.2, 0.8, 0.5], [0.7, 0.3, 0.5]])
    assert len(f) == 3 and abs(sum(f)) < 1e-9        # z-합산은 평균 0
    # 빈/길이불일치 신호는 무시(2신호와 동일).
    assert bm.fuse_zscore_signals([[1, 2, 3], []]) == bm.fuse_zscore_signals([[1, 2, 3]])
    assert bm.fuse_zscore_signals([]) == []
    # 한 신호에서 큰 값일수록 융합 점수도 큼(순위 보존).
    g = bm.fuse_zscore_signals([[3.0, 2.0, 1.0], [1.0, 2.0, 3.0]])
    assert abs(g[1]) < 1e-9 and g[0] == -g[2]        # 대칭(반대 신호) → 가운데 0


def test_npu_assist_recipes_registered_and_wired():
    g = {r.key: r for r in rx.group("npu-assist")}
    assert set(rx.NPU_ASSIST_KEYS) == set(g)
    for r in g.values():
        assert r.npu_defect_assist is True
        assert r.recall == rx.RECALL_GPU             # GPU 임베딩 recall 은 현행 그대로
        assert r.center_ratio > 0 and r.rerank_workers >= 2
    assert set(rx.NPU_ASSIST_KEYS) <= set(rx.MAIN_KEYS)   # 옵션 노출
