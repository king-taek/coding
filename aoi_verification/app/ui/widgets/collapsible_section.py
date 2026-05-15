"""접고 펼칠 수 있는 섹션 위젯.

상단 토글 버튼을 누르면 본문 영역이 부드럽게 펼쳐지거나 접힌다.
- ``QToolButton`` 헤더 + ``QWidget`` 본문 구조
- ``QPropertyAnimation`` 으로 ``maximumHeight`` 를 0 ↔ contentHeight 사이로 애니메이션
- 펼침 상태를 외부에서 읽고 쓸 수 있도록 ``is_expanded`` / ``set_expanded`` 제공
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (QEasingCurve, QPropertyAnimation, Qt, pyqtSignal)
from PyQt6.QtWidgets import (QFrame, QSizePolicy, QToolButton, QVBoxLayout,
                              QWidget)

from ... import i18n


_ANIM_DURATION_MS = 180


class CollapsibleSection(QWidget):
    """제목 + 토글 버튼 + 접을 수 있는 본문 영역."""

    toggled = pyqtSignal(bool)

    def __init__(self,
                 open_label: str = i18n.KO.HOWTO_TOGGLE_OPEN,
                 close_label: str = i18n.KO.HOWTO_TOGGLE_CLOSE,
                 expanded: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._open_label = open_label
        self._close_label = close_label
        self._expanded = bool(expanded)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 헤더 (토글 버튼) ----------------------------------------------------
        self._toggle = QToolButton(self)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(self._expanded)
        self._toggle.setText(self._close_label if self._expanded else self._open_label)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._toggle.setAutoRaise(True)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle.clicked.connect(self._on_clicked)
        root.addWidget(self._toggle)

        # 본문 컨테이너 ------------------------------------------------------
        self._content = QFrame(self)
        self._content.setFrameShape(QFrame.Shape.NoFrame)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 4, 0, 0)
        self._content_layout.setSpacing(6)
        self._content.setMaximumHeight(0 if not self._expanded else 16777215)
        root.addWidget(self._content)

        self._anim = QPropertyAnimation(self._content, b"maximumHeight", self)
        self._anim.setDuration(_ANIM_DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # ------------------------------------------------------------------
    def add_content_widget(self, widget: QWidget) -> None:
        """본문 영역에 위젯 추가."""
        self._content_layout.addWidget(widget)
        if self._expanded:
            self._content.setMaximumHeight(16777215)

    # ------------------------------------------------------------------
    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, expanded: bool, animate: bool = True) -> None:
        """애니메이션 없이 즉시 적용하려면 ``animate=False``."""
        if expanded == self._expanded:
            return
        self._expanded = bool(expanded)
        self._toggle.setChecked(self._expanded)
        self._toggle.setText(self._close_label if self._expanded else self._open_label)

        target = self._content.sizeHint().height() if self._expanded else 0
        if animate:
            current = self._content.maximumHeight()
            self._anim.stop()
            self._anim.setStartValue(int(current))
            self._anim.setEndValue(int(target))
            self._anim.start()
        else:
            # 즉시 적용 — 펼치는 경우엔 무제한으로 두어 sizeHint 가 늘면 자동 적응.
            self._content.setMaximumHeight(16777215 if self._expanded else 0)
        self.toggled.emit(self._expanded)

    # ------------------------------------------------------------------
    def _on_clicked(self) -> None:
        self.set_expanded(not self._expanded, animate=True)
