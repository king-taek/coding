"""WaferID OCR 백그라운드 워커.

KLA 폴더의 파일명에서 WaferID 를 못 읽은(=형식이 아닌, 예: FrontSideADRImg…)
폴더에 한해, 대표 이미지 헤더의 ``WaferID : XXXX`` 를 OCR 한다.  OCR 엔진 초기화
+ 추론이 수 초 걸려 **메인 스레드에서 돌리면 '응답 없음'** 이 되므로 별도 스레드에서
실행하고, 진행 상황을 시그널로 보내 로딩창에 실시간 표시한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..utils import wafer_id


class _OcrSignals(QObject):
    progress = pyqtSignal(int, int)        # done, total
    done = pyqtSignal(dict, dict)          # ocr_ref, ocr_val
    failed = pyqtSignal(str)


class WaferIdOcrWorker(QThread):
    """``jobs`` = [(side, folder_name, [image_paths]), …]. side ∈ {"ref","val"}."""

    def __init__(self,
                 jobs: List[Tuple[str, str, list]],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._jobs = list(jobs)
        self.signals = _OcrSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        ocr_ref: dict[str, str] = {}
        ocr_val: dict[str, str] = {}
        total = len(self._jobs)
        try:
            for i, (side, name, paths) in enumerate(self._jobs):
                if self._stop:
                    break
                self.signals.progress.emit(i, total)
                try:
                    wid = wafer_id.read_folder_wafer_id([Path(p) for p in paths])
                except Exception:
                    wid = None
                if wid:
                    (ocr_ref if side == "ref" else ocr_val)[name] = wid
            self.signals.progress.emit(total, total)
        except Exception as exc:        # pragma: no cover — 방어
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(ocr_ref, ocr_val)
