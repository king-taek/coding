"""KLA 정보파일 읽기 백그라운드 워커.

폴더마다 정보파일 헤더(``WaferID "XXXX";``)를 읽는 반복이 UI 스레드에서 돌면 폴더가
많을 때 로딩 덮개 애니메이션까지 멈춘다.  읽기만 여기서 하고(결과·판정 로직은 호출부 그대로),
진행은 시그널로 보내 덮개에 '정보파일 읽는 중 · 4 / 25' 로 보인다.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..coords import kla_info


class _Signals(QObject):
    progress = pyqtSignal(int, int)        # done, total
    done = pyqtSignal(dict)                # {(side, name): WaferID}  — 읽힌 것만


class KlaInfoReadWorker(QThread):
    """``jobs`` = [(side, folder_name, folder_path), …]. 결과 키는 ``(side, folder_name)``."""

    def __init__(self, jobs: List[Tuple[str, str, Path]],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._jobs = list(jobs)
        self.signals = _Signals()

    def run(self) -> None:        # type: ignore[override]
        out: dict = {}
        total = len(self._jobs)
        for i, (side, name, folder) in enumerate(self._jobs):
            self.signals.progress.emit(i, total)
            try:
                wid = kla_info.read_wafer_id(folder)
            except Exception:
                wid = None
            if wid:
                out[(side, name)] = wid
        self.signals.progress.emit(total, total)
        self.signals.done.emit(out)
