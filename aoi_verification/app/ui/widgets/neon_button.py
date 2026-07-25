"""공용 버튼 — role 속성으로 QSS 가 스타일링, 프레스 시 미세 불투명도 딥."""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QPushButton

from .. import theme


class NeonButton(QPushButton):
    """role 속성 기반 색상 분기(QSS).  색/보더는 전부 style.qss 가 칠한다.

    '도면' 은 무광 제도 시트라 글로우를 쓰지 않는다 — 프레스 피드백은 레이아웃을
    건드리지 않는 미세 불투명도 딥으로.
    """

    def __init__(self,
                 text: str = "",
                 role: str = "default",
                 parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("role", role)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._press_anim = None
        self._press_eff = None
        self.setMinimumHeight(theme.PROFILE.control_h)

    # ------------------------------------------------------------------
    def _press_feedback(self, pressed: bool) -> None:
        """프레스 촉각 피드백 — opacity 1.0↔0.92 딥(레이아웃 불변)."""
        from .. import motion
        if not motion.enabled():
            return
        if self._press_eff is None:
            self._press_eff = QGraphicsOpacityEffect(self)
            self._press_eff.setOpacity(1.0)
            self.setGraphicsEffect(self._press_eff)
        if self._press_anim is not None:
            self._press_anim.stop()
        anim = QPropertyAnimation(self._press_eff, b"opacity", self)
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

    # role 변경 시 QSS 재적용 ---------------------------------------------
    def setRole(self, role: str) -> None:
        self.setProperty("role", role)
        self.style().unpolish(self)
        self.style().polish(self)
