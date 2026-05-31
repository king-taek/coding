"""불필요 레시피 스킵 — 폴백중복/함정대조/과거저성능 자동 제외(헤드리스)."""

from __future__ import annotations

import json

from aoi_verification.app.dev import benchmark as bm
from aoi_verification.app.dev import recipes as rx
from aoi_verification.app.models.slot import ImageItem


# ---------------------------------------------------------------------------
# skip_reason — 장치/패키지 없으면 폴백 중복이라 스킵
# ---------------------------------------------------------------------------
def test_skip_reason_needs_device():
    assert bm.skip_reason(rx.by_key("npu_b8"), set())          # NPU 없음 → 스킵
    assert bm.skip_reason(rx.by_key("gpu_fusion_b16"), set())  # GPU 없음 → 스킵
    assert not bm.skip_reason(rx.by_key("npu_b8"), {"NPU"})    # NPU 있으면 측정
    assert not bm.skip_reason(rx.by_key("cpu_classical_full"), set())  # 장치 불필요
    assert not bm.skip_reason(rx.by_key("cpu_embed_fusion"), set())    # CPU recall


# ---------------------------------------------------------------------------
# diagnostic — 함정/대조용은 플래그가 붙어 있다
# ---------------------------------------------------------------------------
def test_diagnostic_recipes_flagged():
    diag = {r.key for r in rx.ALL_EXTENDED if r.diagnostic}
    assert "gpu_fusion_b1" in diag           # batch1 함정
    assert "gpu_embed_only" in diag          # 재채점 없음(저정확도 대조)
    assert "gpu_npu_ensemble_fusion" in diag # 앙상블 안티패턴
    assert "gpu_fusion_b16" not in diag       # 운영은 함정 아님


# ---------------------------------------------------------------------------
# low_performers — 과거 기록에서 운영보다 낮았던 것만(노이즈 관용)
# ---------------------------------------------------------------------------
def _hist(devices, rows):
    """rows = {key: recall1}. production=gpu_fusion_b16."""
    return [{
        "devices": devices,
        "production_key": "gpu_fusion_b16",
        "runs": [{"key": k, "ok": True, "recall1": v,
                  "fell_back_classical": False, "skipped": False}
                 for k, v in rows.items()],
    }]


def test_low_performers_flags_clear_losers():
    hist = _hist(["GPU", "NPU"], {
        "gpu_fusion_b16": 0.97,
        "gpu_embed_only": 0.61,     # 크게 낮음 → 스킵
        "npu_mbnet_cpu_fuse": 0.97,  # 동률 → 유지
        "npu_extract_cpu_fuse": 0.95,  # 2%p 차 → margin 내 관용
    })
    low = bm.low_performers(hist, margin=0.03)
    assert "gpu_embed_only" in low
    assert "npu_mbnet_cpu_fuse" not in low
    assert "npu_extract_cpu_fuse" not in low      # 근소차 관용


def test_low_performers_ignores_cpu_fallback_records():
    # 가속기 없는 기록(devices=[])은 변별력 없음 → 저성능 판정 제외.
    hist = _hist([], {"gpu_fusion_b16": 1.0, "gpu_embed_only": 1.0})
    assert bm.low_performers(hist) == {}


def test_low_performers_respects_ever_ok():
    # 한 회차는 낮지만 다른 회차에서 운영 이상 → 저성능으로 단정 안 함.
    hist = _hist(["GPU"], {"gpu_fusion_b16": 0.97, "gpu_fusion_topk20": 0.80}) \
        + _hist(["GPU"], {"gpu_fusion_b16": 0.97, "gpu_fusion_topk20": 0.97})
    assert "gpu_fusion_topk20" not in bm.low_performers(hist)


# ---------------------------------------------------------------------------
# run_suite 통합 — 폴백 중복은 측정 안 하고 skipped 로 기록(기준선은 항상 측정)
# ---------------------------------------------------------------------------
def _tiny_ds(tmp_path):
    import cv2
    import numpy as np
    ref = tmp_path / "ref" / "S1"
    val = tmp_path / "val" / "S1"
    ref.mkdir(parents=True)
    val.mkdir(parents=True)
    for nm in ("a", "b"):
        cv2.imwrite(str(ref / f"{nm}.png"),
                    (np.random.rand(16, 16, 3) * 255).astype("uint8"))
        cv2.imwrite(str(val / f"{nm}.png"),
                    (np.random.rand(16, 16, 3) * 255).astype("uint8"))
    return bm.build_dataset(tmp_path / "ref", tmp_path / "val")


def test_run_suite_skips_redundant_no_accel(tmp_path, monkeypatch):
    # 가속기 없음 → 임베딩 레시피는 폴백 중복으로 skipped, 측정은 CPU 고전만.
    monkeypatch.setattr(bm, "detect_devices", lambda: set())
    monkeypatch.setattr(bm, "low_performers", lambda *a, **k: {})
    ds = _tiny_ds(tmp_path)
    spec = "gpu_fusion_b16,npu_b8,cpu_classical_full"
    suite = bm.run_suite(ds, rx.select(spec), skip_low_history=False,
                         explicit_keys=rx.explicit_keys(spec))
    by = {r.key: r for r in suite.runs}
    assert by["cpu_classical_full"].ok and not by["cpu_classical_full"].skipped
    # 개별 키로 직접 고른 경우(explicit) → 스킵하지 않고 그대로 측정(폴백).
    assert by["gpu_fusion_b16"].ok and not by["gpu_fusion_b16"].skipped
    assert by["npu_b8"].ok


def test_run_suite_skips_npu_sweep_group_no_accel(tmp_path, monkeypatch):
    # 그룹(npu-sweep)으로 들어온 비명시 NPU 레시피는 가속기 없으면 전부 skipped.
    monkeypatch.setattr(bm, "detect_devices", lambda: set())
    monkeypatch.setattr(bm, "low_performers", lambda *a, **k: {})
    ds = _tiny_ds(tmp_path)
    suite = bm.run_suite(ds, rx.select("npu-sweep"), skip_low_history=False)
    by = {r.key: r for r in suite.runs}
    # NPU 스윕은 전부 건너뜀(폴백 중복), 기준선만 측정됨.
    npu_runs = [r for r in suite.runs if r.key.startswith("npu_")]
    assert npu_runs and all(r.skipped for r in npu_runs)
    assert by["cpu_classical_full"].ok and not by["cpu_classical_full"].skipped


def test_run_suite_default_excludes_diagnostic(tmp_path, monkeypatch):
    # 전체(core)로 돌리면 함정/대조(diagnostic)는 측정 목록에서 빠진다.
    monkeypatch.setattr(bm, "detect_devices", lambda: {"GPU", "NPU"})
    monkeypatch.setattr(bm, "low_performers", lambda *a, **k: {})
    monkeypatch.setattr(bm, "skip_reason", lambda r, d: "")    # 폴백 스킵은 무력화
    ds = _tiny_ds(tmp_path)
    suite = bm.run_suite(ds, rx.select("core"), skip_redundant=False,
                         skip_low_history=False)
    keys = {r.key for r in suite.runs}
    assert "gpu_fusion_b1" not in keys        # 함정은 아예 목록에서 제외
    assert "gpu_embed_only" not in keys
    # all_recipes 해제(include_diagnostic=True)면 다시 포함.
    suite2 = bm.run_suite(ds, rx.select("core"), include_diagnostic=True,
                          skip_redundant=False, skip_low_history=False)
    assert "gpu_fusion_b1" in {r.key for r in suite2.runs}
