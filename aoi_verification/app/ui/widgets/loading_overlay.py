"""로딩 오버레이 — 페이드 인/아웃 + 숨쉬는 스피너 + 결정형/busy 진행바.

부모 위젯 위에 반투명 스크림 + 회전 링 + 메시지 + 진행 바를 표시.
모션(사용자 1순위): 등장은 빠르게→끝에서 감속(ease-out), 퇴장은 더 짧게.
CLAUDE.md 로딩 계약 유지: set_progress(done,total,msg), total>0 결정형(증가 tween·
감소/범위변경 스냅), total≤0 busy, 백그라운드 스레드+시그널로 갱신.
"""

from __future__ import annotations

from PyQt6.QtCore import (QEasingCurve, QElapsedTimer, QEvent, QRect, QSize, Qt,
                          QTimer, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QGraphicsOpacityEffect, QLabel, QProgressBar,
                             QPushButton, QVBoxLayout, QWidget)

from .. import theme
from .. import motion


class _SpinnerDot(QWidget):
    """숨쉬는 회전 링 — 호 길이가 70°↔110° 로 오가며 꼬리 페이드(애플 감성)."""

    def __init__(self, parent=None, diameter: int = 56) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._angle = 0
        self._phase = 0.0
        self.setFixedSize(QSize(diameter, diameter))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        if motion.enabled():
            self._timer.start(16)                       # ~60fps

    def _tick(self) -> None:
        # 회전 속도를 변형 모션 강도로 스케일(높을수록 느리고 웅장) — C12 보완.
        try:
            scale = max(0.3, float(theme.PROFILE.motion_scale))
        except Exception:
            scale = 1.0
        self._angle = (self._angle + 4.2 / scale) % 360
        self._phase = (self._phase + 0.02 / scale) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect(4, 4, self._diameter - 8, self._diameter - 8)
        # 배경 링
        pen = QPen(QColor(theme.LINE))
        pen.setWidth(4)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        # 숨쉬는 호(70°~110°)
        span = 90 + int(20 * math.sin(self._phase * 2 * math.pi))
        pen2 = QPen(QColor(theme.ACCENT))
        pen2.setWidth(4)
        pen2.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen2)
        p.drawArc(rect, -int(self._angle) * 16, span * 16)

    def start(self) -> None:
        if motion.enabled() and not self._timer.isActive():
            self._timer.start(16)

    def stop(self) -> None:
        self._timer.stop()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)


class _BusyStripe(QWidget):
    """무한(busy) 진행 — 폭 24% 세그먼트가 OutQuart 로 가속·감속하며 순환.

    Qt 기본 블록 왕복 대신 꼬리 알파 그라데이션의 '혜성 스윕'."""

    def __init__(self, parent=None, width: int = 360, height: int = 6) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        # 연속 스윕(등속) — 트랙 양끝에서 감속하는 '숨쉬기' 대신 일정 속도(C11).
        self._anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_phase)

    def _on_phase(self, v):
        self._phase = float(v)
        self.update()

    def start(self) -> None:
        if motion.enabled():
            self._anim.stop()
            self._anim.setDuration(motion.dur(1100))   # 변형 모션 강도 반영
            self._anim.start()
        self.update()

    def stop(self) -> None:
        self._anim.stop()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2
        # 트랙
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LINE))
        p.drawRoundedRect(0, 0, w, h, r, r)
        # 세그먼트(혜성) — 정지 시 25% 지점의 짧은 표시.
        seg = int(w * 0.24)
        travel = w + seg
        x = int(self._phase * travel) - seg if motion.enabled() else int(w * 0.25)
        base = QColor(theme.ACCENT)
        # 꼬리 페이드: 3구간 알파.
        for i, frac in enumerate((1.0, 0.55, 0.25)):
            c = QColor(base)
            c.setAlpha(int(255 * frac))
            p.setBrush(c)
            sx = x + int(seg * (i / 3.0))
            sw = int(seg / 3.0) + 1
            p.drawRoundedRect(max(0, sx), 0, min(sw, w - max(0, sx)), h, r, r)


class _Sparkline(QWidget):
    """학습 loss 추이 라인 그래프 (#16)."""

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
        lo, hi = min(self._values), max(self._values)
        rng = max(1e-6, hi - lo)
        pen_grid = QPen(QColor(theme.LINE))
        pen_grid.setWidth(1)
        p.setPen(pen_grid)
        p.drawLine(0, h - 1, w, h - 1)
        path = QPainterPath()
        n = len(self._values)
        for i, val in enumerate(self._values):
            x = int(i / max(1, n - 1) * (w - 4)) + 2
            y = h - 4 - int((val - lo) / rng * (h - 10))
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        pen = QPen(QColor(theme.ACCENT))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawPath(path)


class LoadingOverlay(QWidget):
    """부모 위젯 size 를 따라가는 풀-커버 오버레이 (페이드 인/아웃)."""

    cancel_requested = pyqtSignal()        # #8 중지 버튼 클릭

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)

        # 콘텐츠를 한 컨테이너에 묶어 페이드 시 불투명도를 한 번에 조절.
        self._content = QWidget(self)
        self._content_eff = QGraphicsOpacityEffect(self._content)
        self._content_eff.setOpacity(1.0)
        self._content.setGraphicsEffect(self._content_eff)

        v = QVBoxLayout(self._content)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)

        self._spinner = _SpinnerDot(self._content)
        self._label = QLabel("", self._content)
        self._label.setProperty("role", "subtitle")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._progress = QProgressBar(self._content)
        self._progress.setFixedWidth(360)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("%v / %m")           # 처리 갯수(done / total)
        self._target_val = 0
        # 결정형 부드러운 채움 — QVariantAnimation(OutQuart) 로 프레임 균일.
        self._val_anim = QVariantAnimation(self)
        self._val_anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._val_anim.valueChanged.connect(
            lambda x: self._progress.setValue(int(x)))

        self._busy = _BusyStripe(self._content)
        self._busy.hide()

        self._sparkline = _Sparkline(self._content)
        self._sparkline.hide()

        # #8 중지 버튼 — cancelable=True 로 보여진 작업에서만.
        self._cancel_btn = QPushButton("중지", self._content)
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.setFixedWidth(160)
        self._cancel_btn.setStyleSheet(
            "QPushButton {{ color: {d}; background: {dt};"
            " border: 1px solid {d}; border-radius: 8px; padding: 8px 14px;"
            " font-weight: 700; }}"
            "QPushButton:hover {{ background: {dts}; }}".format(
                d=theme.DANGER, dt=theme.DANGER_TINT_SOFT, dts=theme.DANGER_TINT)
        )
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._cancel_btn.hide()

        v.addWidget(self._spinner, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._label)
        v.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._busy, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._sparkline, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 페이드 상태.
        self._fade = 0.0
        self._hiding = False
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutQuart)
        self._fade_anim.valueChanged.connect(self._on_fade)
        self._fade_anim.finished.connect(self._on_fade_done)

        # 최소 표시 시간 가드 — 초단타 작업이 '깜빡'하지 않게(C9).
        self._shown_elapsed = QElapsedTimer()
        self._show_token = 0
        self._hide_pending = False

        self.hide()
        parent.installEventFilter(self)

    # ------------------------------------------------------------------
    def _on_fade(self, v) -> None:
        self._fade = float(v)
        self._content_eff.setOpacity(self._fade)
        self.update()

    def _on_fade_done(self) -> None:
        if self._hiding:
            self._hiding = False
            self._finish_hide()

    def show_overlay(self, message: str = "", *, cancelable: bool = False) -> None:
        self._label.setText(message)
        self._cancel_btn.setVisible(bool(cancelable))
        self._cover_parent()
        self._spinner.start()
        self.raise_()
        self.show()
        self._hiding = False
        self._hide_pending = False
        self._show_token += 1
        self._shown_elapsed.restart()
        self._fade_anim.stop()
        if motion.enabled():
            self._fade_anim.setStartValue(self._fade)
            self._fade_anim.setEndValue(1.0)
            # 등장: 부드럽게 — 빠른 변형도 하한 130ms 로 '컷' 방지(C9).
            self._fade_anim.setDuration(max(130, motion.dur(160)))
            self._fade_anim.start()
        else:
            self._on_fade(1.0)

    MIN_DISPLAY_MS = 350

    def hide_overlay(self) -> None:
        if not self.isVisible():
            self._finish_hide()
            return
        if not motion.enabled():
            self._finish_hide()
            return
        remaining = self.MIN_DISPLAY_MS - self._shown_elapsed.elapsed()
        token = self._show_token
        if remaining > 0:                      # 아직 최소 표시 시간 전 → 지연 퇴장
            if not self._hide_pending:
                self._hide_pending = True
                QTimer.singleShot(int(remaining),
                                  lambda t=token: self._begin_fade_out(t))
            return
        self._begin_fade_out(token)

    def _begin_fade_out(self, token: int) -> None:
        self._hide_pending = False
        if token != self._show_token or not self.isVisible():
            return                             # 그 사이 새 표시가 시작됨 → 무시
        self._hiding = True
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._fade)
        self._fade_anim.setEndValue(0.0)
        # 퇴장: 입장보다 짧게, 단 하한 110ms 로 '컷' 방지.
        self._fade_anim.setDuration(max(110, motion.dur(120)))
        self._fade_anim.start()

    def _finish_hide(self) -> None:
        self.hide()
        self._val_anim.stop()
        self._busy.stop()
        self._busy.hide()
        self._spinner.stop()
        self._sparkline.hide()
        self._sparkline.clear()
        self._cancel_btn.hide()
        self._fade = 0.0

    def push_sparkline(self, value: float) -> None:
        self._sparkline.append_value(value)
        self._sparkline.show()

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        if message:
            self._label.setText(message)
        if total > 0:
            self._busy.stop()
            self._busy.hide()
            self._progress.show()
            done = max(0, min(int(done), int(total)))
            self._target_val = done
            if self._progress.maximum() != total:      # 단계 전환/총량 변경 → 스냅
                self._val_anim.stop()
                self._progress.setRange(0, total)
                self._progress.setValue(done)
            else:
                cur = self._progress.value()
                if done <= cur:                        # 리셋/감소 → 즉시 스냅
                    self._val_anim.stop()
                    self._progress.setValue(done)
                elif motion.enabled():                 # 증가 → 부드럽게 tween
                    self._val_anim.stop()
                    self._val_anim.setStartValue(int(cur))
                    self._val_anim.setEndValue(done)
                    self._val_anim.setDuration(motion.dur(240))
                    self._val_anim.start()
                else:
                    self._progress.setValue(done)
        else:
            self._val_anim.stop()
            self._progress.hide()                       # busy: 혜성 스윕으로 교체
            self._busy.show()
            self._busy.start()
        self._cover_parent()

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
        self._content.setGeometry(0, 0, p.width(), p.height())

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, g, b, a = theme.SCRIM_RGBA
        p.fillRect(self.rect(), QColor(r, g, b, int(a * self._fade)))
