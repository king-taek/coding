"""카드 컨테이너 — role 속성으로 QSS 가 면/보더를 칠한다."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QVBoxLayout


class NeonCard(QFrame):
    """역할(role) 별로 다른 외곽선을 갖는 카드."""

    def __init__(self, *, role: str = "card", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", role)
        self.setFrameShape(QFrame.Shape.NoFrame)

        from .. import theme
        pad = theme.PROFILE.card_pad
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(pad, pad, pad, pad)
        self._layout.setSpacing(8)

        if role == "card" and theme.PROFILE.card_shadow:
            # 변형별 elevation 그림자(밝은 테마는 회색, 다크는 검정) — 글로우 대체.
            eff = QGraphicsDropShadowEffect(self)
            eff.setOffset(0, 2)
            eff.setBlurRadius(20)
            eff.setColor(QColor(*theme.SHADOW_RGBA))
            self.setGraphicsEffect(eff)

    def body(self) -> QVBoxLayout:
        return self._layout
