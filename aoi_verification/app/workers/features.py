"""특징 추출(=유사도 비교에 필요한 모든 디스크립터) 워커."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..models.slot import ImageItem
from ..similarity import pipeline as sim


class FeatureSignals(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class FeatureWorker(QThread):
    """이미지 목록을 받아 Feature 캐시를 채워두는 QThread."""

    def __init__(self,
                 items: Iterable[ImageItem],
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[ImageItem] = list(items)
        self._stop = False
        self.signals = FeatureSignals()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        total = len(self._items)
        if total == 0:
            self.signals.finished.emit()
            return

        for idx, item in enumerate(self._items, start=1):
            if self._stop:
                break
            try:
                sim.extract(item.path)
            except Exception as exc:
                self.signals.failed.emit(f"{item.path}: {exc}")
            self.signals.progress.emit(idx, total, str(item.path))

        self.signals.finished.emit()
