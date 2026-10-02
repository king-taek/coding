"""AVAGO 재리뷰 — 웨이퍼마다 1차 리뷰 맵을 대조하는 백그라운드 워커.

웨이퍼 하나에 Camtek INI(결함 수천 건)·``s_DieLocation.dat`` 을 파싱하므로 LOT 이면
초 단위다 — UI 스레드에서 돌리면 로딩바가 멈춘다(CLAUDE.md 로딩바 규칙).  진행은
웨이퍼 단위로 시그널로 올린다."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..coords import rereview


class RereviewPlannerSignals(QObject):
    progress = pyqtSignal(int, int)        # done, total
    done = pyqtSignal(object)              # {slot명 → WaferPlan}


class RereviewPlanner(QThread):
    """``jobs`` : [(slot명, 웨이퍼 폴더, [사진 경로])]."""

    def __init__(self, jobs, map_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self._jobs = list(jobs)
        self._map_dir = Path(map_dir)
        self.signals = RereviewPlannerSignals()

    def run(self) -> None:      # type: ignore[override]
        total = len(self._jobs)
        plans: dict = {}
        self.signals.progress.emit(0, total)
        for k, (slot, folder, paths) in enumerate(self._jobs, start=1):
            # plan_wafer 는 전 구간 fail-safe(실패 = 그 웨이퍼 전부 재리뷰 + 경고).
            plans[slot] = rereview.plan_wafer(slot, folder, paths, self._map_dir)
            self.signals.progress.emit(k, total)
        self.signals.done.emit(plans)
