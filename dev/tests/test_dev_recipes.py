"""개발자 벤치마크 레시피 레지스트리 단위 테스트 (Qt/torch 불필요)."""

from __future__ import annotations

import pytest

from aoi_verification.app.dev import recipes as rx


def test_registry_has_enough_unique_recipes():
    # NPU 지원을 걷어낸 뒤 코어 레지스트리는 CPU/GPU 조합 8종 이상.
    assert len(rx.REGISTRY) >= 8
    keys = [r.key for r in rx.REGISTRY]
    assert len(keys) == len(set(keys)), "레시피 키 중복"


def test_every_recipe_documents_its_operations():
    for r in rx.REGISTRY:
        assert r.desc and len(r.desc) > 10, f"{r.key} 설명 누락"
        assert r.recall in (rx.RECALL_NONE, rx.RECALL_CPU, rx.RECALL_GPU)
        assert r.scoring in (rx.SCORE_CLASSICAL, rx.SCORE_EMBED_ONLY,
                             rx.SCORE_FUSION)


def test_baseline_and_production_keys_present():
    keys = rx.all_keys()
    assert rx.BASELINE_ACCURACY_KEY in keys
    assert rx.PRODUCTION_SPEED_KEY in keys


def test_quick_preset_is_small_and_valid():
    # '빠른(린)' 프리셋 — 항목이 적고(<=12), 모든 키가 실존하며, 기준선/현행을 포함한다.
    assert 0 < len(rx.QUICK_KEYS) <= 12
    for k in rx.QUICK_KEYS:
        rx.by_key(k)                              # KeyError 면 실패
    assert rx.BASELINE_ACCURACY_KEY in rx.QUICK_KEYS
    assert rx.PRODUCTION_SPEED_KEY in rx.QUICK_KEYS
    # 재채점 생존자(rr_/cpu_rr_)가 들어 있어야 한다.
    assert any(k.startswith(("rr_", "cpu_rr_")) for k in rx.QUICK_KEYS)
    # quick 은 메인 옵션(앵커+생존자)의 부분집합이다.
    assert set(rx.QUICK_KEYS) <= set(rx.MAIN_KEYS)


def test_select_quick_returns_quick_keys_in_order():
    assert [r.key for r in rx.select("quick")] == list(rx.QUICK_KEYS)


def test_explicit_keys_expands_quick_for_skip_exemption():
    # 'quick' 으로 펼쳐도 개별 명시로 취급돼 스킵이 면제된다(대상 장비에서 측정 보장).
    assert rx.explicit_keys("quick") == set(rx.QUICK_KEYS)


def test_device_combos_covered():
    recalls = {r.recall for r in rx.REGISTRY}
    # CPU·GPU 단독 + 임베딩 없음(전수) 모두 다룬다.
    assert {rx.RECALL_NONE, rx.RECALL_CPU, rx.RECALL_GPU} <= recalls


def test_gpu_recipe_requires_gpu():
    r = rx.by_key("gpu_fusion_b16")
    assert r.required_devices() == {"GPU"}


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
        assert cfg.use_gpu == (r.recall == rx.RECALL_GPU)


def test_select_and_by_key():
    assert len(rx.select("all")) == len(rx.REGISTRY)
    two = rx.select("cpu_classical_full,gpu_fusion_b16")
    assert [r.key for r in two] == ["cpu_classical_full", "gpu_fusion_b16"]
    with pytest.raises(KeyError):
        rx.by_key("does_not_exist")
