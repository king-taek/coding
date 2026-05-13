"""썸네일 + 중간 이미지 사전 생성 워커.

원본 폴더 스캔이 끝난 직후 호출되어 모든 이미지의 200px/800px 캐시를
미리 만든다. 진행률은 progress 시그널로 전달.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PyQt6.QtCore import QObject, QRunnable, QThread, pyqtSignal

from ..models.slot import ImageItem
from ..utils import image_io


class ThumbnailerSignals(QObject):
    progress = pyqtSignal(int, int, str)   # done, total, current path
    finished = pyqtSignal()
    failed = pyqtSignal(str)               # error message


class ThumbnailWorker(QThread):
    """모든 ImageItem 에 대해 썸네일+중간 이미지를 생성하는 QThread."""

    def __init__(self,
                 items: Iterable[ImageItem],
                 also_mid: bool = True,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[ImageItem] = list(items)
        self._also_mid = also_mid
        self._stop = False
        self.signals = ThumbnailerSignals()

    # ------------------------------------------------------------------
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
                image_io.get_thumb_path(item.path)
                if self._also_mid:
                    image_io.get_mid_path(item.path)
            except Exception as exc:
                # 단일 파일 실패는 무시 (로그만 emit)
                self.signals.failed.emit(f"{item.path}: {exc}")
            self.signals.progress.emit(idx, total, str(item.path))

        self.signals.finished.emit()
