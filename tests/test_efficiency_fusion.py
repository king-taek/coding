"""고효율 fusion-zscore 엔진 — 순수 함수 + 슬롯 순차 파이프라인 헤드리스 테스트."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("cv2")
from PyQt6.QtWidgets import QApplication

from aoi_verification.app import config
from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers import efficiency_matcher as eff


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
def test_zfuse():
    f = eff.zfuse([0.9, 0.5, 0.1], [0.8, 0.4, 0.0])      # 같은 순위 → 강화
    assert f[0] > f[1] > f[2]
    assert eff.zfuse([0.5, 0.5], [0.5, 0.5]) == [0.0, 0.0]   # std 0 → z 무시
    assert eff.zfuse([0.7], [0.3]) == [0.0]                  # 길이<2 → 0


def test_map_score():
    m = eff.map_score([3.0, 1.0, -1.0])
    assert abs(m[0] - 0.98) < 1e-9 and abs(m[-1] - 0.80) < 1e-9   # 밴드 [0.80,0.98]
    assert m[0] > m[1] > m[2]                                     # 단조
    assert eff.map_score([2.0, 2.0]) == [0.98, 0.98]             # span 0
    assert eff.map_score([]) == []


def test_select_backend_cpu_gpu_only(monkeypatch):
    monkeypatch.setattr(eff._ov, "available_units", lambda: [])
    assert eff._select_backend(None) is None                     # GPU 없음 → CPU 폴백
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU", "NPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on", lambda mk, dev, batch=1: object())
    b = eff._select_backend(None)
    assert b is not None and b[1] == "GPU" and b[2] == eff.GPU_BATCH   # NPU 무시·GPU 선택·batch16
    monkeypatch.setattr(eff._ov, "compile_model_on", lambda mk, dev, batch=1: None)
    assert eff._select_backend(None) is None                     # 컴파일 실패 → CPU 폴백


# ---------------------------------------------------------------------------
def _mk(path, seed):
    import cv2
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), (rng.random((40, 40, 3)) * 255).astype(np.uint8))


def _fake_embed(paths, **kw):
    out = {}
    for p in paths:
        h = abs(hash(Path(p).name)) % 9973
        v = np.random.default_rng(h).normal(size=16).astype(np.float32)
        out[Path(p)] = v / (np.linalg.norm(v) + 1e-9)
    return out


def test_fusion_run_fills_results(qapp, tmp_path, monkeypatch):
    refs = [ImageItem(slot="S1", path=tmp_path / f"r{i}.jpg", side="ref") for i in range(2)]
    vals = [ImageItem(slot="S1", path=tmp_path / f"v{i}.jpg", side="val") for i in range(5)]
    for it in refs + vals:
        _mk(it.path, abs(hash(it.path.name)) % 9000)
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on", lambda mk, dev, batch=1: object())
    monkeypatch.setattr(eff._ov, "device_embed", _fake_embed)

    results = {}; prog = []; fin = []
    sched = eff.EfficiencyScheduler(
        [("S1", refs, vals)], cfg=config.SimilarityConfig(engine="efficiency"),
        threshold=0.2, results=results)
    sched.signals.progress.connect(lambda d, t: prog.append((d, t)))
    sched.signals.finished.connect(lambda: fin.append(1))
    sched._run()

    assert fin == [1] and prog                       # finished + progress emit
    assert len(results) == 2
    for r in refs:
        cands = results[("S1", Path(r.path))]
        assert cands and len(cands) <= 5
        scores = [s for _, s in cands]
        assert scores == sorted(scores, reverse=True)            # 내림차순
        assert 0.80 - 1e-6 <= scores[0] <= 0.98 + 1e-6           # fusion 상위 밴드


def test_fusion_run_classical_fallback_no_gpu(qapp, tmp_path, monkeypatch):
    refs = [ImageItem(slot="S1", path=tmp_path / "rr0.jpg", side="ref")]
    vals = [ImageItem(slot="S1", path=tmp_path / f"vv{i}.jpg", side="val") for i in range(3)]
    for it in refs + vals:
        _mk(it.path, abs(hash(it.path.name)) % 9000)
    monkeypatch.setattr(eff._ov, "available_units", lambda: [])   # GPU 없음 → 고전 폴백
    results = {}; fin = []
    sched = eff.EfficiencyScheduler(
        [("S1", refs, vals)], cfg=config.SimilarityConfig(engine="efficiency"),
        threshold=0.0, results=results)
    sched.signals.finished.connect(lambda: fin.append(1))
    sched._run()
    assert fin == [1]
    assert ("S1", Path(refs[0].path)) in results                 # 고전 폴백으로 채워짐
