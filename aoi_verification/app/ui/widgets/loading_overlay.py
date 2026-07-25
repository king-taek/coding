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
        # ★ 생성자에서 켜지 않는다.  오버레이는 앱 시작 시 1회 생성돼 대부분의 시간을
        #   숨어 있는데, 이전엔 16ms(62.5Hz) 타이머가 그동안 계속 돌았다.
        #   `start()`(= show_overlay)에서만 켠다.

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
        # 배경 링 — ★ LINE 을 쓰면 진행 호(accent)와 1.90:1(라이트)/1.84(다크)라
        # '어디가 진행분인지' 구분이 안 된다.  '모션 줄이기'+busy 면 이게 유일한
        # 신호라 특히 치명적이다.  LINE2 로 3.41/4.09 확보(실측).
        pen = QPen(QColor(theme.LINE2))
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
    """무한(busy) 진행 — 폭 24% 세그먼트가 **등속**으로 순환하는 '혜성 스윕'.

    Qt 기본 블록 왕복 대신 꼬리 알파 그라데이션.  이징은 의도적으로 ``Linear`` 다 —
    끝에서 감속하는 '숨쉬기'는 총량을 모르는 작업에 '거의 끝났다'는 거짓 신호를 준다."""

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
        # 트랙 — 스피너 링과 같은 이유로 LINE2(혜성 머리와 3.41/4.09).
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LINE2))
        p.drawRoundedRect(0, 0, w, h, r, r)
        # 세그먼트(혜성) — 정지 시 25% 지점의 짧은 표시.
        seg = int(w * 0.24)
        travel = w + seg
        x = int(self._phase * travel) - seg if motion.enabled() else int(w * 0.25)
        base = QColor(theme.ACCENT)
        # 꼬리 페이드: 3구간 알파.  ★ 진행 방향(왼→오른)에서 **오른쪽이 머리**다 —
        # 알파를 (1.0, 0.55, 0.25) 순으로 두면 머리가 흐리고 꼬리가 진해져 혜성이
        # 거꾸로 난다(실측 지적).  왼쪽(꼬리)부터 옅게 시작해 오른쪽(머리)에서 진해진다.
        # 꼬리 최저 알파를 0.45 로 — 0.25 는 트랙에 묻혀 꼬리가 사라졌다.
        for i, frac in enumerate((0.45, 0.7, 1.0)):
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

    MIN_DISPLAY_MS = 350                   # 초단타 작업의 '깜빡임' 방지 래치
    RISE_IN_PX = 32                        # 등장: 중앙보다 이만큼 아래에서 시작
    RISE_OUT_PX = 12                       # 퇴장: 살짝만 내려가며 사라진다
    # 불투명도는 짧게(빠르게 나타난다) · 위치는 길게(끝에서 감속하며 안착한다).
    # 두 값이 같으면 '안착'이 페이드에 흡수돼 사용자가 요청한 두 속도가 사라진다.
    FADE_IN_MS = 180
    RISE_IN_MS = 300
    FADE_OUT_MS = 140
    PANEL_W = 424                          # 메시지 길이로 패널 폭이 뛰지 않게 고정

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAutoFillBackground(False)

        # 콘텐츠는 **패널** 안에 모은다 — 스크림을 옅게 해도 읽히고, "화면이 전부
        # 가려진다"는 느낌 대신 초점만 남는다.  패널은 레이아웃이 아니라 직접 배치해
        # (중앙 기준 오프셋) 아래에서 올라오는 모션을 값싸게 만든다.
        self._panel = QWidget(self)
        self._panel.setProperty("role", "loadingPanel")
        self._content = self._panel                    # 이전 이름 유지(내부 참조 호환)
        self._content_eff = QGraphicsOpacityEffect(self._panel)
        self._content_eff.setOpacity(1.0)
        self._panel.setGraphicsEffect(self._content_eff)

        v = QVBoxLayout(self._panel)
        v.setContentsMargins(28, 24, 28, 24)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)

        self._spinner = _SpinnerDot(self._content)
        self._label = QLabel("", self._content)
        self._label.setProperty("role", "subtitle")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 긴 메시지가 패널을 옆으로 늘리지 않게 — 폭을 정하고 줄바꿈한다.
        self._label.setWordWrap(True)
        self._label.setFixedWidth(360)

        self._progress = QProgressBar(self._content)
        self._progress.setFixedWidth(360)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        # ★ 숫자를 바 **안**에 두지 않는다 — 채움(accent)이 글자 아래를 지나는 순간
        #   대비가 2.41(라이트)/1.85(다크)로 붕괴한다(실측).  바 밖 모노 라벨로 옮겨
        #   어떤 진행률에서도 같은 대비를 유지한다.
        self._progress.setTextVisible(False)
        self._count_label = QLabel("", self._content)
        self._count_label.setProperty("role", "progressCount")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._target_val = 0
        # 결정형 부드러운 채움 — QVariantAnimation(OutQuart) 로 프레임 균일.
        # ★ tick 마다 OutQuart 를 걸면 매 갱신이 끝에서 감속해 채움이 절뚝인다.
        #   연속 갱신되는 결정형 바는 **등속**이 맞다(전체 곡선은 작업 속도가 만든다).
        self._val_anim = QVariantAnimation(self)
        self._val_anim.setEasingCurve(QEasingCurve.Type.Linear)
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
        # 진행 표시(결정형 바 / busy 스트라이프)를 한 호스트에 묶어 스태거 페이드를 건다.
        self._bar_host = QWidget(self._panel)
        # ★ 맨 QWidget 은 전역 `QWidget { background-color: $bg }` 를 물려받아 패널 면
        #   ($panel) 위에 색이 다른 띠로 보인다(실측).  투명으로 못 박는다.
        self._bar_host.setProperty("role", "loadingBarHost")
        _bar_lay = QVBoxLayout(self._bar_host)
        _bar_lay.setContentsMargins(0, 0, 0, 0)
        _bar_lay.setSpacing(6)
        _bar_lay.addWidget(self._progress, alignment=Qt.AlignmentFlag.AlignCenter)
        _bar_lay.addWidget(self._count_label, alignment=Qt.AlignmentFlag.AlignCenter)
        _bar_lay.addWidget(self._busy, alignment=Qt.AlignmentFlag.AlignCenter)
        # ★ 여기에 두 번째 QGraphicsOpacityEffect 를 걸지 않는다 — 패널이 이미 이펙트로
        #   렌더되는 중이라 이펙트를 겹치면 "A paint device can only be painted by one
        #   painter at a time" 경고가 난다.  대신 위/아래 여백을 맞바꿔(합은 일정)
        #   패널 크기를 흔들지 않고 살짝 밀려 들어오게 한다.
        self._bar_lay = _bar_lay
        v.addWidget(self._bar_host, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._sparkline, alignment=Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # ★ 불투명도와 **위치를 분리한다.**  하나의 t 로 둘을 함께 몰면 사용자가 요청한
        #   "빠르게 나타나고 마지막에 천천히 도착"이 성립하지 않는다 — 24px 이동의 대부분이
        #   페이드가 끝나기 전에 소진돼 '안착'이 사라진다(실측 지적).  그래서:
        #     · 불투명도 FADE_IN_MS  — 빠르게 나타난다
        #     · 위치     RISE_IN_MS  — 더 길게, 끝에서 감속하며 중앙에 **안착**한다
        #     · 스크림   Linear      — 디밍이 슬램하지 않게 등속
        self._fade = 0.0                   # 패널 불투명도 + 스크림(0..1)
        self._rise = 0.0                   # 위치 진행(0=아래, 1=중앙)
        self._rise_span = self.RISE_IN_PX
        self._hiding = False
        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._fade_anim.valueChanged.connect(self._on_fade)
        self._fade_anim.finished.connect(self._on_fade_done)
        self._rise_anim = QVariantAnimation(self)
        # ★ OutQuart 는 너무 앞에서 소진된다: 32px 이동의 잔여가 t=0.5 에 2.0px,
        #   t=0.6 에 0.8px — 페이드(180ms)가 끝나는 시점에 이미 사실상 도착해 있어
        #   사용자가 말한 **두 번째 속도('마지막에 천천히 도착')가 화면에 없다**(실측).
        #   OutQuad 로 낮추면 페이드 종료 시점에 5~6px 이 남아 안착이 눈에 보인다.
        self._rise_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._rise_anim.valueChanged.connect(self._on_rise)

        # 최소 표시 시간 가드 — 초단타 작업이 '깜빡'하지 않게(C9).
        self._shown_elapsed = QElapsedTimer()
        self._show_token = 0
        self._hide_pending = False

        self.hide()
        parent.installEventFilter(self)

    # ------------------------------------------------------------------
    def _on_fade(self, v) -> None:
        self._fade = float(v)
        if self._content_eff is not None:
            self._content_eff.setOpacity(self._fade)
        self.update()                     # 스크림 알파

    def _on_rise(self, v) -> None:
        self._rise = float(v)
        self._place_panel()

    def _on_fade_done(self) -> None:
        if self._hiding:
            self._hiding = False
            self._finish_hide()
            return
        # ★ 등장이 끝나면 그래픽 이펙트를 뗀다 — 이펙트가 걸려 있는 동안 스피너가
        #   1프레임 돌 때마다 패널 **전체**가 오프스크린으로 다시 렌더된다(실측 지적).
        #   불투명도가 1.0 이라 시각적으로 달라지는 것은 없다.
        self._detach_effect()

    def _detach_effect(self) -> None:
        if self._content_eff is None:
            return
        self._panel.setGraphicsEffect(None)
        self._content_eff = None

    def _attach_effect(self) -> None:
        """페이드를 걸기 직전에만 이펙트를 설치한다."""
        if self._content_eff is not None:
            return
        eff = QGraphicsOpacityEffect(self._panel)
        eff.setOpacity(self._fade)
        self._panel.setGraphicsEffect(eff)
        self._content_eff = eff

    def show_overlay(self, message: str = "", *, cancelable: bool = False) -> None:
        self._label.setText(message)
        self._cancel_btn.setVisible(bool(cancelable))
        self._spinner.start()
        self.raise_()
        self.show()
        self._hiding = False
        self._hide_pending = False
        self._show_token += 1
        self._shown_elapsed.restart()
        self._fade_anim.stop()
        self._rise_anim.stop()
        self._rise_span = self.RISE_IN_PX
        if motion.enabled():
            # 중앙보다 조금 아래 + 투명에서 시작해 중앙에 안착.
            self._attach_effect()
            self._on_fade(0.0)
            self._on_rise(0.0)
            self._fade_anim.setStartValue(0.0)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.setDuration(motion.dur(self.FADE_IN_MS))
            self._fade_anim.start()
            # 위치는 더 길게 — 페이드가 끝난 뒤에도 남은 거리를 감속하며 좁힌다.
            self._rise_anim.setStartValue(0.0)
            self._rise_anim.setEndValue(1.0)
            self._rise_anim.setDuration(motion.dur(self.RISE_IN_MS))
            self._rise_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
            self._rise_anim.start()
            self._stagger_bar()
        else:
            self._detach_effect()
            self._on_fade(1.0)
            self._on_rise(1.0)
            self._set_bar_slide(1.0)
        self._cover_parent()

    _BAR_SLIDE_PX = 8
    # 패널이 안착한 **뒤에** 들어와야 계층이 순서대로 읽힌다.
    # ★ 비교 대상은 페이드(FADE_IN_MS)가 아니라 **위치 안착**(RISE_IN_MS)이다.  60→110 으로
    #   올렸을 때도 여전히 먼저 도착했다(88+128=216 vs 안착 240) — 페이드와 비교한
    #   계산이 두 축 분리를 반영하지 못했기 때문.  지연 + 자기 지속시간이 안착보다
    #   뒤가 되도록 잡는다(test_loading_panel 이 이 부등식을 고정한다).
    BAR_STAGGER_MS = 210
    BAR_SLIDE_MS = 160

    def _set_bar_slide(self, t: float) -> None:
        """t=0 → 아래로 _BAR_SLIDE_PX 밀린 상태, t=1 → 제자리.

        위/아래 여백의 **합을 일정하게** 유지하므로 패널 크기가 흔들리지 않는다."""
        lay = getattr(self, "_bar_lay", None)
        if lay is None:
            return
        t = max(0.0, min(1.0, float(t)))
        top = int(round(self._BAR_SLIDE_PX * (1.0 - t)))
        lay.setContentsMargins(0, top, 0, self._BAR_SLIDE_PX - top)

    def _stagger_bar(self) -> None:
        """진행바는 패널이 안착한 뒤 살짝 늦게 들어온다 — 계층이 순서대로 읽히게."""
        anim = getattr(self, "_bar_anim", None)
        if anim is not None:
            anim.stop()
        self._set_bar_slide(0.0)
        token = self._show_token

        def _run(t=token):
            if t != self._show_token or self._hiding:
                return
            a = QVariantAnimation(self)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setDuration(max(120, motion.dur(self.BAR_SLIDE_MS)))
            a.setEasingCurve(QEasingCurve.Type.OutQuart)
            a.valueChanged.connect(lambda v: self._set_bar_slide(float(v)))
            a.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
            self._bar_anim = a

        QTimer.singleShot(max(1, motion.dur(self.BAR_STAGGER_MS)), _run)

    def hide_overlay(self) -> None:
        if not self.isVisible():
            self._finish_hide()
            return
        # ★ 최소 표시 래치는 **모션이 아니라 타이밍 위생**이다 — 이 검사를
        #   `motion.enabled()` 안쪽에 두면 '모션 줄이기' 사용자만 깜빡임을 그대로 받는다
        #   (모션에 민감해서 끈 사람에게 정확히 깜빡임을 주는 셈).  게이트 밖에 둔다.
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
        if not motion.enabled():
            self._finish_hide()                # 래치는 지켰으니 이제 즉시 종료
            return
        self._hiding = True
        self._rise_span = self.RISE_OUT_PX     # 퇴장은 살짝만 내려간다
        self._attach_effect()
        dur = max(110, motion.dur(self.FADE_OUT_MS))
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._fade)
        self._fade_anim.setEndValue(0.0)
        # 퇴장: 입장보다 짧게, 단 하한 110ms 로 '컷' 방지.
        self._fade_anim.setDuration(dur)
        self._fade_anim.start()
        # 퇴장은 위치·불투명도를 같이 몰아도 된다(짧고 얕아 '안착'이 없다).
        self._rise_anim.stop()
        self._rise_anim.setStartValue(self._rise)
        self._rise_anim.setEndValue(0.0)
        self._rise_anim.setDuration(dur)
        self._rise_anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self._rise_anim.start()

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
        self._rise_span = self.RISE_IN_PX      # 다음 등장을 위해 초기화
        self._set_bar_slide(1.0)

    def push_sparkline(self, value: float) -> None:
        self._sparkline.append_value(value)
        self._sparkline.show()

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        if message:
            self._label.setText(message)
        was_busy = not self._busy.isHidden()
        mode_changed = (total > 0) == was_busy
        if total > 0:
            self._busy.stop()
            self._busy.hide()
            self._progress.show()
            done = max(0, min(int(done), int(total)))
            self._target_val = done
            # ★ hide 하지 않는다 — 자리를 예약해 두면 busy↔결정형 전환에 패널 높이가
            #   뛰지 않는다(이전 36px 점프 → 중앙 정렬이라 상단이 18px 즉시 튀었다).
            self._count_label.setText(f"{done} / {int(total)}")
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
            self._count_label.setText("")               # 총량을 모르니 숫자는 비운다
            self._busy.show()
            self._busy.start()
        # ★ 매 tick 마다 _cover_parent() 를 부르지 않는다 — sizeHint + setGeometry 가
        #   진행 갱신 횟수만큼 돌았다(200 tick = 200회).  크기는 부모 리사이즈
        #   (eventFilter)와 표시 시점에만 바뀐다.  단, busy↔결정형 전환은 내용이
        #   바뀌므로 그때만 다시 배치한다.
        if mode_changed:
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
        self._place_panel()

    def _place_panel(self) -> None:
        """패널을 화면 중앙에 두고, 위치 진행도만큼 아래에서 끌어올린다.

        폭은 :data:`PANEL_W` 로 고정한다 — sizeHint 를 쓰면 메시지 길이나 busy↔결정형
        전환마다 패널 폭·높이가 튀어 '같은 자리에 있는 하나의 패널'로 읽히지 않는다."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        hint = self._panel.sizeHint()
        pw = min(max(self.PANEL_W, hint.width()), max(1, w - 48))
        ph = min(hint.height(), max(1, h - 48))
        offset = int(round(self._rise_span * (1.0 - self._rise)))
        self._panel.setGeometry((w - pw) // 2, (h - ph) // 2 + offset, pw, ph)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r, g, b, a = theme.SCRIM_RGBA
        p.fillRect(self.rect(), QColor(r, g, b, int(a * self._fade)))
