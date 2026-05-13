"""네온 스타일 로딩 오버레이.

부모 위젯 위에 반투명 배경 + 회전 링 + 메시지 + 진행 바를 표시.
"""

from __future__ import annotations

from PyQt6.QtCore import QPropertyAnimation, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QLabel, QProgressBar, QVBoxLayout, QWidget)


class _SpinnerDot(QWidget):
    """회전 링 아이콘 (paintEvent 로 직접 렌더링)."""

    def __init__(self, parent=None, diameter: int = 56) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self.setFixedSize(QSize(diameter, diameter))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(40)

    def _tick(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRect(4, 4, self._diameter - 8, self._diameter - 8)

        # 배경 링
        pen = QPen(QColor(31, 42, 63))
        pen.setWidth(4)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)

        # 회전 호
        pen2 = QPen(QColor(0, 212, 255))
        pen2.setWidth(4)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawArc(rect, -self._angle * 16, 90 * 16)

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class LoadingOverlay(QWidget):
    """부모 위젯의 size 를 따라가는 풀-커버 오버레이."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)

        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)

        self._spinner = _SpinnerDot(self)
        self._label = QLabel("", self)
        self._label.setProperty("role", "subtitle")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._progress = QProgressBar(self)
        self._progress.setFixedWidth(360)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)

        v.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._label)
        v.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)

        self.hide()
        parent.installEventFilter(self)

    # ------------------------------------------------------------------
    def show_overlay(self, message: str = "") -> None:
        self._label.setText(message)
        self._cover_parent()
        self.raise_()
        self.show()

    def hide_overlay(self) -> None:
        self.hide()

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        if message:
            self._label.setText(message)
        if total > 0:
            pct = int(done * 100 / total)
            self._progress.setRange(0, 100)
            self._progress.setValue(pct)
        else:
            self._progress.setRange(0, 0)
        self._cover_parent()

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.parent() and event.type().name == "Resize":
            self._cover_parent()
        return super().eventFilter(obj, event)

    def _cover_parent(self) -> None:
        if self.parent() is None:
            return
        p = self.parent()
        self.setGeometry(0, 0, p.width(), p.height())

    # 반투명 검정 배경 ---------------------------------------------------
    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(5, 10, 24, 200))
