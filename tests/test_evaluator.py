"""learning.evaluator — log_decision, aggregate, Wilson CI, per-slot, refresh."""

import json
from pathlib import Path

from aoi_verification.app.learning import evaluator as E
from aoi_verification.app.learning import registry as R
from aoi_verification.app.utils import paths


def _write_log(name: str, rows: list[dict]) -> None:
    f = paths.evaluations_dir() / f"{name}.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_aggregate_empty_returns_zero(isolated_cache):
    m = E.aggregate("nonexistent")
    assert m.num_evaluations == 0
    assert m.picks == 0
    assert m.hit_at_5 == 0.0


def test_aggregate_picks_only(isolated_cache):
    _write_log("modelA", [
        {"slot": "S01", "decision": "pick", "picked_rank": 0,
         "candidates": [], "ts": "2026-05-13T10:00:00"},
        {"slot": "S01", "decision": "pick", "picked_rank": 1,
         "candidates": [], "ts": "2026-05-13T10:00:01"},
        {"slot": "S02", "decision": "pick", "picked_rank": 4,
         "candidates": [], "ts": "2026-05-13T10:00:02"},
        {"slot": "S02", "decision": "pick", "picked_rank": 7,
         "candidates": [], "ts": "2026-05-13T10:00:03"},
    ])
    m = E.aggregate("modelA")
    assert m.num_evaluations == 4
    assert m.picks == 4
    assert m.hit_at_1 == 1 / 4
    assert m.hit_at_5 == 3 / 4
    assert m.hit_at_8 == 1.0
    assert m.mean_rank == (1 + 2 + 5 + 8) / 4


def test_decision_defer_excluded_from_metrics(isolated_cache):
    _write_log("m", [
        {"slot": "S01", "decision": "pick", "picked_rank": 0, "ts": ""},
        {"slot": "S01", "decision": "defer", "picked_rank": None, "ts": ""},
        {"slot": "S01", "decision": "defer", "picked_rank": None, "ts": ""},
        {"slot": "S01", "decision": "none", "picked_rank": None, "ts": ""},
    ])
    m = E.aggregate("m")
    # picks=1, none=1 → num_evals=2; defers=2 (제외)
    assert m.picks == 1
    assert m.none_count == 1
    assert m.defers == 2
    assert m.num_evaluations == 2


def test_legacy_skipped_field_treated_as_defer(isolated_cache):
    _write_log("legacy", [
        {"slot": "S01", "skipped": False, "picked_rank": 0, "ts": ""},
        {"slot": "S01", "skipped": True, "picked_rank": None, "ts": ""},
    ])
    m = E.aggregate("legacy")
    assert m.picks == 1
    assert m.defers == 1


def test_wilson_interval_bounds():
    lo, hi = E.wilson_interval(0, 0)
    assert lo == 0.0 and hi == 0.0
    lo, hi = E.wilson_interval(5, 10)
    assert 0.0 <= lo < 0.5 < hi <= 1.0
    lo, hi = E.wilson_interval(100, 100)
    assert lo > 0.9 and hi <= 1.0


def test_per_slot_breakdown(isolated_cache):
    _write_log("m", [
        {"slot": "S01", "decision": "pick", "picked_rank": 0, "ts": ""},
        {"slot": "S01", "decision": "pick", "picked_rank": 1, "ts": ""},
        {"slot": "S01", "decision": "pick", "picked_rank": 9, "ts": ""},   # out of 5
        {"slot": "S02", "decision": "pick", "picked_rank": 0, "ts": ""},
    ])
    m = E.aggregate("m")
    assert "S01" in m.per_slot and "S02" in m.per_slot
    picks_s01, hit5_s01 = m.per_slot["S01"]
    assert picks_s01 == 3
    assert abs(hit5_s01 - 2 / 3) < 1e-9


def test_refresh_accuracy_renames_when_threshold_crossed(isolated_cache):
    # 모의 모델 파일 + 평가 로그 생성
    name = "2026-05-13"
    info = R._build_files(name)
    info.weights_path.parent.mkdir(parents=True, exist_ok=True)
    info.weights_path.write_bytes(b"PT_FAKE")
    R.write_meta(info, {"name": name, "num_train_pairs": 30})
    rows = [{"slot": "S01", "decision": "pick", "picked_rank": 0, "ts": ""}
            for _ in range(20)]
    _write_log(name, rows)
    outcomes = E.refresh_accuracy()
    assert outcomes
    new_name = outcomes[0].info.name
    # Hit@5 = 100% → 이름에 _HitAt5_100 부여
    assert "HitAt5" in new_name
