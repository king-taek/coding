"""썸네일 그리드 (+N 처리, 선택 모드 지원)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QLabel,
                              QToolButton, QVBoxLayout, QWidget)

from ... import config, i18n
from ...models.slot import ImageItem
from ...utils import image_io


THUMB_PX = config.Sizing.THUMB_PX


# ---------------------------------------------------------------------------
@dataclass
class ThumbEntry:
    item: ImageItem
    extra: dict | None = None     # 추가 메타 (예: score, dim_overlay)


class _ThumbTile(QFrame):
    """단일 썸네일 박스."""

    clicked = pyqtSignal(object)              # ThumbEntry
    toggled = pyqtSignal(object, bool)        # (ThumbEntry, selected)
    expand_requested = pyqtSignal(object)     # ThumbEntry — ‘더 크게 보기’

    def __init__(self,
                 entry: ThumbEntry,
                 *,
                 select_mode: bool = False,
                 dim: bool = False,
                 footer: str = "",
                 show_expand: bool = False,
                 tile_px: Optional[int] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self._dim = dim
        self._tile_px = int(tile_px) if tile_px else THUMB_PX
        self.setFixedSize(self._tile_px + 14, self._tile_px + (40 if footer else 18))
        self.setProperty("role", "card-soft")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._img = QLabel(self)
        self._img.setFixedSize(self._tile_px, self._tile_px)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pix()
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        if footer:
            cap = QLabel(footer, self)
            cap.setProperty("role", "muted")
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(cap)

        self._checkbox: Optional[QCheckBox] = None
        if select_mode:
            self._enable_checkbox()

        # ‘더 크게 보기’ 버튼 (선택 사항 — Stage 2 후보 타일에만 표시) ----
        self._expand_btn: Optional[QToolButton] = None
        if show_expand:
            btn = QToolButton(self)
            btn.setText("🔍")
            btn.setToolTip(i18n.KO.EXPAND_VIEW_TOOLTIP)
            btn.setAutoRaise(True)
            btn.setFixedSize(QSize(24, 24))
            btn.setStyleSheet(
                "QToolButton { background: rgba(0,212,255,0.18);"
                "  color: #00D4FF; border: 1px solid #00D4FF;"
                "  border-radius: 4px; font-size: 14px; }"
                "QToolButton:hover { background: rgba(0,212,255,0.35); }"
            )
            btn.move(self.width() - 28, 4)
            btn.show()
            btn.clicked.connect(
                lambda: self.expand_requested.emit(self.entry)
            )
            self._expand_btn = btn

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ------------------------------------------------------------------
    def _load_pix(self) -> None:
        size = self._tile_px
        try:
            tp = image_io.get_thumb_path(self.entry.item.path)
            pix = QPixmap(str(tp))
            if pix.isNull():
                pix = QPixmap(size, size)
                pix.fill(QColor(20, 28, 40))
            pix = pix.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        except Exception:
            pix = QPixmap(size, size)
            pix.fill(QColor(20, 28, 40))

        if self._dim:
            faded = QPixmap(pix.size())
            faded.fill(Qt.GlobalColor.transparent)
            p = QPainter(faded)
            p.setOpacity(0.35)
            p.drawPixmap(0, 0, pix)
            p.end()
            pix = faded

        self._img.setPixmap(pix)

    def _enable_checkbox(self) -> None:
        cb = QCheckBox(self)
        cb.move(8, 8)
        cb.stateChanged.connect(
            lambda st: self.toggled.emit(
                self.entry, st == Qt.CheckState.Checked.value,
            )
        )
        cb.show()
        self._checkbox = cb

    # 마우스 클릭 → 시그널 (체크박스/확대 버튼 클릭과 분리) -----------------
    def mousePressEvent(self, event):  # noqa: N802
        if self._checkbox is not None and self._checkbox.geometry().contains(event.pos()):
            return super().mousePressEvent(event)
        if self._expand_btn is not None and self._expand_btn.geometry().contains(event.pos()):
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.entry)
        super().mousePressEvent(event)


class _PlusTile(QFrame):
    """+N 표시 타일."""

    clicked = pyqtSignal()

    def __init__(self, n: int, *, tile_px: Optional[int] = None,
                 parent=None) -> None:
        super().__init__(parent)
        size = int(tile_px) if tile_px else THUMB_PX
        self.setProperty("role", "card-soft")
        self.setFixedSize(size + 14, size + 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lab = QLabel(i18n.KO.COUNT_PLUS_N_FMT.format(n=n), self)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lab.setStyleSheet(
            "color: #00D4FF;"
            "font-size: 28px;"
            "font-weight: 700;"
            "border: 2px dashed #00D4FF;"
            "border-radius: 8px;"
        )
        lab.setMinimumHeight(size)
        lay.addWidget(lab)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
class ThumbGrid(QWidget):
    """+N 처리(5장 이상 → 첫 4장 + +N) 가 들어간 그리드.

    selected_changed: 선택 모드에서 체크 변경될 때 emit.
    """

    tile_clicked = pyqtSignal(object)                  # ThumbEntry
    plus_clicked = pyqtSignal()
    selected_changed = pyqtSignal(list)                # list[ThumbEntry]
    expand_requested = pyqtSignal(object)              # ThumbEntry

    def __init__(self,
                 *,
                 columns: int = 4,
                 select_mode: bool = False,
                 truncate: bool = True,
                 show_expand: bool = False,
                 tile_px: Optional[int] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._select_mode = select_mode
        self._truncate = truncate
        self._show_expand = show_expand
        self._tile_px = tile_px
        self._entries: list[ThumbEntry] = []
        self._selected: list[ThumbEntry] = []

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)

    # ------------------------------------------------------------------
    def set_entries(self, entries: Iterable[ThumbEntry]) -> None:
        self._entries = list(entries)
        self._selected.clear()
        self.selected_changed.emit([])
        self._rebuild()

    def set_select_mode(self, on: bool) -> None:
        self._select_mode = on
        self._selected.clear()
        self.selected_changed.emit([])
        self._rebuild()

    def selected(self) -> list[ThumbEntry]:
        return list(self._selected)

    # ------------------------------------------------------------------
    def _rebuild(self) -> None:
        # clear
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        threshold = config.CONFIG.show_n_threshold
        max_visible = config.CONFIG.max_thumbs_per_row

        if self._truncate and len(self._entries) >= threshold:
            visible = self._entries[:max_visible]
            extra = len(self._entries) - max_visible
        else:
            visible = self._entries
            extra = 0

        row = 0
        col = 0
        for ent in visible:
            tile = _ThumbTile(ent, select_mode=self._select_mode,
                              footer=ent.item.filename,
                              show_expand=self._show_expand,
                              tile_px=self._tile_px)
            tile.clicked.connect(self.tile_clicked.emit)
            tile.toggled.connect(self._on_toggle)
            tile.expand_requested.connect(self.expand_requested.emit)
            self._grid.addWidget(tile, row, col)
            col += 1
            if col >= self._columns:
                col = 0
                row += 1
        if extra > 0:
            plus = _PlusTile(extra, tile_px=self._tile_px)
            plus.clicked.connect(self.plus_clicked.emit)
            self._grid.addWidget(plus, row, col)

    def _on_toggle(self, entry: ThumbEntry, selected: bool) -> None:
        if selected:
            if entry not in self._selected:
                self._selected.append(entry)
        else:
            if entry in self._selected:
                self._selected.remove(entry)
        self.selected_changed.emit(list(self._selected))
