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
def test_quick_is_lean_and_includes_production_and_center():
    quick = [r.key for r in rx.select("quick")]
    assert len(quick) <= 12                         # 린(작게)
    # 추천 엔진이 production 대비 speedup 을 계산할 수 있게 현행/기준선 항상 포함.
    assert rx.PRODUCTION_SPEED_KEY in quick
    assert rx.BASELINE_ACCURACY_KEY in quick
    # 사용자 제안(중앙-인식)과 생존자가 들어있다.
    assert any(k.startswith("center_") for k in quick)
    assert "rr_parallel" in quick
    # 입증된 사패(임베딩 장치 교체)는 린 기본에서 빠진다.
    assert "npu_mbnet_cpu_fuse" not in quick


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
