"""개발자 벤치마크 레시피 레지스트리 단위 테스트 (Qt/torch 불필요)."""

from __future__ import annotations

import pytest

from aoi_verification.app.dev import recipes as rx


def test_registry_has_at_least_ten_unique_recipes():
    assert len(rx.REGISTRY) >= 10
    keys = [r.key for r in rx.REGISTRY]
    assert len(keys) == len(set(keys)), "레시피 키 중복"


def test_every_recipe_documents_its_operations():
    for r in rx.REGISTRY:
        assert r.desc and len(r.desc) > 10, f"{r.key} 설명 누락"
        assert r.recall in (rx.RECALL_NONE, rx.RECALL_CPU, rx.RECALL_GPU,
                            rx.RECALL_NPU, rx.RECALL_GPU_NPU)
        assert r.scoring in (rx.SCORE_CLASSICAL, rx.SCORE_EMBED_ONLY,
                             rx.SCORE_FUSION)


def test_baseline_and_production_keys_present():
    keys = rx.all_keys()
    assert rx.BASELINE_ACCURACY_KEY in keys
    assert rx.PRODUCTION_SPEED_KEY in keys


def test_user_npu_extract_cpu_compute_recipe_exists():
    """'NPU 로 데이터 뽑고 CPU 로 계산' 사용자 아이디어가 레시피에 있어야 한다."""
    r = rx.by_key("npu_extract_cpu_fuse")
    assert r.recall == rx.RECALL_NPU
    assert r.scoring == rx.SCORE_FUSION
    assert r.required_devices() == {"NPU"}


def test_device_combos_covered():
    recalls = {r.recall for r in rx.REGISTRY}
    # CPU·GPU·NPU 단독 + GPU+NPU 조합 + 임베딩 없음(전수) 모두 다룬다.
    assert {rx.RECALL_NONE, rx.RECALL_CPU, rx.RECALL_GPU, rx.RECALL_NPU,
            rx.RECALL_GPU_NPU} <= recalls


def test_split_recipe_requires_both_devices():
    r = rx.by_key("gpu_npu_split_fusion")
    assert r.required_devices() == {"GPU", "NPU"}
    assert r.ensemble is False


def test_to_cfg_always_bypasses_cache_and_sets_engine():
    for r in rx.REGISTRY:
        cfg = r.to_cfg()
        assert cfg.bench_no_cache is True
        assert cfg.persist_scores is False
        if r.scoring == rx.SCORE_CLASSICAL:
            assert cfg.engine == "basic"
        else:
            assert cfg.engine == "efficiency"
        # 장치 토글이 recall 과 일치
        assert cfg.use_gpu == (r.recall in (rx.RECALL_GPU, rx.RECALL_GPU_NPU))
        assert cfg.use_npu == (r.recall in (rx.RECALL_NPU, rx.RECALL_GPU_NPU))


def test_select_and_by_key():
    assert len(rx.select("all")) == len(rx.REGISTRY)
    two = rx.select("cpu_classical_full,gpu_fusion_b16")
    assert [r.key for r in two] == ["cpu_classical_full", "gpu_fusion_b16"]
    with pytest.raises(KeyError):
        rx.by_key("does_not_exist")
