"""네온 스타일 로딩 오버레이.

부모 위젯 위에 반투명 배경 + 회전 링 + 메시지 + 진행 바를 표시.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QLabel, QProgressBar, QPushButton, QVBoxLayout,
                             QWidget)


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


class _Sparkline(QWidget):
    """학습 loss 추이를 보여주는 작은 라인 그래프 (#16)."""

    def __init__(self, parent=None, width: int = 360, height: int = 48) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._values: list[float] = []

    def set_values(self, values: list[float]) -> None:
        self._values = list(values)
        self.update()

    def append_value(self, value: float, *, max_keep: int = 64) -> None:
        self._values.append(float(value))
        if len(self._values) > max_keep:
            del self._values[: len(self._values) - max_keep]
        self.update()

    def clear(self) -> None:
        self._values.clear()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        if not self._values:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        lo = min(self._values)
        hi = max(self._values)
        rng = max(1e-6, hi - lo)

        # 가이드 라인
        pen_grid = QPen(QColor(31, 42, 63))
        pen_grid.setWidth(1)
        p.setPen(pen_grid)
        p.drawLine(0, h - 1, w, h - 1)

        # 라인
        path = QPainterPath()
        n = len(self._values)
        for i, v in enumerate(self._values):
            x = int(i / max(1, n - 1) * (w - 4)) + 2
            y = h - 4 - int((v - lo) / rng * (h - 10))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        pen = QPen(QColor(0, 212, 255))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(path)


class LoadingOverlay(QWidget):
    """부모 위젯의 size 를 따라가는 풀-커버 오버레이."""

    cancel_requested = pyqtSignal()        # #8 중지 버튼 클릭

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
        self._progress.setTextVisible(True)
        self._progress.setFormat("%p%")
        # 진행 바 부드러운 채움 — 목표치(%)로 매끄럽게 tween (ease-out).
        self._target_pct = 0
        self._anim = QTimer(self)
        self._anim.setInterval(16)            # ~60fps
        self._anim.timeout.connect(self._tween_step)

        self._sparkline = _Sparkline(self)
        self._sparkline.hide()

        # #8 중지 버튼 — cancelable=True 로 보여진 작업에서만 노출.
        self._cancel_btn = QPushButton("중지", self)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setFixedWidth(160)
        self._cancel_btn.setStyleSheet(
            "QPushButton { color: #FFD6D6; background: rgba(255,45,85,0.18);"
            " border: 1px solid #FF2D55; border-radius: 8px; padding: 8px 14px;"
            " font-weight: 700; }"
            "QPushButton:hover { background: rgba(255,45,85,0.30); }"
        )
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._cancel_btn.hide()

        v.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._label)
        v.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._sparkline, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.hide()
        parent.installEventFilter(self)

    # ------------------------------------------------------------------
    def show_overlay(self, message: str = "", *, cancelable: bool = False) -> None:
        self._label.setText(message)
        self._cancel_btn.setVisible(bool(cancelable))
        self._cover_parent()
        self.raise_()
        self.show()

    def hide_overlay(self) -> None:
        self.hide()
        self._anim.stop()
        self._sparkline.hide()
        self._sparkline.clear()
        self._cancel_btn.hide()

    def push_sparkline(self, value: float) -> None:
        """학습 진행 중 매 에폭마다 loss 값을 추가 (#16)."""
        self._sparkline.append_value(value)
        self._sparkline.show()

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        if message:
            self._label.setText(message)
        if total > 0:
            pct = max(0, min(100, int(done * 100 / total)))
            if self._progress.maximum() == 0:        # 무한(busy) → 확정 바로 복귀
                self._progress.setRange(0, 100)
            self._target_pct = pct
            cur = self._progress.value()
            if pct <= cur:                            # 리셋/감소 → 즉시 스냅
                self._anim.stop()
                self._progress.setValue(pct)
            elif not self._anim.isActive():           # 증가 → 부드럽게 tween
                self._anim.start()
        else:
            self._anim.stop()
            self._progress.setRange(0, 0)             # 무한 진행(busy)
        self._cover_parent()

    def _tween_step(self) -> None:
        cur = self._progress.value()
        if cur >= self._target_pct:
            self._progress.setValue(self._target_pct)
            self._anim.stop()
            return
        gap = self._target_pct - cur
        cur += max(1, gap // 4)                       # ease-out: 남은 격차의 1/4
        self._progress.setValue(min(cur, self._target_pct))

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
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
