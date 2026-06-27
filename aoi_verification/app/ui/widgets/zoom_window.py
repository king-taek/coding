"""Slot 의 모든 사진을 중간(800px) 크기로 보여주는 줌-뷰 윈도우.

- 패널 종류(source) 별로 타이틀과 액션 버튼이 달라진다.
- 단일 클릭 → 액션 버튼 활성화 / 더블 클릭 → 풀스크린 뷰어.
- 다중 선택 + 일괄 액션 지원.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap, QShortcut, QKeySequence
from PyQt6.QtWidgets import (QApplication, QDialog, QGridLayout, QHBoxLayout,
                              QLabel, QPushButton, QScrollArea, QVBoxLayout,
                              QWidget)

from ... import i18n
from ...models.slot import ImageItem
from ...utils import image_io
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls

# source 종류 ----------------------------------------------------------------
SOURCE_TARGET = "target"        # 검증 대상 (Right)
SOURCE_EXCLUDED = "excluded"    # 제외됨 (Bottom)
SOURCE_CANDIDATES = "candidate" # 후보 (Left)


# ---------------------------------------------------------------------------
class _MidTile(QWidget):
    """800px 중간 이미지 + 파일명 + 선택 상태."""

    clicked = pyqtSignal(object)         # ImageItem
    double_clicked = pyqtSignal(object)  # ImageItem
    toggled = pyqtSignal(object, bool)   # (ImageItem, on)

    TILE_W = 360
    TILE_H = 360

    def __init__(self, item: ImageItem, *, dim: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self._dim = dim
        self._selected = False
        self.setFixedSize(self.TILE_W, self.TILE_H + 30)
        self.setStyleSheet(
            "QWidget { background: #0E1424; border: 1px solid #1F2A3F; "
            "border-radius: 8px; }"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._img_label = QLabel(self)
        self._img_label.setFixedSize(self.TILE_W - 12, self.TILE_H - 18)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._load_pix()
        lay.addWidget(self._img_label)

        cap = QLabel(item.filename, self)
        cap.setProperty("role", "muted")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet("border: none;")
        lay.addWidget(cap)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _load_pix(self) -> None:
        try:
            mid = image_io.get_mid_path(self.item.path)
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(self.TILE_W, self.TILE_H)
            pix.fill(QColor(20, 28, 40))
        if pix.isNull():
            pix = QPixmap(self.TILE_W, self.TILE_H)
            pix.fill(QColor(20, 28, 40))
        pix = pix.scaled(
            self.TILE_W - 12, self.TILE_H - 18,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._dim:
            faded = QPixmap(pix.size())
            faded.fill(Qt.GlobalColor.transparent)
            p = QPainter(faded)
            p.setOpacity(0.30)
            p.drawPixmap(0, 0, pix)
            p.end()
            pix = faded
        self._img_label.setPixmap(pix)

    def set_selected(self, on: bool) -> None:
        self._selected = on
        if on:
            self.setStyleSheet(
                "QWidget { background: #0E1424; border: 2px solid #39FF14; "
                "border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                "QWidget { background: #0E1424; border: 1px solid #1F2A3F; "
                "border-radius: 8px; }"
            )
        self.toggled.emit(self.item, on)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.set_selected(not self._selected)
            self.clicked.emit(self.item)
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.item)
        super().mouseDoubleClickEvent(e)


# ---------------------------------------------------------------------------
class FullscreenViewer(QDialog):
    """더블 클릭 시 열리는 풀스크린 뷰어 (휠 줌 + 드래그 팬)."""

    def __init__(self, image_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(image_path.name)
        self.setModal(True)
        self.setStyleSheet("background-color: #000;")
        # 작은 모니터에서 1280×800 이 화면을 넘어가지 않도록 화면 가용 영역의
        # 90% 안으로 제한.
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(1280, int(g.width() * 0.9)),
                        min(800, int(g.height() * 0.9)))
        else:
            self.resize(1280, 800)

        self._scale = 1.0
        self._offset_x = 0
        self._offset_y = 0
        self._last_drag = None

        # 풀 사이즈 이미지는 너무 클 수 있어 우선 mid 를 사용한다.
        self._pix = QPixmap(str(image_io.get_mid_path(image_path)))
        if self._pix.isNull():
            self._pix = QPixmap(800, 600)
            self._pix.fill(Qt.GlobalColor.black)

        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("background-color: #000;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)

        QShortcut(QKeySequence("Esc"), self, activated=self.close)

    def resizeEvent(self, e):  # noqa: N802
        self._redraw()
        super().resizeEvent(e)

    def wheelEvent(self, e):  # noqa: N802
        step = 1.1 if e.angleDelta().y() > 0 else (1.0 / 1.1)
        self._scale = max(0.1, min(8.0, self._scale * step))
        self._redraw()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._last_drag = e.position().toPoint()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._last_drag is not None:
            cur = e.position().toPoint()
            self._offset_x += cur.x() - self._last_drag.x()
            self._offset_y += cur.y() - self._last_drag.y()
            self._last_drag = cur
            self._redraw()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._last_drag = None

    def _redraw(self) -> None:
        w = int(self._pix.width() * self._scale)
        h = int(self._pix.height() * self._scale)
        scaled = self._pix.scaled(
            w, h, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        # 단순 중앙 + offset
        cw = self.width()
        ch = self.height()
        canvas = QPixmap(cw, ch)
        canvas.fill(Qt.GlobalColor.black)
        p = QPainter(canvas)
        x = (cw - scaled.width()) // 2 + self._offset_x
        y = (ch - scaled.height()) // 2 + self._offset_y
        p.drawPixmap(x, y, scaled)
        p.end()
        self._label.setPixmap(canvas)


# ---------------------------------------------------------------------------
class ZoomWindow(QDialog):
    """Slot 내 사진 전체를 mid 크기로 보여주는 다이얼로그."""

    # action_requested: (action_id, list[ImageItem])
    action_requested = pyqtSignal(str, list)

    def __init__(self,
                 slot_name: str,
                 items: Iterable[ImageItem],
                 source: str,
                 *,
                 already_matched_items: Iterable[ImageItem] = (),
                 view_only: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        # 닫는 즉시 C++ 위젯 해제 — 매번 열 때마다 부모에 누적되지 않도록.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._view_only = bool(view_only)
        self._slot = slot_name
        self._items = list(items)
        self._already_matched = list(already_matched_items)
        self._source = source
        self._tiles: list[_MidTile] = []
        self._selected: list[ImageItem] = []

        title_fmt = {
            SOURCE_TARGET: i18n.KO.ZOOM_TITLE_TARGETS,
            SOURCE_EXCLUDED: i18n.KO.ZOOM_TITLE_EXCLUDED,
            SOURCE_CANDIDATES: i18n.KO.ZOOM_TITLE_CANDIDATES,
        }[source]
        self.setWindowTitle(title_fmt.format(slot=slot_name))
        self._resize_within_screen(1280, 800)

        # 창에 최소화/최대화 버튼 + F11 전체화면 토글 (#9). 첫 show 이전에 설정.
        enable_window_controls(self)
        add_fullscreen_shortcut(self)

        self._build()

    @staticmethod
    def _screen_available(parent) -> tuple[int, int]:
        """다이얼로그가 띄워질 모니터의 ‘작업 영역(taskbar 제외)’ 크기."""
        scr = None
        if parent is not None and hasattr(parent, "screen"):
            scr = parent.screen()
        if scr is None:
            scr = QApplication.primaryScreen()
        if scr is None:
            return (1280, 800)
        g = scr.availableGeometry()
        return (g.width(), g.height())

    def _resize_within_screen(self, w: int, h: int) -> None:
        sw, sh = self._screen_available(self.parent())
        # 모니터의 90% 까지만 차지 → 다이얼로그 옆 / 작업표시줄이 가려지지 않도록.
        self.resize(min(w, int(sw * 0.9)), min(h, int(sh * 0.9)))

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 액션 바
        bar = QHBoxLayout()
        bar.setSpacing(8)

        if self._view_only:
            # 액션 없음 — 단순 확대 뷰어.
            self._btn_a = self._btn_b = None  # type: ignore[assignment]
        elif self._source == SOURCE_TARGET:
            self._btn_a = NeonButton(i18n.KO.ZOOM_BTN_EXCLUDE, role="danger")
            self._btn_b = NeonButton(i18n.KO.ZOOM_BTN_TO_CENTER, role="warn")
            self._btn_a.clicked.connect(lambda: self._emit("exclude"))
            self._btn_b.clicked.connect(lambda: self._emit("recenter"))
        elif self._source == SOURCE_EXCLUDED:
            self._btn_a = NeonButton(i18n.KO.ZOOM_BTN_TO_TARGET, role="primary")
            self._btn_b = NeonButton(i18n.KO.ZOOM_BTN_TO_CENTER, role="warn")
            self._btn_a.clicked.connect(lambda: self._emit("verify"))
            self._btn_b.clicked.connect(lambda: self._emit("recenter"))
        elif self._source == SOURCE_CANDIDATES:
            # Stage 2 의 +N 후보 — 단일 선택으로 매칭 확정 가능 (#2)
            self._btn_a = NeonButton(i18n.KO.ZOOM_BTN_PICK_MATCH, role="primary")
            self._btn_a.clicked.connect(lambda: self._emit("pick"))
            # 좁은 노트북 화면에서도 라벨이 잘리지 않도록 최소 폭 확보.
            self._btn_a.setMinimumWidth(220)
            self._btn_b = None  # type: ignore[assignment]
        else:
            self._btn_a = self._btn_b = None  # type: ignore[assignment]

        if self._btn_a is not None:
            self._btn_a.setEnabled(False)
            # 라벨이 잘리지 않도록 sizeHint 를 최소값으로 보장.
            self._btn_a.setMinimumWidth(
                max(self._btn_a.sizeHint().width(),
                    self._btn_a.minimumWidth())
            )
            bar.addWidget(self._btn_a)
        if self._btn_b is not None:
            self._btn_b.setEnabled(False)
            self._btn_b.setMinimumWidth(
                max(self._btn_b.sizeHint().width(),
                    self._btn_b.minimumWidth())
            )
            bar.addWidget(self._btn_b)
        bar.addStretch(1)
        close = NeonButton(i18n.KO.BTN_OK, role="ghost")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        root.addLayout(bar)

        # 그리드 (스크롤)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget()
        scroll.setWidget(host)
        grid = QGridLayout(host)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(10)

        cols = 3
        row = 0
        col = 0
        for item in self._items:
            tile = _MidTile(item, dim=False, parent=host)
            tile.toggled.connect(self._on_toggle)
            tile.double_clicked.connect(self._open_fullscreen)
            grid.addWidget(tile, row, col)
            self._tiles.append(tile)
            col += 1
            if col >= cols:
                col = 0
                row += 1

        # 이미 매칭된 항목들 (Phase B 시나리오) — 회색 처리 후 클릭 가능
        if self._already_matched:
            row += 1
            col = 0
            sep = QLabel(i18n.KO.INFO_ALREADY_MATCHED_SECTION, host)
            sep.setProperty("role", "muted")
            sep.setStyleSheet("color: #586378; padding: 12px 0;")
            grid.addWidget(sep, row, 0, 1, cols)
            row += 1
            for item in self._already_matched:
                tile = _MidTile(item, dim=True, parent=host)
                grid.addWidget(tile, row, col)
                col += 1
                if col >= cols:
                    col = 0
                    row += 1

        root.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------------
    def _on_toggle(self, item: ImageItem, selected: bool) -> None:
        if selected:
            if item not in self._selected:
                self._selected.append(item)
        else:
            if item in self._selected:
                self._selected.remove(item)
        any_sel = bool(self._selected)
        if self._btn_a is not None:
            self._btn_a.setEnabled(any_sel)
        if self._btn_b is not None:
            self._btn_b.setEnabled(any_sel)

    def _emit(self, action: str) -> None:
        if not self._selected:
            return
        self.action_requested.emit(action, list(self._selected))
        # 처리 후 즉시 닫는다 — 호출자가 데이터 갱신 후 다시 열도록
        self.accept()

    def _open_fullscreen(self, item: ImageItem) -> None:
        viewer = FullscreenViewer(item.path, self)
        viewer.exec()
