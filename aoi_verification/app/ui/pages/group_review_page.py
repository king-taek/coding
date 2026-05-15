"""올인원(auto_all) 모드의 ‘그룹 검토’ 페이지.

자동 그룹화 결과를 사용자가 검토하는 단계.  같은 슬롯에서 거의 동일한
사진들이 묶여있는데, 잘못 묶인 사진이 있으면 클릭으로 ‘그룹에서 분리’.
분리된 사진은 다음 Stage 2 자동 매치 큐에 자동으로 들어간다.

검토가 끝나면 [매치 시작] 으로 다음 단계.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                              QSizePolicy, QVBoxLayout, QWidget)

from ... import i18n
from ...models.group import GroupingResult, PhotoGroup
from ...models.slot import ImageItem
from ...utils import image_io
from ..widgets.neon_button import NeonButton
from ..widgets.neon_card import NeonCard


_THUMB_PX = 120


class _PhotoTile(QFrame):
    """그룹 안에서 한 사진을 클릭으로 ‘그룹 분리’ 가능한 작은 타일."""

    detach_requested = pyqtSignal(object)        # ImageItem

    def __init__(self, item: ImageItem, *, is_rep: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.item = item
        self.setProperty("role", "card-soft")
        self.setFixedSize(_THUMB_PX + 14, _THUMB_PX + 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if is_rep:
            self.setStyleSheet(
                "QFrame { border: 1px solid #00D4FF; border-radius: 6px; }"
            )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        self._img = QLabel(self)
        self._img.setFixedSize(_THUMB_PX, _THUMB_PX)
        self._img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img.setPixmap(image_io.load_thumb_qpixmap(item.path, _THUMB_PX))
        lay.addWidget(self._img, alignment=Qt.AlignmentFlag.AlignCenter)

        cap_text = ("⭐ " if is_rep else "") + item.filename
        cap = QLabel(cap_text, self)
        cap.setProperty("role", "muted")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setWordWrap(True)
        lay.addWidget(cap)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.detach_requested.emit(self.item)
        super().mousePressEvent(event)


class _GroupCard(NeonCard):
    """한 슬롯의 한 그룹 — 대표 + siblings 가 한 줄로 나열."""

    detach_requested = pyqtSignal(object)        # ImageItem

    def __init__(self, group: PhotoGroup, parent=None) -> None:
        super().__init__(role="card-soft", parent=parent)
        self._group = group
        self._rebuild()

    def _rebuild(self) -> None:
        while self.body().count():
            it = self.body().takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        head = QLabel(
            f"{self._group.slot}  ·  {self._group.size()} 장",
            self,
        )
        head.setStyleSheet("color: #00D4FF; font-weight: 700;")
        self.body().addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        # 대표 먼저, 그 다음 siblings (파일명 순)
        ordered = [self._group.rep] + sorted(
            self._group.siblings, key=lambda x: x.filename.lower()
        )
        for i, it in enumerate(ordered):
            tile = _PhotoTile(it, is_rep=(i == 0), parent=self)
            tile.detach_requested.connect(self.detach_requested.emit)
            row.addWidget(tile)
        row.addStretch(1)

        host = QWidget(self)
        host.setLayout(row)
        self.body().addWidget(host)


class GroupReviewPage(QWidget):
    """auto_all 모드의 그룹 검토 화면 — 잘못 묶인 사진을 분리."""

    match_requested = pyqtSignal()           # [매치 시작] 클릭

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._grouping: GroupingResult | None = None
        self._group_cards: dict[str, _GroupCard] = {}   # rep.key → card
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 헤더
        title = QLabel(i18n.KO.GROUP_REVIEW_PHASE, self)
        title.setProperty("role", "title")
        root.addWidget(title)

        hint = QLabel(i18n.KO.GROUP_REVIEW_HINT, self)
        hint.setProperty("role", "subtitle")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #7FB3D5;")
        root.addWidget(hint)

        # 진행 라벨
        self._summary_label = QLabel("", self)
        self._summary_label.setStyleSheet("color: #00FFA3; font-weight: 700;")
        root.addWidget(self._summary_label)

        # 그룹 리스트 스크롤 영역
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.MinimumExpanding)
        scroll.setWidget(host)
        self._list_layout = QVBoxLayout(host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch(1)
        root.addWidget(scroll, stretch=1)

        # 하단 [매치 시작] 버튼
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.btn_start = NeonButton(i18n.KO.BTN_START_AUTO_MATCH, role="primary")
        self.btn_start.setMinimumWidth(220)
        self.btn_start.setMinimumHeight(46)
        self.btn_start.clicked.connect(self.match_requested.emit)
        bar.addWidget(self.btn_start)
        root.addLayout(bar)

    # ------------------------------------------------------------------
    def load_state(self, grouping: GroupingResult) -> None:
        self._grouping = grouping
        self._refresh()

    def get_queue(self) -> list[ImageItem]:
        """[매치 시작] 시 Stage 2 에 넘길 최종 큐."""
        if self._grouping is None:
            return []
        return list(self._grouping.representatives)

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        while self._list_layout.count():
            it = self._list_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._group_cards.clear()

        groups = (
            self._grouping.remaining_groups() if self._grouping is not None else []
        )
        if not groups:
            empty = QLabel(
                "묶인 그룹이 없습니다.  바로 [매치 시작] 을 누르세요.",
                self,
            )
            empty.setStyleSheet("color: #7FB3D5; padding: 20px;")
            self._list_layout.addWidget(empty)
            self._list_layout.addStretch(1)
            self._update_summary()
            return

        # 슬롯명 → 그 슬롯의 그룹들
        groups_by_slot: dict[str, list[PhotoGroup]] = {}
        for g in groups:
            groups_by_slot.setdefault(g.slot, []).append(g)

        for slot in sorted(groups_by_slot.keys()):
            for g in groups_by_slot[slot]:
                card = _GroupCard(g, parent=self)
                card.detach_requested.connect(self._on_detach)
                self._group_cards[g.rep.key] = card
                self._list_layout.addWidget(card)
        self._list_layout.addStretch(1)
        self._update_summary()

    def _on_detach(self, item: ImageItem) -> None:
        if self._grouping is None:
            return
        self._grouping.detach(item)
        self._refresh()

    def _update_summary(self) -> None:
        if self._grouping is None:
            self._summary_label.setText("")
            return
        n_groups = len(self._grouping.remaining_groups())
        n_queue = len(self._grouping.representatives)
        self._summary_label.setText(
            f"총 매치 대상: {n_queue} 장  ·  남은 그룹: {n_groups} 개"
        )
