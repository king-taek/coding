"""그룹 보기 다이얼로그 (#15).

- 한 그룹의 모든 사진을 800px (mid) 크기로 그리드 표시.
- 각 사진에 “그룹에서 분리” 액션 버튼이 있어, 그 사진은 별도 결정 대상이 된다.
- 사용자가 닫은 뒤 호출자는 ``removed_items`` 로 분리된 항목들을 받아 큐에
  되돌려 넣는다.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                              QScrollArea, QVBoxLayout, QWidget)

from ... import i18n
from ...models.group import PhotoGroup
from ...models.slot import ImageItem
from ...utils import image_io
from .neon_button import NeonButton


_TILE = 320


class _GroupTile(QWidget):
    detach_requested = pyqtSignal(object)        # ImageItem

    def __init__(self, item: ImageItem, *, is_rep: bool, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setFixedSize(_TILE, _TILE + 50)
        self.setStyleSheet(
            "QWidget { background: #0E1424; border: 1px solid #1F2A3F; "
            "border-radius: 8px; }"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        img = QLabel(self)
        img.setFixedSize(_TILE - 12, _TILE - 18)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setStyleSheet("border: none;")
        try:
            mid = image_io.get_mid_path(item.path)
            pix = QPixmap(str(mid))
        except Exception:
            pix = QPixmap(_TILE, _TILE)
            pix.fill(QColor(20, 28, 40))
        if pix.isNull():
            pix = QPixmap(_TILE, _TILE)
            pix.fill(QColor(20, 28, 40))
        pix = pix.scaled(
            _TILE - 12, _TILE - 18,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img.setPixmap(pix)
        lay.addWidget(img)

        cap = QLabel(item.filename + ("  ·  대표" if is_rep else ""), self)
        cap.setProperty("role", "muted")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setStyleSheet("border: none; color: #7FB3D5;")
        lay.addWidget(cap)

        btn = NeonButton(i18n.KO.GROUP_BTN_DETACH, role="warn")
        btn.clicked.connect(lambda: self.detach_requested.emit(self.item))
        lay.addWidget(btn)


class GroupDialog(QDialog):
    """그룹 보기 + 분리 다이얼로그."""

    # 닫힌 후 분리된 항목 목록을 가져갈 수 있는 속성으로도 제공.
    detach_signal = pyqtSignal(object)       # ImageItem

    def __init__(self, group: PhotoGroup, parent=None) -> None:
        super().__init__(parent)
        self._group = group
        self._removed: list[ImageItem] = []
        self.setWindowTitle(
            i18n.KO.GROUP_DIALOG_TITLE_FMT.format(
                slot=group.slot, n=group.size(),
            )
        )
        self.resize(1200, 800)
        self._build()

    @property
    def removed_items(self) -> list[ImageItem]:
        return list(self._removed)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        info = QLabel(i18n.KO.GROUP_DIALOG_HINT, self)
        info.setProperty("role", "muted")
        info.setWordWrap(True)
        root.addWidget(info)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        host = QWidget()
        scroll.setWidget(host)
        self._grid = QGridLayout(host)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setSpacing(10)
        root.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        bar.addStretch(1)
        close_btn = NeonButton(i18n.KO.BTN_OK, role="primary")
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        root.addLayout(bar)

        self._render()

    def _render(self) -> None:
        # clear
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        items = self._group.all_items()
        cols = 3
        for idx, item in enumerate(items):
            tile = _GroupTile(item, is_rep=(item.key == self._group.rep.key))
            tile.detach_requested.connect(self._on_detach)
            self._grid.addWidget(tile, idx // cols, idx % cols)

    def _on_detach(self, item: ImageItem) -> None:
        self._removed.append(item)
        self.detach_signal.emit(item)
        # 그룹에서도 즉시 제거 (대표일 수도, sibling 일 수도)
        if item.key == self._group.rep.key:
            # 대표를 분리하면 첫 sibling 을 새 대표로 격상
            if self._group.siblings:
                new_rep = self._group.siblings.pop(0)
                self._group.rep = new_rep
            else:
                # 그룹이 비었음 — 다이얼로그 닫기
                self.accept()
                return
        else:
            self._group.siblings = [
                s for s in self._group.siblings if s.key != item.key
            ]
        self._render()
