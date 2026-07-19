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

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(8)

        if role == "card":
            # 네온 글로우 대신 은은한 검정 elevation 그림자 (깊이만 유지).
            eff = QGraphicsDropShadowEffect(self)
            eff.setOffset(0, 2)
            eff.setBlurRadius(20)
            eff.setColor(QColor(0, 0, 0, 140))
            self.setGraphicsEffect(eff)

    def body(self) -> QVBoxLayout:
        return self._layout
