"""동일 defect 그룹 검토 다이얼로그 (#5 재시도).

Stage 1 후보 패널의 [동일 defect 그룹 보기] 버튼이 열어주는 팝업.
``similarity.grouping.GroupingWorker`` 가 만들어준 ``DefectGroup`` 리스트를
받아 그룹별로 사진들을 한 줄에 늘어놓고, 그룹 단위로 ‘전체 검증’ / ‘전체
제외’ 액션을 한 번에 실행할 수 있게 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPixmap
from PyQt6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,
                              QLabel, QScrollArea, QSizePolicy, QVBoxLayout,
                              QWidget)

from ... import i18n
from ...models.slot import ImageItem
from ...similarity.grouping import DefectGroup
from ...utils import image_io
from .neon_button import NeonButton


_TILE_PX = 130          # 그룹 내 사진 한 장의 표시 크기
_CAP_PX = 22            # 캡션


class _GroupRow(QFrame):
    """한 그룹 (동일 defect) 의 사진들 + 그룹 액션 버튼들."""

    verify_clicked = pyqtSignal(list)       # list[ImageItem]
    exclude_clicked = pyqtSignal(list)      # list[ImageItem]

    def __init__(self, group: DefectGroup, parent=None) -> None:
        super().__init__(parent)
        self.group = group
        self.setProperty("role", "card-soft")
        self.setSizePolicy(QSizePolicy.Policy.MinimumExpanding,
                            QSizePolicy.Policy.Maximum)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        # 헤더 — 슬롯 + 개수 + 액션 버튼.
        head = QHBoxLayout()
        head.setSpacing(8)
        ttl = QLabel(
            i18n.KO.GROUP_ROW_HEADER_FMT.format(
                slot=group.slot, n=group.size,
            ),
            self,
        )
        ttl.setStyleSheet(
            "color: #00D4FF; font-weight: 700; padding: 2px 4px;"
        )
        head.addWidget(ttl)
        head.addStretch(1)

        btn_verify = NeonButton(i18n.KO.GROUP_BTN_VERIFY_ALL, role="primary")
        btn_verify.setMinimumWidth(150)
        btn_verify.clicked.connect(
            lambda: self.verify_clicked.emit(list(self.group.items))
        )
        head.addWidget(btn_verify)

        btn_exclude = NeonButton(i18n.KO.GROUP_BTN_EXCLUDE_ALL, role="danger")
        btn_exclude.setMinimumWidth(150)
        btn_exclude.clicked.connect(
            lambda: self.exclude_clicked.emit(list(self.group.items))
        )
        head.addWidget(btn_exclude)
        outer.addLayout(head)

        # 사진 가로 strip — 그룹이 많으면 가로 스크롤.
        strip_host = QWidget(self)
        strip = QHBoxLayout(strip_host)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        for it in group.items:
            tile = _GroupTile(it, parent=strip_host)
            strip.addWidget(tile)
        strip.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(strip_host)
        scroll.setFixedHeight(_TILE_PX + _CAP_PX + 24)
        outer.addWidget(scroll)


class _GroupTile(QFrame):
    """그룹 안의 한 장 — 썸네일 (mid 캐시) + 파일명."""

    def __init__(self, item: ImageItem, parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setFixedSize(_TILE_PX + 8, _TILE_PX + _CAP_PX + 8)
        self.setStyleSheet("background: transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        img = QLabel(self)
        img.setFixedSize(_TILE_PX, _TILE_PX)
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setPixmap(
            image_io.load_thumb_qpixmap(item.path, _TILE_PX, kind="thumb")
        )
        lay.addWidget(img, alignment=Qt.AlignmentFlag.AlignCenter)

        cap = QLabel(self)
        cap.setFixedHeight(_CAP_PX)
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setProperty("role", "muted")
        cap.setStyleSheet("color: #7FB3D5; font-size: 11px;")
        cap.setWordWrap(False)
        fm = QFontMetrics(cap.font())
        cap.setText(fm.elidedText(
            item.filename, Qt.TextElideMode.ElideMiddle, _TILE_PX - 2,
        ))
        cap.setToolTip(item.filename)
        lay.addWidget(cap)


class DefectGroupDialog(QDialog):
    """그룹 리스트를 보여주고 그룹별로 일괄 액션을 실행한다."""

    # action_id: "verify" or "exclude"
    group_action = pyqtSignal(str, list)        # (action_id, ImageItem list)

    def __init__(self,
                 groups: list[DefectGroup],
                 parent=None) -> None:
        super().__init__(parent)
        self._groups = list(groups)
        self.setWindowTitle(i18n.KO.GROUP_DIALOG_TITLE_FMT.format(
            n=sum(g.size for g in self._groups),
            g=len(self._groups),
        ))
        self.setModal(True)
        # 매번 열 때마다 부모에 dialog 가 쌓이지 않도록 자동 폐기.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        # 모니터 작업 영역 안에 맞춤.
        scr = (parent.screen() if parent is not None
               and hasattr(parent, "screen") else None) \
            or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(min(1280, int(g.width() * 0.92)),
                        min(820, int(g.height() * 0.88)))
        else:
            self.resize(1280, 820)
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QLabel(
            i18n.KO.GROUP_DIALOG_HINT, self,
        )
        head.setWordWrap(True)
        head.setStyleSheet("color: #7FB3D5; padding: 4px;")
        root.addWidget(head)

        if not self._groups:
            empty = QLabel(i18n.KO.GROUP_DIALOG_EMPTY, self)
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            root.addWidget(empty)
        else:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            host = QWidget()
            host_l = QVBoxLayout(host)
            host_l.setContentsMargins(0, 0, 0, 0)
            host_l.setSpacing(10)
            for grp in self._groups:
                row = _GroupRow(grp, parent=host)
                row.verify_clicked.connect(
                    lambda items: self._on_action("verify", items)
                )
                row.exclude_clicked.connect(
                    lambda items: self._on_action("exclude", items)
                )
                host_l.addWidget(row)
            host_l.addStretch(1)
            scroll.setWidget(host)
            root.addWidget(scroll, stretch=1)

        # 하단 닫기.
        bar = QHBoxLayout()
        bar.addStretch(1)
        close = NeonButton(i18n.KO.BTN_OK, role="ghost")
        close.clicked.connect(self.accept)
        bar.addWidget(close)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def _on_action(self, action_id: str, items: list[ImageItem]) -> None:
        """그룹 액션 시그널을 외부로 중계. 다이얼로그는 계속 열린 상태 유지."""
        self.group_action.emit(action_id, items)
