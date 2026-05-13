"""Slot 별 누적 그룹 헤더 + 썸네일 그리드."""

from __future__ import annotations

from typing import Iterable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import i18n
from .thumb_grid import ThumbEntry, ThumbGrid


class SlotSection(QWidget):
    """단일 Slot 의 헤더 + 썸네일 그리드를 한 묶음으로."""

    tile_clicked = pyqtSignal(object)            # ThumbEntry
    plus_clicked = pyqtSignal(str)               # slot name

    def __init__(self,
                 slot_name: str,
                 *,
                 columns: int = 4,
                 select_mode: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._slot = slot_name

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(6)

        header = QHBoxLayout()
        self._label = QLabel(self)
        self._label.setProperty("role", "subtitle")
        self._label.setStyleSheet("font-weight: 700; color: #00D4FF;")
        header.addWidget(self._label)
        header.addStretch(1)
        outer.addLayout(header)

        self.grid = ThumbGrid(columns=columns, select_mode=select_mode,
                              truncate=True, parent=self)
        self.grid.tile_clicked.connect(self.tile_clicked.emit)
        self.grid.plus_clicked.connect(lambda: self.plus_clicked.emit(self._slot))
        outer.addWidget(self.grid)

    # ------------------------------------------------------------------
    def set_entries(self, entries: Iterable[ThumbEntry]) -> None:
        entries = list(entries)
        self._label.setText(
            i18n.KO.GROUP_HEADER_FMT.format(slot=self._slot, count=len(entries))
        )
        self.grid.set_entries(entries)

    def set_select_mode(self, on: bool) -> None:
        self.grid.set_select_mode(on)

    @property
    def slot_name(self) -> str:
        return self._slot
