"""제목 + 설명 + 토글 스위치가 한 줄인 컨트롤 — 켜짐/꺼짐이 한눈에 보이는 on/off.

왜 커스텀 페인트인가: 기본 `QCheckBox` 는 상태가 작은 사각형 하나에만 담겨,
'지금 켜져 있나?'를 멀리서 못 읽는다.  이 위젯은 **행 전체가 클릭영역**(집안 관습)이고
노브 위치·트랙 색으로 상태를 크게 말한다.

색은 전부 ``theme`` 토큰에서 읽으므로 라이트/다크 팔레트를 자동으로 따른다.
노브 이동은 ``motion`` 을 거치므로 '모션 줄이기'·헤드리스에서는 즉시 스냅한다.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import (QEasingCurve, QRectF, QSize, Qt, QVariantAnimation,
                          pyqtSignal)
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                             QWidget)

from .. import theme

_TRACK_W = 48
_TRACK_H = 28
_KNOB_M = 3                      # 트랙 안쪽 여백
# 행 전체가 클릭영역이므로 **행 높이**가 실제 타깃이다 — 설명 없는 스위치(어두운 화면)
# 에서도 WCAG 2.5.8(AA, 24px)을 넉넉히 넘기게 하한을 둔다.
_ROW_MIN_H = 34


class ToggleSwitch(QWidget):
    """접근성 있는 on/off 스위치(트랙 + 노브).  라벨은 :class:`SwitchRow` 가 담당."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0     # 0=off, 1=on (노브 위치)
        self._anim: Optional[QVariantAnimation] = None
        self.setFixedSize(QSize(_TRACK_W, _TRACK_H))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ------------------------------------------------------------------
    def is_on(self) -> bool:
        return self._checked

    def set_on(self, on: bool, *, emit: bool = False, animate: bool = True) -> None:
        on = bool(on)
        if on == self._checked:
            return
        self._checked = on
        self._animate_to(1.0 if on else 0.0, animate=animate)
        if emit:
            self.toggled.emit(on)

    def _animate_to(self, target: float, *, animate: bool = True) -> None:
        from .. import motion
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if not animate or not motion.enabled():
            self._pos = target
            self.update()
            return
        anim = QVariantAnimation(self)
        anim.setStartValue(float(self._pos))
        anim.setEndValue(float(target))
        anim.setDuration(motion.dur(140))
        anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        anim.valueChanged.connect(self._on_tween)
        anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
        self._anim = anim

    def _on_tween(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _toggle(self) -> None:
        self.set_on(not self._checked, emit=True)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._toggle()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.isEnabled():
                self._toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2.0
        on = self._pos

        enabled = self.isEnabled()
        # 트랙 — off 는 중립 면, on 은 강조색. 비활성은 채도를 뺀다.
        # ★ off 트랙을 LINE2 로 칠하면 시트 면에서 1.5~1.7:1 밖에 안 돼 '꺼진 스위치가
        #   안 보인다'(WCAG 1.4.11 실패, 실측).  LINE_STRONG 으로 3:1 을 확보한다.
        if enabled:
            off_c = QColor(theme.LINE_STRONG)
            on_c = QColor(theme.ACCENT)
        else:
            off_c = QColor(theme.LINE)
            on_c = QColor(theme.LINE)
        track = QColor(
            int(off_c.red() + (on_c.red() - off_c.red()) * on),
            int(off_c.green() + (on_c.green() - off_c.green()) * on),
            int(off_c.blue() + (on_c.blue() - off_c.blue()) * on),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        # 포커스 링 — 키보드 사용자가 지금 어디 있는지 보이게.
        if self.hasFocus():
            pen = p.pen()
            p.setBrush(Qt.BrushStyle.NoBrush)
            from PyQt6.QtGui import QPen
            fp = QPen(QColor(theme.FOCUS))
            fp.setWidth(2)
            p.setPen(fp)
            p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), r - 1, r - 1)
            p.setPen(pen)
            p.setPen(Qt.PenStyle.NoPen)

        # 노브
        knob_d = h - 2 * _KNOB_M
        x = _KNOB_M + on * (w - knob_d - 2 * _KNOB_M)
        p.setBrush(QColor(theme.ON_ACCENT if enabled else theme.ELEV))
        p.drawEllipse(QRectF(x, _KNOB_M, knob_d, knob_d))
        p.end()


class SwitchRow(QWidget):
    """제목(+설명) + 스위치가 한 줄.  행 어디를 눌러도 토글된다."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, *, description: str = "",
                 checked: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(_ROW_MIN_H)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        text_host = QWidget(self)
        col = QVBoxLayout(text_host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._title = QLabel(title, text_host)
        self._title.setProperty("role", "cardTitle")
        col.addWidget(self._title)
        self._desc: Optional[QLabel] = None
        if description:
            self._desc = QLabel(description, text_host)
            self._desc.setProperty("role", "muted")
            self._desc.setWordWrap(True)
            col.addWidget(self._desc)
        row.addWidget(text_host, stretch=1)

        self.switch = ToggleSwitch(checked, parent=self)
        self.switch.toggled.connect(self.toggled.emit)
        row.addWidget(self.switch, alignment=Qt.AlignmentFlag.AlignVCenter)

    # ------------------------------------------------------------------
    def is_on(self) -> bool:
        return self.switch.is_on()

    def set_on(self, on: bool, *, emit: bool = False) -> None:
        self.switch.set_on(on, emit=emit)

    def set_description(self, text: str) -> None:
        if self._desc is not None:
            self._desc.setText(text)

    def mousePressEvent(self, event):  # noqa: N802
        """행 전체가 클릭영역 — 라벨을 눌러도 토글."""
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self.switch._toggle()
            event.accept()
            return
        super().mousePressEvent(event)
