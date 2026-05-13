"""기준 사진 1장 vs 같은 슬롯의 검증 후보 N장 — 유사도 정렬 워커."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..config import CONFIG
from ..models.slot import ImageItem
from ..similarity import pipeline as sim


@dataclass
class Candidate:
    item: ImageItem
    score: float


class MatcherSignals(QObject):
    done = pyqtSignal(list)            # list[Candidate]
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)


class MatcherWorker(QThread):
    """단일 기준 이미지에 대해 후보들의 score 를 계산해 정렬해 돌려준다."""

    def __init__(self,
                 ref_item: ImageItem,
                 val_items: Iterable[ImageItem],
                 threshold: Optional[float] = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ref = ref_item
        self._vals: list[ImageItem] = list(val_items)
        self._threshold = threshold if threshold is not None else CONFIG.default_threshold
        self.signals = MatcherSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        try:
            ref_feat = sim.extract(self._ref.path)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        total = len(self._vals)
        out: list[Candidate] = []
        for idx, vi in enumerate(self._vals, start=1):
            if self._stop:
                break
            try:
                vf = sim.extract(vi.path)
                s = sim.score(ref_feat, vf)
            except Exception as exc:
                self.signals.failed.emit(f"{vi.path}: {exc}")
                s = 0.0
            if s >= self._threshold:
                out.append(Candidate(item=vi, score=s))
            self.signals.progress.emit(idx, total)

        out.sort(key=lambda c: c.score, reverse=True)
        self.signals.done.emit(out)
