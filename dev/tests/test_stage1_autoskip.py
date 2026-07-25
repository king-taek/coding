"""Stage 1 에 판단할 후보가 하나도 없으면 곧장 매칭으로 넘어간다.

증상: '이전 기준 사진 재사용 → 예' 로 기준 사진이 전부 복원되면 선별 큐가 비는데,
그래도 Stage 1(후보 선별) 화면이 떠서 왼쪽(대기)·중앙(현재 사진)이 텅 빈 채로
사용자가 할 수 있는 조작이 없었다.  큐가 비면 선별 화면을 건너뛰고 Stage 2 로 간다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication              # noqa: E402

from aoi_verification.app.models.slot import ImageItem, ScanResult, Slot  # noqa: E402
from aoi_verification.app.ui import main_window as mw   # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _scan_with(slot_name: str, n_ref: int) -> ScanResult:
    refs = [ImageItem(slot_name, Path(f"/tmp/{slot_name}_ref{i}.png"), "ref")
            for i in range(n_ref)]
    vals = [ImageItem(slot_name, Path(f"/tmp/{slot_name}_val{i}.png"), "val")
            for i in range(2)]
    slot = Slot(name=slot_name, ref_images=refs, val_images=vals)
    return ScanResult(slots={slot_name: slot}, ref_only=[], val_only=[])


class _Win:
    """`_enter_stage1_phase_a` 만 실제 코드로 돌리는 최소 대역(headless)."""

    def __init__(self, scan, restored):
        self._scan = scan
        self._input = object()
        self._restored = restored
        self._phase = None
        self.shown = []
        self.finished_calls = 0
        self.autosaves = 0

        class _Page:
            def __init__(self):
                self.loaded = None

            def load_state(self, **kw):
                self.loaded = kw

        self._select_page = _Page()

    # 재사용 질의는 대역이 대신한다 — 복원분을 queue 에서 빼고 targets 로.
    def _maybe_restore_ref_selection(self, queue):
        if not self._restored:
            return {}
        keep = {it for items in self._restored.values() for it in items}
        queue[:] = [it for it in queue if it not in keep]
        return self._restored

    def _show_page(self, page):
        self.shown.append(page)

    def _on_select_finished(self):
        self.finished_calls += 1

    def _autosave(self):
        self.autosaves += 1


def _run(scan, restored):
    win = _Win(scan, restored)
    mw.MainWindow._enter_stage1_phase_a(win)
    return win


def test_empty_queue_skips_select_page(qapp):
    """재사용으로 큐가 전부 비면 선별 화면을 띄우지 않고 매칭으로 간다."""
    scan = _scan_with("A", 3)
    restored = {"A": list(scan.slots["A"].ref_images)}     # 전부 복원 → 큐 0
    win = _run(scan, restored)

    assert win._select_page.loaded["queue"] == []          # 큐가 비었고
    assert win.finished_calls == 1                         # 매칭으로 넘어갔으며
    assert win.shown == []                                 # 빈 선별 화면은 안 띄운다
    assert win._phase == mw.PHASE_A_SELECT                 # 단계 표시는 정상 설정


def test_remaining_queue_still_shows_select_page(qapp):
    """판단할 후보가 남아 있으면 기존대로 선별 화면을 띄운다(회귀 방지)."""
    scan = _scan_with("A", 3)
    refs = scan.slots["A"].ref_images
    restored = {"A": [refs[0]]}                            # 1장만 복원 → 2장 남음
    win = _run(scan, restored)

    assert len(win._select_page.loaded["queue"]) == 2
    assert win.finished_calls == 0                         # 자동 진행하지 않고
    assert win.shown == [win._select_page]                 # 선별 화면을 띄운다
    assert win.autosaves == 1


def test_no_reuse_shows_select_page(qapp):
    """재사용 기록이 없으면(복원 0) 전량이 큐에 남아 선별 화면이 뜬다."""
    win = _run(_scan_with("A", 2), {})
    assert len(win._select_page.loaded["queue"]) == 2
    assert win.finished_calls == 0
    assert win.shown == [win._select_page]
