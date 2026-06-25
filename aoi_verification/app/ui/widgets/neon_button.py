"""네온 글로우 효과가 적용된 버튼."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QPushButton


class NeonButton(QPushButton):
    """기본 사이언 글로우 + role 속성 기반 색상 분기."""

    def __init__(self,
                 text: str = "",
                 role: str = "default",
                 parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_glow(role)
        self.setMinimumHeight(34)

    # ------------------------------------------------------------------
    def _apply_glow(self, role: str) -> None:
        color_map = {
            "primary": "#39FF14",
            "danger": "#FF2D55",
            "warn": "#FFD600",
            "ghost": "#1F2A3F",
            "default": "#39FF14",
        }
        color = QColor(color_map.get(role, "#39FF14"))
        eff = QGraphicsDropShadowEffect(self)
        eff.setOffset(0, 0)
        eff.setBlurRadius(18)
        eff.setColor(color)
        self.setGraphicsEffect(eff)

    # role 변경 시 글로우 색상 재적용 -----------------------------------
    def setRole(self, role: str) -> None:
        self.setProperty("role", role)
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_glow(role)
