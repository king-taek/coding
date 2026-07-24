"""공용 버튼 — role 속성으로 QSS 가 스타일링, 글로우는 primary 전용."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
                             QPushButton)

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
        self._glow_eff = None
        self._press_anim = None
        self._press_opacity_eff = None
        self._apply_glow(role)
        self.setMinimumHeight(theme.PROFILE.control_h)

    # ------------------------------------------------------------------
    def _apply_glow(self, role: str) -> None:
        # 절제 원칙: 화면의 강조는 primary 하나 — 나머지는 글로우 없음.
        # 변형이 글로우를 끄면(밝은/무광 테마) primary 도 글로우 없음.
        if role != "primary" or not theme.PROFILE.primary_glow:
            self._glow_eff = None
            self.setGraphicsEffect(None)
            return
        color = QColor(theme.ACCENT)
        color.setAlpha(120)
        eff = QGraphicsDropShadowEffect(self)
        eff.setOffset(0, 0)
        eff.setBlurRadius(14)
        eff.setColor(color)
        self.setGraphicsEffect(eff)
        self._glow_eff = eff

    def _tween_glow(self, target: int) -> None:
        """primary 프레스/릴리즈 시 글로우 blurRadius 를 부드럽게(레이아웃 불변)."""
        from .. import motion
        if self._glow_eff is None or not motion.enabled():
            return
        if self._press_anim is not None:
            self._press_anim.stop()
        anim = QPropertyAnimation(self._glow_eff, b"blurRadius", self)
        anim.setEndValue(target)
        anim.setDuration(motion.dur(120))
        anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        anim.start()
        self._press_anim = anim

    def _press_feedback(self, pressed: bool) -> None:
        """프레스 촉각 피드백 — 글로우 변형은 blur 트윈, 그 외는 미세 불투명도 딥(C13)."""
        from .. import motion
        if not motion.enabled():
            return
        if self._glow_eff is not None:
            self._tween_glow(6 if pressed else 14)
            return
        # 글로우 없는 변형: opacity 1.0↔0.92 딥(레이아웃 불변, QSS 색은 유지).
        if self._press_opacity_eff is None:
            self._press_opacity_eff = QGraphicsOpacityEffect(self)
            self._press_opacity_eff.setOpacity(1.0)
            self.setGraphicsEffect(self._press_opacity_eff)
        if self._press_anim is not None:
            self._press_anim.stop()
        anim = QPropertyAnimation(self._press_opacity_eff, b"opacity", self)
        anim.setEndValue(0.92 if pressed else 1.0)
        anim.setDuration(motion.dur(90))
        anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        anim.start()
        self._press_anim = anim

    def mousePressEvent(self, e):  # noqa: N802
        self._press_feedback(True)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._press_feedback(False)
        super().mouseReleaseEvent(e)

    # role 변경 시 글로우 색상 재적용 -----------------------------------
    def setRole(self, role: str) -> None:
        self.setProperty("role", role)
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_glow(role)
