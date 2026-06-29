"""'일부 슬롯만 진행' 선택 다이얼로그.

기준 폴더에서 발견된 슬롯(하위 폴더) 목록을 **큰 카드 그리드**로 보여주고, 이번 검증에서
진행할 슬롯만 카드를 눌러 고르게 한다.  결과는 ``selected`` 속성(슬롯명 집합)으로 가져간다.
미선택(취소) 시 호출자가 전체 진행으로 처리한다.

작은 체크박스 대신 **카드 전체가 클릭영역**이라 잘 보이고 잘 눌린다.  선택 카드는 네온
초록 테두리/배경+✓ 로 강조한다.  타일 토글/반응형 열 계산은 BulkSelectDialog 의
``_SelectTile``/``_relayout_grids`` 패턴을 차용한다.
"""

from __future__ import annotations

from typing import Iterable, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QVBoxLayout, QWidget)

from ... import i18n
from .neon_button import NeonButton
from .window_controls import add_fullscreen_shortcut, enable_window_controls

# 카드 한 장 크기와 최대 열 수.  좁은 창에선 viewport 폭 기반으로 더 적게 동적 계산.
_TILE_W = 150
_TILE_H = 64
_MAX_COLS = 5


class _SlotTile(QFrame):
    """클릭 토글 가능한 큰 슬롯 카드.  카드 전체가 클릭영역(작은 체크박스 X).

    선택 시 네온 초록 테두리/배경+✓ 로 강조한다.
    """

    toggled = pyqtSignal(str, bool)        # (슬롯명, 선택여부)

    def __init__(self, name: str, *, selected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self._selected = bool(selected)
        # objectName 스코프 셀렉터로 테두리가 내부 라벨까지 번지지 않게 한다.
        self.setObjectName("slotTile")
        self.setProperty("role", "card-soft")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(_TILE_W, _TILE_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(0)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self._label.setToolTip(name)
        lay.addWidget(self._label)

        self._refresh_visual()

    # ------------------------------------------------------------------
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, selected: bool) -> None:
        if bool(selected) == self._selected:
            return
        self._selected = bool(selected)
        self._refresh_visual()

    def _toggle(self) -> None:
        self._selected = not self._selected
        self._refresh_visual()
        self.toggled.emit(self.name, self._selected)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return,
                            Qt.Key.Key_Enter):
            self._toggle()
            return
        super().keyPressEvent(event)

    def _refresh_visual(self) -> None:
        # 이름 — 길면 가운데 '…' 으로 줄이고, 선택 시 앞에 ✓ 표시.
        prefix = "✓ " if self._selected else ""
        fm = QFontMetrics(self._label.font())
        text = fm.elidedText(self.name, Qt.TextElideMode.ElideMiddle,
                             _TILE_W - 28)
        self._label.setText(prefix + text)
        if self._selected:
            self.setStyleSheet(
                "#slotTile { border: 3px solid #39FF14; border-radius: 8px;"
                " background: rgba(57, 255, 20, 0.10); }"
            )
        else:
            self.setStyleSheet("")


class SlotSelectDialog(QDialog):
    """발견된 슬롯 목록을 큰 카드 그리드로 보여주고 진행할 슬롯만 토글로 선택."""

    def __init__(self,
                 slot_names: Iterable[str],
                 *,
                 preselected: Optional[Iterable[str]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(i18n.KO.SLOT_SELECT_TITLE)
        self.resize(560, 600)
        self._slot_names = sorted(set(slot_names))
        self._preselected = (set(preselected) if preselected is not None
                             else set(self._slot_names))
        self._accepted = False
        self._tiles: dict[str, _SlotTile] = {}
        enable_window_controls(self)
        add_fullscreen_shortcut(self)
        self._build()

    @property
    def selected(self) -> set[str]:
        """선택된 슬롯명 집합."""
        return {name for name, tile in self._tiles.items()
                if tile.is_selected()}

    @property
    def accepted_ok(self) -> bool:
        return self._accepted

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        hint = QLabel(i18n.KO.SLOT_SELECT_HINT, self)
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # 상단 바 — 전체 선택/해제 + 우측 선택 수.
        top = QHBoxLayout()
        btn_all = NeonButton(i18n.KO.SLOT_SELECT_ALL, role="ghost")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none = NeonButton(i18n.KO.SLOT_SELECT_NONE, role="ghost")
        btn_none.clicked.connect(lambda: self._set_all(False))
        self._count_label = QLabel(self)
        self._count_label.setProperty("role", "muted")
        top.addWidget(btn_all)
        top.addWidget(btn_none)
        top.addStretch(1)
        top.addWidget(self._count_label)
        root.addLayout(top)

        # 카드 그리드(세로 스크롤만, 가로 스크롤 X).
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(self._scroll)
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(10)
        self._grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for name in self._slot_names:
            tile = _SlotTile(name, selected=name in self._preselected,
                             parent=host)
            tile.toggled.connect(self._on_tile_toggled)
            self._tiles[name] = tile
        self._scroll.setWidget(host)
        root.addWidget(self._scroll, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        cancel = NeonButton(i18n.KO.BTN_CANCEL, role="ghost")
        cancel.clicked.connect(self.reject)
        ok = NeonButton(i18n.KO.BTN_OK, role="primary")
        ok.clicked.connect(self._on_ok)
        bar.addWidget(cancel)
        bar.addWidget(ok)
        root.addLayout(bar)

        self._update_count()
        QTimer.singleShot(0, self._relayout)

    # ------------------------------------------------------------------
    def _relayout(self) -> None:
        """viewport 폭에 맞춰 그리드 열 수 자동 계산 — 가로 스크롤 회피."""
        if not self._tiles:
            return
        vp_w = self._scroll.viewport().width() if hasattr(self, "_scroll") else 0
        if vp_w <= 0:
            vp_w = self.width()
        tile_w = _TILE_W + self._grid.spacing()
        cols = max(1, min(_MAX_COLS, max(1, vp_w // tile_w)))
        # 위젯을 떼어내고 cols 로 재배치(위젯 자체는 삭제하지 않고 재사용).
        while self._grid.count():
            self._grid.takeAt(0)
        for idx, name in enumerate(self._slot_names):
            self._grid.addWidget(self._tiles[name], idx // cols, idx % cols)
        self._grid.setColumnStretch(cols, 1)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._relayout)

    # ------------------------------------------------------------------
    def _on_tile_toggled(self, _name: str, _selected: bool) -> None:
        self._update_count()

    def _set_all(self, checked: bool) -> None:
        for tile in self._tiles.values():
            tile.set_selected(checked)
        self._update_count()

    def _update_count(self) -> None:
        self._count_label.setText(
            i18n.KO.SLOT_SELECT_DIALOG_COUNT_FMT.format(
                n=len(self.selected), total=len(self._slot_names),
            )
        )

    def _on_ok(self) -> None:
        self._accepted = True
        self.accept()
