"""개발자 벤치마크 러너 — 순수 로직(평가/추천/메모리) + 소규모 통합 테스트."""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from aoi_verification.app.dev import benchmark as bm
from aoi_verification.app.dev import recipes as rx
from aoi_verification.app.dev.benchmark import RecipeRun


# ---------------------------------------------------------------------------
# 순수 로직 — 이미지 매칭 품질과 무관하게 결정적으로 검증
# ---------------------------------------------------------------------------
def test_evaluate_recall_at_k():
    results = {
        ("s", "r1"): [("v1", 0.9), ("v2", 0.8)],
        ("s", "r2"): [("vX", 0.7), ("v2", 0.6)],   # 정답 v2 가 2위(top1 실패, top5 성공)
    }
    gt = {("s", "r1"): {"v1"}, ("s", "r2"): {"v2"}}
    r1, r5, n = bm.evaluate(results, gt)
    assert n == 2
    assert r1 == 0.5
    assert r5 == 1.0


def test_agreement_against_baseline():
    base = {("s", "r1"): [("v1", 0.9)], ("s", "r2"): [("v2", 0.9)]}
    same = {("s", "r1"): [("v1", 0.5)], ("s", "r2"): [("v2", 0.5)]}
    diff = {("s", "r1"): [("v9", 0.5)], ("s", "r2"): [("v2", 0.5)]}
    assert bm.agreement(same, base) == 1.0
    assert bm.agreement(diff, base) == 0.5


def test_recommend_picks_fastest_that_preserves_accuracy():
    runs = [
        RecipeRun(key=rx.PRODUCTION_SPEED_KEY, name="prod", ok=True,
                  total_sec=10.0, recall1=0.95),
        RecipeRun(key="fast_good", name="fg", ok=True,
                  total_sec=4.0, recall1=0.96),     # 더 빠르고 정확도 보존 → 채택
        RecipeRun(key="fast_bad", name="fb", ok=True,
                  total_sec=2.0, recall1=0.80),      # 최속이지만 정확도 하락 → 제외
        RecipeRun(key="timeout", name="to", ok=False, timed_out=True,
                  total_sec=1.0, recall1=0.99),      # 미완료 → 제외
    ]
    assert bm.recommend(runs) == "fast_good"


def test_recommend_excludes_accuracy_regressions_even_if_faster():
    runs = [
        RecipeRun(key=rx.PRODUCTION_SPEED_KEY, name="prod", ok=True,
                  total_sec=10.0, recall1=0.95),
        RecipeRun(key="only_fast", name="of", ok=True,
                  total_sec=1.0, recall1=0.50),
    ]
    # 더 빠른 후보가 정확도 하락이면 운영을 유지(정확도 우선).
    assert bm.recommend(runs) == rx.PRODUCTION_SPEED_KEY


def test_accuracy_metric_prefers_groundtruth():
    r = RecipeRun(key="k", name="n", recall1=0.9, agree1=0.5)
    assert bm.accuracy_metric(r) == 0.9
    r2 = RecipeRun(key="k", name="n", recall1=None, agree1=0.7)
    assert bm.accuracy_metric(r2) == 0.7


def test_safe_concurrency_clamps_to_memory_and_workload(monkeypatch):
    # 메모리가 충분해도 워크로드(항목 수)보다 크게 띄우지 않는다.
    assert bm.safe_concurrency(3, 32, avail=64 * 1024 * 1024 * 1024) == 3
    # 메모리가 작으면 그만큼 줄인다.
    small = bm.safe_concurrency(1000, 32, avail=8 * 1024 * 1024)
    assert 1 <= small <= 32
    # 메모리 정보를 전혀 얻을 수 없으면 보수적으로 8 이하로 제한한다.
    monkeypatch.setattr(bm, "available_bytes", lambda: None)
    assert bm.safe_concurrency(1000, 32, avail=None) <= 8


# ---------------------------------------------------------------------------
# 소규모 통합 — 실제 이미지로 하니스가 끝까지 돈다
# ---------------------------------------------------------------------------
def _make_distinct_image(path, slot_idx, img_idx):
    """슬롯/인덱스마다 구분되는 구조적 이미지(매칭이 모호하지 않게)."""
    h = w = 96
    arr = np.full((h, w, 3), 30 + 20 * slot_idx, dtype=np.uint8)
    x = 10 + 20 * img_idx
    cv2.rectangle(arr, (x, x), (x + 30, x + 30), (200, 200, 200), -1)
    cv2.circle(arr, (w - 20, h - 20), 8 + 4 * img_idx, (120, 180, 240), -1)
    cv2.imwrite(str(path), arr)


def _make_ref_root(tmp_path, n_slots=2, n_imgs=2):
    root = tmp_path / "ref"
    for s in range(n_slots):
        d = root / f"Slot_{s:02d}"
        d.mkdir(parents=True, exist_ok=True)
        for j in range(n_imgs):
            _make_distinct_image(d / f"img{j}.png", s, j)
    return root


def test_selftest_synthesis_and_suite_end_to_end(tmp_path):
    ref_root = _make_ref_root(tmp_path)
    val_root = tmp_path / "val"
    labels = bm.synthesize_val(ref_root, val_root)
    assert labels, "self-test 라벨 생성 실패"

    ds = bm.build_dataset(ref_root, val_root, labels=labels)
    assert ds.tasks, "공통 slot 없음"
    assert ds.gt, "GT 비어 있음"
    assert ds.n_images() > 0 and ds.n_pairs() > 0

    spec = "cpu_classical_full,gpu_fusion_b16"
    chosen = rx.select(spec)
    # 개별 키를 직접 골랐으므로 explicit — 스킵 없이 그대로 측정(가속기 없으면 폴백).
    suite = bm.run_suite(ds, chosen, per_recipe_timeout=120,
                         explicit_keys=rx.explicit_keys(spec))

    keys = {r.key for r in suite.runs}
    assert "cpu_classical_full" in keys and "gpu_fusion_b16" in keys
    for run in suite.runs:
        assert run.total_sec >= 0.0
        assert run.recall1 is not None        # GT 있으므로 정확도 측정됨
    assert suite.recommended_key in rx.all_keys()
    assert suite.has_gt is True

    run_dir = bm.write_report(suite, ds, tmp_path / "out")
    assert (run_dir / "result.json").exists()
    assert (run_dir / "report.md").exists()
    md = bm.render_markdown(suite, ds)
    assert "벤치마크" in md and "추천" in md


def test_build_dataset_subsample_limits(tmp_path):
    ref_root = _make_ref_root(tmp_path, n_slots=3, n_imgs=3)
    val_root = tmp_path / "val"
    labels = bm.synthesize_val(ref_root, val_root)
    ds = bm.build_dataset(ref_root, val_root, labels=labels,
                          max_slots=2, max_images_per_side=1)
    assert len(ds.tasks) <= 2
    for _slot, refs, vals in ds.tasks:
        assert len(refs) <= 1 and len(vals) <= 1
