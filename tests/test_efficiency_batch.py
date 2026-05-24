"""고효율 모드 throughput 최적화 — 묶음(batch) 처리 정확성 검증.

ref 를 묶음으로 동시 임베딩해도 per-ref 단건 처리와 **결과가 동일**해야 한다
(동시성만 바뀌고 계산은 불변).  임베딩은 path 기반 결정적 벡터로 mock 하고
실제 ANN 인덱스(brute-force)로 랭킹을 검증한다 — openvino/NPU 불필요.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers import efficiency_matcher as eff


_VECS = {
    "v0.png": [1.0, 0.0, 0.0, 0.0],
    "v1.png": [0.0, 1.0, 0.0, 0.0],
    "v2.png": [1.0, 1.0, 0.0, 0.0],
    "v3.png": [0.0, 0.0, 1.0, 0.0],
    "r0.png": [1.0, 0.2, 0.0, 0.0],
    "r1.png": [0.0, 1.0, 0.1, 0.0],
    "r2.png": [0.5, 0.5, 0.0, 0.0],
}


def _vec(p) -> np.ndarray:
    return np.array(_VECS[Path(p).name], dtype=np.float32)


def _embed_units():
    unit = eff._EmbedUnit("npu", eff._ov.MODEL_RESNET18, "NPU", None, 0.0, jobs=8)
    # _embed 를 결정적 벡터로 대체 (openvino 추론 회피).
    unit._embed = lambda paths: {Path(p): _vec(p) for p in paths}
    return unit


def _cands_repr(cands):
    return [(str(c.item.path), round(float(c.score), 6)) for c in cands]


def test_match_batch_equals_per_ref():
    vals = [ImageItem(slot="S", path=Path(f"v{i}.png"), side="val") for i in range(4)]
    refs = [ImageItem(slot="S", path=Path(f"r{i}.png"), side="ref") for i in range(3)]

    unit = _embed_units()
    batched = unit.match_batch(refs, vals)               # 묶음 동시 처리

    # 같은 슬롯 인덱스를 재사용해 ref 별 단건 처리 — 결과가 동일해야 함.
    for r in refs:
        single = unit.match_batch([r], vals)
        assert _cands_repr(batched[Path(r.path)]) == _cands_repr(single[Path(r.path)])

    # 각 ref 결과는 점수 내림차순 정렬.
    for r in refs:
        scores = [c.score for c in batched[Path(r.path)]]
        assert scores == sorted(scores, reverse=True)


def test_match_delegates_to_batch():
    vals = [ImageItem(slot="S", path=Path(f"v{i}.png"), side="val") for i in range(4)]
    ref = ImageItem(slot="S", path=Path("r0.png"), side="ref")
    unit = _embed_units()
    via_single = unit.match(ref, vals)
    via_batch = unit.match_batch([ref], vals)[Path(ref.path)]
    assert _cands_repr(via_single) == _cands_repr(via_batch)


def test_accel_concurrency_reads_cfg_with_default():
    assert eff.accel_concurrency(None) == eff.DEFAULT_ACCEL_CONCURRENCY

    class Cfg:
        accel_concurrency = 64

    assert eff.accel_concurrency(Cfg()) == 64

    class Bad:
        accel_concurrency = "oops"

    assert eff.accel_concurrency(Bad()) == eff.DEFAULT_ACCEL_CONCURRENCY

    class Zero:
        accel_concurrency = 0

    assert eff.accel_concurrency(Zero()) == 1        # 최소 1 보장
