"""Stage 2 ‘더 크게 보기’ 모드 — 후보 한 장을 크게 보면서 확정.

MatchPage 의 QStackedWidget 두 번째 페이지로 사용된다.  ZoomWindow 같은
풀스크린 모달 대신 같은 화면 안에서 페이지 전환이라 메모리/속도 면에서 가볍다.

UI 요소:
- 상단 sticky 바: 슬롯명 / 위치 / ◀ 이전 · 다음 ▶ · [이 사진으로 매칭] · [← 돌아가기]
- 본문: QScrollArea 안에 ScalableImage (mid 캐시 이미지) 가 viewport 폭에
  맞춰 큰 사이즈로 표시.

입력 후보 리스트와 ‘현재 인덱스’ 는 외부에서 ``load_candidates`` 로 주입.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
                              QSlider, QVBoxLayout, QWidget)

from ... import i18n
from ...models.slot import ImageItem
from .neon_button import NeonButton
from .scalable_image import ScalableImage


class MatchExpandView(QWidget):
    """후보 한 장을 큰 화면으로 보고 매칭 확정/취소하는 위젯."""

    # 외부 시그널 ---------------------------------------------------------
    confirm_match = pyqtSignal(object)        # ImageItem — 현재 보고 있는 후보
    back_requested = pyqtSignal()             # ‘돌아가기’ — 그리드로 복귀

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._candidates: List[ImageItem] = []
        self._index = 0
        # 사용자가 슬라이더로 정한 표시 크기.  세션 동안 유지되도록 보관.
        self._target_long_edge: Optional[int] = None
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Sticky top bar -----------------------------------------------
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.slot_label = QLabel("", self)
        self.slot_label.setStyleSheet(
            "color: #00D4FF; font-weight: 700; font-size: 16px;"
        )
        bar.addWidget(self.slot_label)
        bar.addSpacing(20)

        self.pos_label = QLabel("", self)
        self.pos_label.setProperty("role", "muted")
        bar.addWidget(self.pos_label)
        bar.addStretch(1)

        self.btn_prev = NeonButton(i18n.KO.BTN_EXPAND_PREV, role="ghost")
        self.btn_prev.clicked.connect(self._on_prev)
        bar.addWidget(self.btn_prev)

        self.btn_next = NeonButton(i18n.KO.BTN_EXPAND_NEXT, role="ghost")
        self.btn_next.clicked.connect(self._on_next)
        bar.addWidget(self.btn_next)

        bar.addSpacing(10)
        self.btn_confirm = NeonButton(i18n.KO.BTN_CONFIRM_AS_MATCH, role="primary")
        self.btn_confirm.clicked.connect(self._on_confirm)
        bar.addWidget(self.btn_confirm)

        self.btn_back = NeonButton(i18n.KO.BTN_BACK_TO_GRID, role="ghost")
        self.btn_back.clicked.connect(self.back_requested.emit)
        bar.addWidget(self.btn_back)

        root.addLayout(bar)

        # 사진 크기 슬라이더 (#1) — 기본값은 외부 (MatchPage 의 기준 사진 크기) 가
        # set_default_long_edge() 로 주입.  사용자가 변경하면 이후 사진들도 같은
        # 크기로 표시 (load_candidates 시점에도 _target_long_edge 유지).
        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        size_label = QLabel(i18n.KO.IMAGE_SIZE_LABEL, self)
        size_label.setProperty("role", "muted")
        self._size_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._size_slider.setRange(ScalableImage.MIN_LONG_EDGE,
                                    ScalableImage.MAX_LONG_EDGE)
        self._size_slider.setSingleStep(20)
        self._size_slider.setPageStep(80)
        self._size_slider.setValue(ScalableImage.auto_fit_long_edge())
        self._size_value = QLabel(f"{self._size_slider.value()} px", self)
        self._size_value.setProperty("role", "muted")
        self._size_value.setFixedWidth(64)
        self._size_value.setAlignment(Qt.AlignmentFlag.AlignRight
                                      | Qt.AlignmentFlag.AlignVCenter)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        size_row.addWidget(size_label)
        size_row.addWidget(self._size_slider, stretch=1)
        size_row.addWidget(self._size_value)
        root.addLayout(size_row)

        # 이미지 영역 ---------------------------------------------------
        self._img = ScalableImage(self)
        self._img.set_target_size(self._size_slider.value())
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._img)
        self._scroll.setStyleSheet(
            "QScrollArea { background: #050810; border: 1px solid #1F2A3F;"
            " border-radius: 8px; }"
        )
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding,
                                     QSizePolicy.Policy.Expanding)
        root.addWidget(self._scroll, stretch=1)

        # 단축키 — 부모(QStackedWidget 의 현재 페이지) 가 활성일 때만 동작.
        # WidgetWithChildrenShortcut context 로 격리한다.
        self._mk_shortcut("Left", self._on_prev)
        self._mk_shortcut("Right", self._on_next)
        self._mk_shortcut("Return", self._on_confirm)
        self._mk_shortcut("Enter", self._on_confirm)
        self._mk_shortcut("Escape", self.back_requested.emit)

    def _mk_shortcut(self, key: str, callback: Callable[..., None]) -> None:
        sc = QShortcut(QKeySequence(key), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(callback)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_candidates(self,
                        slot: str,
                        candidates: List[ImageItem],
                        start_index: int = 0,
                        default_long_edge: Optional[int] = None) -> None:
        """``default_long_edge`` 가 주어지면 사용자가 아직 슬라이더를 만지지
        않은 경우에 한해 그 값을 기본 크기로 적용 (보통 MatchPage 중앙 기준
        사진과 동일).  사용자가 한 번이라도 슬라이더를 움직였다면 그 값이
        세션 동안 유지된다 (#1)."""
        self._candidates = list(candidates)
        self._index = max(0, min(start_index, len(self._candidates) - 1)
                          if self._candidates else 0)
        self.slot_label.setText(i18n.KO.SLOT_LABEL_FMT.format(slot=slot))
        if default_long_edge is not None and self._target_long_edge is None:
            v = max(ScalableImage.MIN_LONG_EDGE,
                    min(ScalableImage.MAX_LONG_EDGE, int(default_long_edge)))
            self._size_slider.blockSignals(True)
            self._size_slider.setValue(v)
            self._size_slider.blockSignals(False)
            self._size_value.setText(f"{v} px")
            self._img.set_target_size(v)
        self._refresh()

    def _on_size_changed(self, v: int) -> None:
        self._target_long_edge = int(v)
        self._size_value.setText(f"{v} px")
        self._img.set_target_size(v)

    def current_item(self) -> Optional[ImageItem]:
        if not self._candidates:
            return None
        return self._candidates[self._index]

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        n = len(self._candidates)
        if n == 0:
            self._img.clear_image()
            self.pos_label.setText("")
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.btn_confirm.setEnabled(False)
            return
        cur = self._candidates[self._index]
        self._img.set_image(cur.path)
        self.pos_label.setText(i18n.KO.EXPAND_POSITION_FMT.format(
            cur=self._index + 1, total=n,
        ))
        self.btn_prev.setEnabled(self._index > 0)
        self.btn_next.setEnabled(self._index < n - 1)
        self.btn_confirm.setEnabled(True)

    def _on_prev(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._refresh()

    def _on_next(self) -> None:
        if self._index < len(self._candidates) - 1:
            self._index += 1
            self._refresh()

    def _on_confirm(self) -> None:
        cur = self.current_item()
        if cur is None:
            return
        self.confirm_match.emit(cur)
