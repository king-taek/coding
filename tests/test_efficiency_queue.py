"""고효율 모드 — work-stealing 스케줄러 단위 테스트.

공유 큐에서 모든 ref 가 정확히 1회씩, 유닛 간 중복 없이 처리되는지(동적
부하분산 정확성)와 결과 dict 가 빠짐없이 채워지는지를 검증한다.  실제 Qt
이벤트 루프 없이 ``_run()`` 을 직접 호출 — finished 는 메인 스레드에서 emit
되므로 direct connection 으로 잡힌다."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers import efficiency_matcher as eff


_APP = QCoreApplication.instance() or QCoreApplication([])


class _FakeUnit:
    def __init__(self, tag):
        self.tag = tag
        self.seen = []
        self._lock = threading.Lock()

    def match_batch(self, refs, vals):
        # 스케줄러는 ref 묶음(chunk)을 한 번에 넘긴다 — 각 ref 를 1 회 기록.
        time.sleep(0.001)               # 작업 시뮬레이션 → 스레드 인터리빙 유도
        with self._lock:
            for r in refs:
                self.seen.append((r.slot, r.path))
        return {Path(r.path): [] for r in refs}


def _make_tasks(n_slots=3, refs_per_slot=5):
    tasks = []
    for s in range(n_slots):
        slot = f"S{s}"
        refs = [ImageItem(slot=slot, path=Path(f"{slot}_r{i}.png"), side="ref")
                for i in range(refs_per_slot)]
        vals = [ImageItem(slot=slot, path=Path(f"{slot}_v{i}.png"), side="val")
                for i in range(3)]
        tasks.append((slot, refs, vals))
    return tasks


def test_all_refs_processed_once_no_overlap(monkeypatch):
    units = [_FakeUnit("cpu"), _FakeUnit("gpu"), _FakeUnit("npu")]
    monkeypatch.setattr(eff, "build_units", lambda cfg, thr: units)

    tasks = _make_tasks(n_slots=3, refs_per_slot=5)
    total_refs = sum(len(r) for _, r, _ in tasks)
    results = {}
    finished = []

    sched = eff.EfficiencyScheduler(tasks, cfg=None, threshold=0.0,
                                    auto=True, results=results)
    sched.signals.finished.connect(lambda: finished.append(1))
    sched._run()                        # 동기 실행 (스레드 spawn + join)

    # (a) 모든 (slot, ref) 가 결과에 정확히 존재
    expected = {(s, Path(r.path)) for s, refs, _ in tasks for r in refs}
    assert set(results.keys()) == expected
    assert len(results) == total_refs

    # (b) 유닛들이 처리한 ref 합집합 == 전체, 중복 없음 (work-stealing 정확성)
    all_seen = [item for u in units for item in u.seen]
    assert len(all_seen) == total_refs              # 정확히 1회씩
    assert len(set(all_seen)) == total_refs         # 중복 없음

    # (c) finished 발생 + 활성 유닛 기록
    assert finished == [1]
    assert sched.active_units() == ["cpu", "gpu", "npu"]


def test_empty_tasks_finishes(monkeypatch):
    monkeypatch.setattr(eff, "build_units", lambda cfg, thr: [_FakeUnit("cpu")])
    finished = []
    sched = eff.EfficiencyScheduler([], cfg=None, threshold=0.0, results={})
    sched.signals.finished.connect(lambda: finished.append(1))
    sched._run()
    assert finished == [1]


def test_single_cpu_unit_processes_all(monkeypatch):
    unit = _FakeUnit("cpu")
    monkeypatch.setattr(eff, "build_units", lambda cfg, thr: [unit])
    tasks = _make_tasks(n_slots=2, refs_per_slot=4)
    total = sum(len(r) for _, r, _ in tasks)
    results = {}
    sched = eff.EfficiencyScheduler(tasks, cfg=None, threshold=0.0, results=results)
    sched._run()
    assert len(results) == total
    assert len(unit.seen) == total
