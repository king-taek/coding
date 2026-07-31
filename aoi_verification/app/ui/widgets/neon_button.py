"""공용 버튼 — role 속성으로 QSS 가 스타일링, 프레스 시 미세 불투명도 딥."""

from __future__ import annotations

from PyQt6.QtCore import QPropertyAnimation, Qt
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
        anim.setEasingCurve(motion.EASE_PRIMARY)
        if not pressed:
            anim.finished.connect(self._clear_press_effect)
        anim.start()
        self._press_anim = anim

    def _clear_press_effect(self) -> None:
        """딥이 1.0 으로 돌아왔으면 이펙트를 **떼어 낸다**(#렉).

        그래픽 이펙트가 붙은 위젯은 값이 1.0(무변화)이어도 다시 그릴 때마다 오프스크린
        픽스맵을 거친다.  떼지 않으면 세션 동안 '한 번이라도 누른 모든 버튼'이 영구히
        느린 경로로 남는다.  다음 프레스에서 lazy 로 다시 만들어지므로 딥은 그대로다.
        (중간에 다시 눌려 값이 1.0 이 아니면 손대지 않는다 — 진행 중인 딥을 지우면
        버튼이 깜빡인다.)"""
        eff = self._press_eff
        if eff is None:
            return
        try:
            if eff.opacity() < 0.999:
                return
            self.setGraphicsEffect(None)     # 이펙트 소유권은 위젯 → 여기서 파괴된다
        except RuntimeError:
            pass
        self._press_eff = None
        self._press_anim = None

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
