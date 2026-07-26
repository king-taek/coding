"""개발자 벤치마크 확장 — 고속 재채점·중앙인식 그룹 테스트(헤드리스)."""

from __future__ import annotations

from aoi_verification.app.dev import benchmark as bm
from aoi_verification.app.dev import recipes as rx


# ---------------------------------------------------------------------------
# (D) CPU 재채점 고속화 — 컴포넌트 매핑/병렬 워커
# ---------------------------------------------------------------------------
def test_fast_rerank_components_mapping():
    by = {r.key: r for r in rx.FAST_RERANK}
    assert bm._rerank_components(by["rr_phash"]) == {"phash"}
    assert bm._rerank_components(by["rr_phash_ssim"]) == {"phash", "ssim"}
    assert bm._rerank_components(by["rr_orb_ssim"]) == {"orb", "ssim"}
    assert bm._rerank_components(by["rr_parallel"]) is None      # 전체(정확도 동일)
    assert by["rr_parallel"].rerank_workers >= 2                 # 병렬


def test_fast_rerank_recipes_are_fusion():
    for r in rx.FAST_RERANK:
        assert r.scoring == rx.SCORE_FUSION
        assert r.tag == "fast_rerank"


def test_new_rerank_component_modes_mapping():
    # SSIM 제거(pHash+ORB)·ORB 단독·SSIM 단독 모드가 올바로 매핑된다.
    assert bm._rerank_components(rx.by_key("cpu_rr_phash_orb")) == {"phash", "orb"}
    assert bm._rerank_components(rx.by_key("cpu_rr_orb_only")) == {"orb"}
    assert bm._rerank_components(rx.by_key("cpu_rr_ssim_only")) == {"ssim"}


def test_cpu_rerank_group_has_at_least_ten_speedup_methods():
    """CPU 매치 단계 고속화 — 최소 10가지 방법을 테스트할 수 있어야 한다."""
    cpu = [r for r in rx.FAST_RERANK if r.key.startswith("cpu_rr_")]
    assert len(cpu) >= 10, f"CPU 재채점 고속화 레시피가 10개 미만: {len(cpu)}"
    for r in cpu:
        assert r.recall == rx.RECALL_CPU      # 끝까지 CPU(매치 단계 전부 CPU)
        assert r.scoring == rx.SCORE_FUSION and r.tag == "fast_rerank"
        r.to_cfg()                            # 설정 생성 예외 없음


def test_cpu_rerank_covers_distinct_speedup_levers():
    cpu = {r.key: r for r in rx.FAST_RERANK if r.key.startswith("cpu_rr_")}
    # (1) 항 빼기: pHash 단독 / ORB 제거 / SSIM 제거 모드가 모두 존재
    assert bm._rerank_components(cpu["cpu_rr_phash"]) == {"phash"}
    assert bm._rerank_components(cpu["cpu_rr_phash_ssim"]) == {"phash", "ssim"}
    assert bm._rerank_components(cpu["cpu_rr_phash_orb"]) == {"phash", "orb"}
    # (2) 병렬화: 서로 다른 워커 수(8/16/32)가 존재
    workers = {r.rerank_workers for r in cpu.values() if r.rerank == "classical"}
    assert {8, 16, 32} <= workers
    # (3) ORB 특징 수 줄이기: 0 이 아닌 값(예: 256/128)이 존재
    assert any(r.orb_nfeatures in (128, 256) for r in cpu.values())
    # (4) 재채점 깊이 줄이기: topk 10/20 존재
    assert {r.fusion_topk for r in cpu.values()} >= {10, 20}
    # (5) 중앙 crop 변형 존재
    assert any(r.center_crop for r in cpu.values())


def test_orb_nfeatures_flows_into_cfg():
    assert rx.by_key("cpu_rr_orb256").to_cfg().orb_nfeatures == 256
    assert rx.by_key("cpu_rr_orb128").to_cfg().orb_nfeatures == 128
    assert rx.by_key("cpu_rr_phash").to_cfg().orb_nfeatures == 0    # 기본은 0


# ---------------------------------------------------------------------------
# select() 그룹 — core / center / orb-center / fast-rerank / all+
# ---------------------------------------------------------------------------
def test_select_groups_and_all_extended():
    assert len(rx.select("all")) == len(rx.REGISTRY)
    assert len(rx.select("fast-rerank")) == len(rx.FAST_RERANK)
    assert len(rx.select("all+")) == len(rx.ALL_EXTENDED)
    mixed = rx.select("center,fast-rerank")
    assert len(mixed) == len(rx.CENTER_AWARE) + len(rx.FAST_RERANK)
    # 개별 키 + 그룹 혼합도 중복 없이.
    one = rx.select("gpu_fusion_b16,fast-rerank")
    keys = [r.key for r in one]
    assert len(keys) == len(set(keys))


def test_all_extended_keys_unique():
    keys = [r.key for r in rx.ALL_EXTENDED]
    assert len(keys) == len(set(keys))
    # NPU 그룹을 걷어낸 뒤의 확장 레지스트리 규모.
    assert len(rx.ALL_EXTENDED) >= 40
