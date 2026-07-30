"""고효율 모드 운영 계약 — 개발자 벤치마크를 걷어낸 뒤에도 매칭이 그대로인지.

벤치마크는 ``device_embed``/``compile_model_on`` 에 스윕용 인자(perf_hint·streams·
preprocess_threads·input_px)와 캐시 우회 스위치(bench_no_cache)를 달고 있었다.
기능을 지우면서 그 인자들도 함께 지웠는데, 여기는 **매칭 hot path** 라 실수하면
사용자 결과가 조용히 달라진다.  그래서 '운영이 고르는 값' 을 못 박아 둔다.

무거운 의존성 없이 돌도록 순수 로직만 본다(openvino 미설치 환경 포함).
"""

from __future__ import annotations

import inspect

from aoi_verification.app.learning import embedder_openvino as ov


def test_operational_backend_is_mobilenet_gpu_batch16():
    """운영이 쓰는 (모델·장치·배치) 조합 — 바뀌면 매칭 속도/결과가 달라진다."""
    from aoi_verification.app.workers import efficiency_matcher as eff

    assert ov.MODEL_MOBILENET_V3 == "mobilenet_v3_small"
    assert eff.GPU_BATCH == 16

    picked = {}

    class _Cfg:
        use_gpu = True

    def fake_compile(kind, device, batch=1):
        picked.update(kind=kind, device=device, batch=batch)
        return ("compiled", "Intel GPU")

    orig_units, orig_compile = ov.available_units, ov.compile_model_on
    ov.available_units = lambda: ["GPU"]
    ov.compile_model_on = fake_compile
    try:
        assert eff._select_backend(_Cfg()) == (ov.MODEL_MOBILENET_V3, "GPU", 16)
    finally:
        ov.available_units, ov.compile_model_on = orig_units, orig_compile
    assert picked == {"kind": "mobilenet_v3_small", "device": "GPU", "batch": 16}


def test_no_gpu_falls_back_instead_of_failing():
    """GPU 가 없거나 IR 이 없으면 None → CPU 고전 단독.  앱이 죽으면 안 된다."""
    from aoi_verification.app.workers import efficiency_matcher as eff

    class _Cfg:
        use_gpu = True

    orig = ov.available_units
    ov.available_units = lambda: []
    try:
        assert eff._select_backend(_Cfg()) is None
    finally:
        ov.available_units = orig


def test_embedding_cache_signature_is_unchanged():
    """★ 캐시 키가 바뀌면 기존 ``.npy`` 가 통째로 무효화된다(사용자 PC 가 재추출).

    벤치 노브를 지우면서 시그니처를 건드리지 않았음을 고정한다."""
    assert ov._emb_signature("mobilenet_v3_small", None, "ref") == \
        f"mobilenet_v3_small|px{ov._INPUT_PX}|cc0|{ov._EMB_VERSION}"
    assert ov._INPUT_PX == 256


def test_benchmark_only_knobs_are_gone():
    """벤치 스윕 인자가 남아 있으면 죽은 분기가 hot path 에 남는다."""
    embed = inspect.signature(ov.device_embed).parameters
    assert set(embed) == {"paths", "model_kind", "device", "cfg", "jobs",
                          "batch", "side", "progress_cb"}
    compile_params = inspect.signature(ov.compile_model_on.__wrapped__).parameters
    assert set(compile_params) == {"model_kind", "device", "batch"}

    from aoi_verification.app.config import SimilarityConfig
    assert not hasattr(SimilarityConfig(), "bench_no_cache")
    # 운영이 실제로 쓰는 노브는 남아 있어야 한다(사용자 설정 → 매칭).
    for keep in ("accel_concurrency", "use_gpu", "embed_batch", "persist_scores",
                 "rerank_components", "orb_center_weight"):
        assert hasattr(SimilarityConfig(), keep), keep


def test_the_app_no_longer_ships_a_benchmark():
    """개발자 벤치마크는 기능째로 제거됐다 — 되살아나면 배포본이 다시 무거워진다."""
    import importlib
    for gone in ("aoi_verification.app.dev.benchmark",
                 "aoi_verification.app.dev.recipes",
                 "aoi_verification.app.ui.widgets.dev_benchmark_dialog",
                 "aoi_verification.app.ui.widgets.label_maker_dialog"):
        try:
            importlib.import_module(gone)
        except ImportError:
            continue
        raise AssertionError(f"{gone} 이 아직 살아 있다")
