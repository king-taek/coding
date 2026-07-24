"""공용 버튼 — role 속성으로 QSS 가 스타일링, 글로우는 primary 전용."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QPushButton

from .. import theme


class NeonButton(QPushButton):
    """role 속성 기반 색상 분기.  주요 액션(primary)만 은은한 앰버 글로우."""

    def __init__(self,
                 text: str = "",
                 role: str = "default",
                 parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_glow(role)
        self.setMinimumHeight(theme.PROFILE.control_h)

    # ------------------------------------------------------------------
    def _apply_glow(self, role: str) -> None:
        # 절제 원칙: 화면의 강조는 primary 하나 — 나머지는 글로우 없음.
        # 변형이 글로우를 끄면(밝은/무광 테마) primary 도 글로우 없음.
        if role != "primary" or not theme.PROFILE.primary_glow:
            self.setGraphicsEffect(None)
            return
        color = QColor(theme.ACCENT)
        color.setAlpha(120)
        eff = QGraphicsDropShadowEffect(self)
        eff.setOffset(0, 0)
        eff.setBlurRadius(14)
        eff.setColor(color)
        self.setGraphicsEffect(eff)

    # role 변경 시 글로우 색상 재적용 -----------------------------------
    def setRole(self, role: str) -> None:
        self.setProperty("role", role)
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_glow(role)
