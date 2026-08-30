"""Slot 별 누적 그룹 헤더 + 썸네일 그리드."""

from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import i18n
from .thumb_grid import ThumbEntry, ThumbGrid


class SlotSection(QWidget):
    """단일 Slot 의 헤더 + 썸네일 그리드를 한 묶음으로."""

    tile_clicked = pyqtSignal(object)            # ThumbEntry
    plus_clicked = pyqtSignal(str)               # slot name
    expand_requested = pyqtSignal(object)        # ThumbEntry — 타일 우상단 확대 버튼
    inline_changed = pyqtSignal()                # 인라인 선택 변경 (#2)

    def __init__(self,
                 slot_name: str,
                 *,
                 columns: int = 4,
                 select_mode: bool = False,
                 inline_select: bool = False,
                 truncate: bool = True,
                 tile_px: Optional[int] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._slot = slot_name

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 6)
        outer.setSpacing(6)

        header = QHBoxLayout()
        self._label = QLabel(self)
        self._label.setProperty("role", "paneTitle")
        header.addWidget(self._label)
        header.addStretch(1)
        outer.addLayout(header)

        self.grid = ThumbGrid(columns=columns, select_mode=select_mode,
                              inline_select=inline_select, truncate=truncate,
                              tile_px=tile_px, parent=self)
        self.grid.tile_clicked.connect(self.tile_clicked.emit)
        self.grid.plus_clicked.connect(lambda: self.plus_clicked.emit(self._slot))
        self.grid.expand_requested.connect(self.expand_requested.emit)
        self.grid.inline_changed.connect(self.inline_changed.emit)
        outer.addWidget(self.grid)

    # ------------------------------------------------------------------
    def set_entries(self, entries: Iterable[ThumbEntry]) -> None:
        entries = list(entries)
        self._label.setText(
            i18n.KO.GROUP_HEADER_FMT.format(slot=self._slot, count=len(entries))
        )
        self.grid.set_entries(entries)

    def inline_selected(self) -> list[ThumbEntry]:
        return self.grid.inline_selected()

    def remove_entry(self, entry: ThumbEntry) -> bool:
        """한 장만 지우고 헤더 개수를 갱신한다(전체 재생성 회피)."""
        if not self.grid.remove_entry(entry):
            return False
        self._label.setText(
            i18n.KO.GROUP_HEADER_FMT.format(
                slot=self._slot, count=len(self.grid._entries))
        )
        return True
