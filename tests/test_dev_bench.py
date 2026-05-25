"""개발자 벤치마크 — 순수 변형 함수 + 워커(가짜 임베딩 장치) 헤드리스 테스트."""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("cv2")
from PyQt6.QtWidgets import QApplication

from aoi_verification.app.dev import bench
from aoi_verification.app.models.slot import ImageItem


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
def test_whiten_improves_discrimination():
    """공통성분이 지배적인 임베딩에서 평균 제거가 정답 순위를 끌어올린다."""
    rng = np.random.default_rng(0)
    D = 32
    common = rng.normal(size=D) * 5.0           # 모든 후보 공유(=반복 텍스처)
    disc = rng.normal(size=(6, D)) * 0.4         # 후보별 미세 변별 성분
    val = common[None, :] + disc
    val = val / np.linalg.norm(val, axis=1, keepdims=True)
    # ref = 3번 후보 + 공통 + 노이즈
    ref = common + disc[3] + rng.normal(size=D) * 0.05
    ref = ref / np.linalg.norm(ref)

    raw_order, _ = bench.cosine_order(ref, bench._l2n(val))
    mu, comps = bench.whiten_fit(val, n_pc=0)
    val_w = bench.whiten_apply(val, mu, comps)
    ref_w = bench.whiten_apply(ref[None, :], mu, comps)[0]
    wh_order, _ = bench.cosine_order(ref_w, val_w)

    assert wh_order[0] == 3                       # 화이트닝 후 정답이 1위
    # 화이트닝이 raw보다 정답 순위를 같거나 더 좋게
    assert list(wh_order).index(3) <= list(raw_order).index(3)


def test_rerank_topk_classical_reorders_head():
    order = np.array([0, 1, 2, 3, 4])
    names = ["a", "b", "c", "d", "e"]
    # 고전 점수: c 최고 → top-3 안에서 c가 1위로
    cs = {"a": 0.1, "b": 0.2, "c": 0.9, "d": 0.0, "e": 0.0}
    out = bench.rerank_topk_classical(order, names, 3, lambda n: cs[n])
    assert names[out[0]] == "c"
    assert out[3:] == [3, 4]                       # 꼬리는 임베딩 순서 유지


# ---------------------------------------------------------------------------
def _mk_img(path: Path, seed: int):
    import cv2
    rng = np.random.default_rng(seed)
    img = (rng.random((48, 48, 3)) * 255).astype(np.uint8)
    cv2.imwrite(str(path), img)


def test_worker_runs_all_variants(qapp, tmp_path, monkeypatch):
    # 가짜 이미지 (classical 경로가 cv2 로 실제 처리)
    refs, vals = [], []
    for i in range(3):
        p = tmp_path / f"ref{i}.jpg"; _mk_img(p, i)
        refs.append(ImageItem(slot="S1", path=p, side="ref"))
    for i in range(5):
        p = tmp_path / f"val{i}.jpg"; _mk_img(p, 100 + i)
        vals.append(ImageItem(slot="S1", path=p, side="val"))
    tasks = [("S1", refs, vals)]

    # 가짜 임베딩 장치
    from aoi_verification.app.learning import embedder_openvino as _ov
    monkeypatch.setattr(_ov, "available_units", lambda: ["GPU", "NPU"])

    def fake_embed(paths, **kw):
        out = {}
        for p in paths:
            h = abs(hash(Path(p).name)) % 9973
            v = np.random.default_rng(h).normal(size=16).astype(np.float32)
            out[Path(p)] = v / (np.linalg.norm(v) + 1e-9)
        return out
    monkeypatch.setattr(_ov, "device_embed", fake_embed)

    from aoi_verification.app.utils import paths as _paths
    monkeypatch.setattr(_paths, "results_dir", lambda: tmp_path)

    from aoi_verification.app import config
    cfg = config.SimilarityConfig(engine="efficiency", center_crop=False)
    w = bench.BenchmarkWorker(tasks, cfg=cfg, threshold=0.2,
                              use_gpu=True, use_npu=True, session_id="t1")
    w.run()                                   # 동기 실행

    out = tmp_path / "레퍼런스" / "dev_benchmark_t1.jsonl"
    assert out.exists()
    recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    runs = {(r["device"], r["variant"]) for r in recs if r.get("type") == "run"}
    # classical(cpu) + gpu·npu × 6 변형
    assert ("cpu", "classical") in runs
    for dev in ("gpu", "npu"):
        for v in bench.EMBED_VARIANTS:
            assert (dev, v) in runs, f"missing {dev}/{v}"
    # 결과 라인 구조
    res = [r for r in recs if r.get("type") == "result"]
    assert res and all("topk" in r and "ref_filename" in r for r in res)
    # 각 run 에 타이밍 필드
    for r in recs:
        if r.get("type") == "run":
            assert "precompute_s" in r and "decide_s" in r
