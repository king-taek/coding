"""네온 외곽선이 들어간 카드 컨테이너."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QVBoxLayout


class NeonCard(QFrame):
    """역할(role) 별로 다른 외곽선 색을 갖는 카드."""

    def __init__(self, *, role: str = "card", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", role)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(8)

        if role == "card":
            eff = QGraphicsDropShadowEffect(self)
            eff.setOffset(0, 0)
            eff.setBlurRadius(24)
            eff.setColor(QColor("#39FF14"))
            self.setGraphicsEffect(eff)

    def body(self) -> QVBoxLayout:
        return self._layout
