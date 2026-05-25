"""WaferID OCR 백그라운드 워커.

slot명이 ref/val 간 일치하지 않을 때, 미매칭 폴더의 대표 이미지 1장씩에서
WaferID 를 순차 OCR 한다.  UI 가 멈추지 않도록 별도 스레드에서 돌리고, 진행
상황을 시그널로 전달해 로딩창에 표시한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..utils import wafer_id


class _OcrSignals(QObject):
    progress = pyqtSignal(int, int)        # done, total
    done = pyqtSignal(dict, dict)          # wid_by_ref, wid_by_val
    failed = pyqtSignal(str)


class WaferIdOcrWorker(QThread):
    """미매칭 폴더들의 WaferID 를 순차 OCR.

    ``jobs`` 는 ``(side, slot_name, [image_paths])`` 튜플 목록.  ``side`` 는
    ``"ref"`` 또는 ``"val"``.  각 폴더에서 첫 장이 실패하면 다음 장으로 넘어가며
    여러 장을 시도한다(``read_folder_wafer_id``).
    """

    def __init__(self, jobs, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._jobs = list(jobs)
        self.signals = _OcrSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        wid_by_ref: dict[str, str] = {}
        wid_by_val: dict[str, str] = {}
        total = len(self._jobs)
        try:
            for idx, (side, name, paths) in enumerate(self._jobs, start=1):
                if self._stop:
                    break
                wid = wafer_id.read_folder_wafer_id(
                    [Path(p) for p in paths])
                if wid:
                    if side == "ref":
                        wid_by_ref[name] = wid
                    else:
                        wid_by_val[name] = wid
                self.signals.progress.emit(idx, total)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(wid_by_ref, wid_by_val)
