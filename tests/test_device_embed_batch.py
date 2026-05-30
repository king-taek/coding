"""정적 배치 B 추론 + 장치 토글 회귀 테스트.

- `device_embed(batch=B)` 가 batch=1 과 **path별 동일한 임베딩**을 돌려주는지
  (배치는 throughput 용 — 결과 불변).  OpenVINO 없이 컴파일/추론을 mock.
- `build_units` 가 use_cpu/use_gpu/use_npu 토글을 따르고, 전부 끄면 CPU 폴백.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from aoi_verification.app.learning import embedder_openvino as ov
from aoi_verification.app.workers import efficiency_matcher as eff


# ---------------------------------------------------------------------------
# device_embed: batch>1 == batch=1 (path별 동일)
# ---------------------------------------------------------------------------
def _code(p) -> float:
    return float(int(Path(p).stem[1:]))      # x0->0, x1->1, ...


def _fake_make_input(p, cfg, side=None, px=None):
    return np.full((3, 2, 2), _code(p), dtype=np.float32)


def _fake_infer(compiled, inputs, n_streams, progress_cb=None):
    # inputs: [(userdata, (B,3,2,2)), ...].  행별 임베딩 = [mean, 1.0].
    raw = {}
    for userdata, x in inputs:
        b = x.shape[0]
        out = np.zeros((b, 2), dtype=np.float32)
        for i in range(b):
            out[i] = [float(x[i].mean()), 1.0]
        raw[userdata] = out
        if progress_cb is not None:
            progress_cb(len(userdata) if isinstance(userdata, tuple) else 1)
    return raw


def test_progress_cb_counts_all_images(_mock_ov):
    paths = [Path(f"x{i}.png") for i in range(5)]
    seen = {"n": 0}
    ov.device_embed(paths, model_kind=ov.MODEL_RESNET18, device="NPU",
                    jobs=4, batch=1, progress_cb=lambda n: seen.__setitem__("n", seen["n"] + n))
    assert seen["n"] == 5            # 모든 사진이 진행률에 반영(per-image)


@pytest.fixture
def _mock_ov(monkeypatch):
    monkeypatch.setattr(ov, "compile_model_on",
                        lambda mk, dev, batch=1, **kw: (object(), "dev"))
    monkeypatch.setattr(ov, "_make_input_array", _fake_make_input)
    monkeypatch.setattr(ov, "_infer_raw", _fake_infer)
    monkeypatch.setattr(ov, "mark_unit_active", lambda dev: None)
    yield


def _embed(paths, batch):
    return ov.device_embed(paths, model_kind=ov.MODEL_RESNET18, device="NPU",
                           jobs=4, batch=batch)


def test_batch_matches_single(_mock_ov):
    paths = [Path(f"x{i}.png") for i in range(7)]   # 7 → batch 3 이면 3 그룹(마지막 1장)
    single = _embed(paths, 1)
    batched = _embed(paths, 3)
    assert set(single) == set(batched) == set(paths)
    for p in paths:
        assert np.allclose(single[p], batched[p]), p
    # 정규화 확인(L2=1).
    for p in paths:
        assert abs(float(np.linalg.norm(batched[p])) - 1.0) < 1e-5


def test_batch_padding_ignored(_mock_ov):
    # 4장 + batch 3 → 마지막 그룹은 1장(+2 패딩) — 패딩이 결과에 안 섞여야.
    paths = [Path(f"x{i}.png") for i in range(4)]
    out = _embed(paths, 3)
    assert len(out) == 4
    # x3 의 임베딩은 자기 code 기반이어야(패딩 0 과 섞이면 달라짐).
    expected = _fake_infer(None, [((Path("x3.png"),),
                                   np.full((1, 3, 2, 2), 3.0, np.float32))],
                           1)[(Path("x3.png"),)][0]
    expected = expected / (np.linalg.norm(expected) + 1e-9)
    assert np.allclose(out[Path("x3.png")], expected.astype(np.float32))


# ---------------------------------------------------------------------------
# build_units 장치 토글
# ---------------------------------------------------------------------------
class _Cfg:
    def __init__(self, **kw):
        self.use_cpu = kw.get("use_cpu", True)
        self.use_gpu = kw.get("use_gpu", True)
        self.use_npu = kw.get("use_npu", True)
        self.accel_concurrency = 16
        self.embed_batch = kw.get("embed_batch", 1)


def _tags(units):
    return [getattr(u, "tag", "?") for u in units]


def test_build_units_cpu_gpu(monkeypatch):
    # build_units(보존용): CPU 항상 + GPU(use_gpu 기본 True). NPU 는 use_npu=True 일 때만.
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU", "NPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on",
                        lambda mk, dev, batch=1, **kw: (object(), "dev"))
    units = eff.build_units(_Cfg(use_npu=False), 0.5)
    assert _tags(units) == ["cpu", "gpu"]               # 기본 = CPU+GPU
    units = eff.build_units(_Cfg(use_npu=True), 0.5)
    assert _tags(units) == ["cpu", "gpu", "npu"]        # NPU 재활성 경로 보존
