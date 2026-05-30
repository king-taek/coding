"""개발자 벤치마크 확장 — NPU 스윕·NPU 단독·고속 재채점·모델 주머니 테스트(헤드리스)."""

from __future__ import annotations

from aoi_verification.app.dev import benchmark as bm
from aoi_verification.app.dev import model_zoo as mz
from aoi_verification.app.dev import recipes as rx


# ---------------------------------------------------------------------------
# (A) NPU 사용 방식 스윕 — ≥20가지, 전부 NPU recall, 설정 유효
# ---------------------------------------------------------------------------
def test_npu_sweep_at_least_20_and_npu_recall():
    assert len(rx.NPU_SWEEP) >= 20
    for r in rx.NPU_SWEEP:
        assert r.recall == rx.RECALL_NPU
        assert r.tag == "npu_sweep"
        r.to_cfg()                       # 설정 생성이 예외 없이 동작


def test_npu_sweep_covers_all_knob_axes():
    keys = {r.key for r in rx.NPU_SWEEP}
    # 배치/동시추론/힌트/스트림/전처리스레드/해상도/모델 축이 모두 존재.
    assert any(k.startswith("npu_b") for k in keys)
    assert any(k.startswith("npu_c") for k in keys)
    assert any(k.startswith("npu_hint_") for k in keys)
    assert any(k.startswith("npu_streams") for k in keys)
    assert any(k.startswith("npu_prep") for k in keys)
    assert any(k.startswith("npu_px") for k in keys)
    assert any(k.startswith("npu_model_") for k in keys)
    # 노브가 실제로 서로 다른 값을 갖는지(예: 힌트 3종).
    hints = {r.perf_hint for r in rx.NPU_SWEEP}
    assert {"THROUGHPUT", "LATENCY", "CUMULATIVE_THROUGHPUT"} <= hints


# ---------------------------------------------------------------------------
# (C) NPU 단독 채점 — CPU 재채점 없이 임베딩만
# ---------------------------------------------------------------------------
def test_npu_only_group_uses_npu_without_cpu_rerank():
    assert rx.NPU_ONLY
    embed_only = [r for r in rx.NPU_ONLY if r.scoring == rx.SCORE_EMBED_ONLY]
    assert embed_only                       # 순수 NPU 단독(코사인만)이 존재
    for r in embed_only:
        assert r.recall == rx.RECALL_NPU
        cfg = r.to_cfg()
        assert cfg.use_npu and not cfg.use_gpu   # NPU 만 사용


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


# ---------------------------------------------------------------------------
# (B) 모델 주머니 — 레지스트리/가용성/레시피
# ---------------------------------------------------------------------------
def test_model_zoo_registry_has_requested_models():
    for m in ("mobilevit_s", "cae", "unet", "attention_unet",
              "superpoint_lightglue", "patchcore", "padim"):
        sp = mz.spec(m)
        assert sp is not None and sp.desc


def test_model_zoo_availability_reasons_without_deps():
    # torch/timm/kornia/anomalib 미설치 환경 → (False, 사유) 로 친절히 보고.
    ok, reason = mz.availability("mobilevit_s")
    assert ok is False and reason
    ok2, _ = mz.availability("patchcore")
    assert ok2 is False
    assert mz.availability("does_not_exist")[0] is False


def test_model_zoo_recipes_carry_needs_and_method():
    keys = {r.key for r in rx.MODEL_ZOO}
    assert "zoo_superpoint_lightglue" in keys
    assert "zoo_patchcore" in keys
    for r in rx.MODEL_ZOO:
        assert r.tag == "model_zoo"
        r.to_cfg()                          # 예외 없이 설정 생성


# ---------------------------------------------------------------------------
# select() 그룹 — core / npu-sweep / npu-only / fast-rerank / model-zoo / all+
# ---------------------------------------------------------------------------
def test_select_groups_and_all_extended():
    assert len(rx.select("all")) == len(rx.REGISTRY)
    assert len(rx.select("npu-sweep")) == len(rx.NPU_SWEEP)
    assert len(rx.select("all+")) == len(rx.ALL_EXTENDED)
    mixed = rx.select("npu-only,fast-rerank")
    assert len(mixed) == len(rx.NPU_ONLY) + len(rx.FAST_RERANK)
    # 개별 키 + 그룹 혼합도 중복 없이.
    one = rx.select("gpu_fusion_b16,npu-only")
    keys = [r.key for r in one]
    assert len(keys) == len(set(keys))


def test_all_extended_keys_unique():
    keys = [r.key for r in rx.ALL_EXTENDED]
    assert len(keys) == len(set(keys))
    assert len(rx.ALL_EXTENDED) >= 50
