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
    sel_toggled = pyqtSignal(object, bool)    # (ThumbEntry, selected) — 인라인 선택

    _SEL_STYLE = ("QFrame { border: 3px solid #00D4FF; border-radius: 8px;"
                  " background: rgba(0, 212, 255, 0.06); }")

    def __init__(self,
                 entry: ThumbEntry,
                 *,
                 select_mode: bool = False,
                 inline_select: bool = False,
                 dim: bool = False,
                 footer: str = "",
                 show_expand: bool = False,
                 tile_px: Optional[int] = None,
                 prefer_mid: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.entry = entry
        self._dim = dim
        self._inline_select = bool(inline_select)
        self._inline_selected = False
        self._tile_px = int(tile_px) if tile_px else THUMB_PX
        # 후보 패널은 prefer_mid=True 로 mid 캐시 (~800px) 를 소스로 사용 →
        # 같은 표시 크기에서도 더 선명 (#5).
        self._prefer_mid = bool(prefer_mid)
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
            # prefer_mid: 후보 패널처럼 더 선명한 표시가 필요하면 mid 캐시
            # (~800px) 를 소스로.  표시 크기는 동일해도 다운스케일 품질이 ↑.
            if self._prefer_mid:
                tp = image_io.get_mid_path(self.entry.item.path)
            else:
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

    # 인라인 선택(체크박스 없이 클릭 토글) ---------------------------------
    def set_inline_selected(self, selected: bool) -> None:
        self._inline_selected = bool(selected)
        self.setStyleSheet(self._SEL_STYLE if self._inline_selected else "")

    def is_inline_selected(self) -> bool:
        return self._inline_selected

    # 마우스 클릭 → 시그널 (체크박스/확대 버튼 클릭과 분리) -----------------
    def mousePressEvent(self, event):  # noqa: N802
        if self._checkbox is not None and self._checkbox.geometry().contains(event.pos()):
            return super().mousePressEvent(event)
        if self._expand_btn is not None and self._expand_btn.geometry().contains(event.pos()):
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            if self._inline_select:
                # 인라인 선택 모드: 클릭=선택 토글(파란 테두리), 즉시 동작 없음 (#2).
                self.set_inline_selected(not self._inline_selected)
                self.sel_toggled.emit(self.entry, self._inline_selected)
            else:
                self.clicked.emit(self.entry)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        # 인라인 선택 모드에서 더블클릭 = 선택 해제 (확대 모드 제거).
        # 더블클릭 직전의 단일 press 가 토글했더라도 여기서 OFF 로 고정 →
        # 더블클릭은 항상 ‘해제’로 끝난다.  super() 를 호출하면 QWidget 기본
        # 구현이 press 를 재생성해 다시 토글하므로 여기서 종료한다.
        if self._inline_select and event.button() == Qt.MouseButton.LeftButton:
            self.set_inline_selected(False)
            self.sel_toggled.emit(self.entry, False)
            return
        super().mouseDoubleClickEvent(event)


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
    inline_changed = pyqtSignal()                      # 인라인 선택 변경 알림

    def __init__(self,
                 *,
                 columns: int = 4,
                 select_mode: bool = False,
                 inline_select: bool = False,
                 truncate: bool = True,
                 show_expand: bool = False,
                 tile_px: Optional[int] = None,
                 prefer_mid: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._select_mode = select_mode
        self._inline_select = bool(inline_select)
        self._truncate = truncate
        self._show_expand = show_expand
        self._tile_px = tile_px
        self._prefer_mid = bool(prefer_mid)
        self._entries: list[ThumbEntry] = []
        self._selected: list[ThumbEntry] = []
        self._tiles: list[_ThumbTile] = []         # 인라인 선택용 타일 참조
        self._active_cols = columns                # 현재 적용 중인 열 수(반응형)

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
        self._tiles = []

        threshold = config.CONFIG.show_n_threshold
        max_visible = config.CONFIG.max_thumbs_per_row

        # 인라인 선택 모드는 +N 생략 없이 전체를 표시(전체 선택/드래그가 모두
        # 유효하도록, #2). 그 외에는 기존 +N 트렁케이션 유지.
        if self._truncate and not self._inline_select \
                and len(self._entries) >= threshold:
            visible = self._entries[:max_visible]
            extra = len(self._entries) - max_visible
        else:
            visible = self._entries
            extra = 0

        cols = self._effective_columns()
        self._active_cols = cols
        row = 0
        col = 0
        for ent in visible:
            tile = _ThumbTile(ent, select_mode=self._select_mode,
                              inline_select=self._inline_select,
                              footer=ent.item.filename,
                              show_expand=self._show_expand,
                              tile_px=self._tile_px,
                              prefer_mid=self._prefer_mid)
            tile.clicked.connect(self.tile_clicked.emit)
            tile.toggled.connect(self._on_toggle)
            tile.sel_toggled.connect(self._on_sel_toggle)
            tile.expand_requested.connect(self.expand_requested.emit)
            self._tiles.append(tile)
            self._grid.addWidget(tile, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        if extra > 0:
            plus = _PlusTile(extra, tile_px=self._tile_px)
            plus.clicked.connect(self.plus_clicked.emit)
            self._grid.addWidget(plus, row, col)

    # ------------------------------------------------------------------
    # 반응형 열 수 — 패널 폭에 맞춰 가로 스크롤 없이 자동 reflow.
    # ------------------------------------------------------------------
    def _effective_columns(self) -> int:
        spacing = self._grid.spacing()
        tile_w = (self._tile_px or THUMB_PX) + 14 + spacing
        avail = self.width()
        if avail <= 0:
            return self._columns
        return max(1, min(self._columns, avail // tile_w))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        cols = self._effective_columns()
        if cols != self._active_cols:
            self._active_cols = cols
            self._relayout_columns(cols)

    def _relayout_columns(self, cols: int) -> None:
        """기존 그리드 위젯(타일 + +N)을 순서 보존하여 새 cols 로 재배치 —
        위젯 재생성/재디코드 없음."""
        widgets = []
        for i in reversed(range(self._grid.count())):
            it = self._grid.takeAt(i)
            w = it.widget()
            if w is not None:
                widgets.append(w)
        widgets.reverse()
        for i, w in enumerate(widgets):
            self._grid.addWidget(w, i // cols, i % cols)

    def _on_toggle(self, entry: ThumbEntry, selected: bool) -> None:
        if selected:
            if entry not in self._selected:
                self._selected.append(entry)
        else:
            if entry in self._selected:
                self._selected.remove(entry)
        self.selected_changed.emit(list(self._selected))

    # ------------------------------------------------------------------
    # 인라인 선택 (체크박스 없이 클릭/드래그/전체선택) (#2)
    # ------------------------------------------------------------------
    def _on_sel_toggle(self, entry: ThumbEntry, selected: bool) -> None:
        self.inline_changed.emit()

    def tiles(self) -> list[_ThumbTile]:
        return list(self._tiles)

    def inline_selected_items(self) -> list[ImageItem]:
        return [t.entry.item for t in self._tiles if t.is_inline_selected()]

    def set_all_inline_selected(self, selected: bool) -> None:
        for t in self._tiles:
            t.set_inline_selected(selected)
        self.inline_changed.emit()

    def set_inline_selected_for(self, items: set, selected: bool) -> None:
        """``items`` (ImageItem.key 집합) 에 해당하는 타일만 선택/해제."""
        changed = False
        for t in self._tiles:
            if t.entry.item.key in items:
                t.set_inline_selected(selected)
                changed = True
        if changed:
            self.inline_changed.emit()
