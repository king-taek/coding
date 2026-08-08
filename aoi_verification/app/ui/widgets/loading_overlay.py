"""로딩 오버레이 — 페이드 인/아웃 + 숨쉬는 스피너 + 결정형/busy 진행바.

부모 위젯 위에 반투명 스크림 + 회전 링 + 메시지 + 진행 바를 표시.
모션(사용자 1순위): 등장은 빠르게→끝에서 감속(ease-out), 퇴장은 더 짧게.
CLAUDE.md 로딩 계약 유지: set_progress(done,total,msg), total>0 결정형, total≤0 busy,
백그라운드 스레드+시그널로 갱신.  결정형 채움 규칙:

- 감소/총량 변경 → **즉시 스냅**
- **완료**(done ≥ total) → **즉시 스냅**.  마지막 증가를 tween 하면 오버레이가 내려가며
  잘려 끝까지 찬 적이 없게 된다.
- **돌고 있는 tween** → 재시작하지 않고 **즉시 스냅**(재시작이 '따라가지 못함'의 기계다).
- **촘촘한** 증가(간격 < VAL_TWEEN_MS) → **즉시 스냅**.
- 그 밖의 **드문** 증가 → 부드럽게 tween.
- 퇴장/숨김 직전에는 `_settle_progress()` 로 **목표값을 확정**한 뒤 멈춘다 — 멈춘 tween 의
  중간값이 마지막 프레임으로 남지 않게(set_progress 주석에 실측값과 이유가 있다).
"""

from __future__ import annotations

from PyQt6.QtCore import (QEasingCurve, QElapsedTimer, QEvent, QRect, QSize, Qt,
                          QTimer, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QLabel,
                             QProgressBar, QVBoxLayout, QWidget)

from ... import i18n
from .. import theme
from .. import motion
from .neon_button import NeonButton


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


class LoadingOverlay(QWidget):
    """부모 위젯 size 를 따라가는 풀-커버 오버레이 (페이드 인/아웃)."""

    cancel_requested = pyqtSignal()        # #8 중지 버튼 클릭

    MIN_DISPLAY_MS = 350                   # 초단타 작업의 '깜빡임' 방지 래치
    RISE_IN_PX = 32                        # 등장: 중앙보다 이만큼 아래에서 시작
    RISE_OUT_PX = 12                       # 퇴장: 살짝만 내려가며 사라진다
    # 불투명도는 짧게(빠르게 나타난다) · 위치는 길게(끝에서 감속하며 안착한다).
    # 두 값이 같으면 '안착'이 페이드에 흡수돼 사용자가 요청한 두 속도가 사라진다.
    # ★ 등장 전체 길이 = RISE_IN_MS.  사용자가 **실측 500ms 로 지정**했으므로
    #   `motion.DUR_LOADING` 을 그대로 쓰고 `motion.dur()` 스케일을 태우지 않는다
    #   (0.8 을 곱하면 지정값 500 이 400 으로 나가 '정한 값'과 어긋난다).
    #   페이드는 옛 비율(180/300 = 0.6)을 유지해 두 속도가 그대로 살아 있게 한다.
    RISE_IN_MS = motion.DUR_LOADING              # 500
    FADE_IN_MS = int(RISE_IN_MS * 0.6)           # 300
    FADE_OUT_MS = 140
    PANEL_W = 424                          # 메시지 길이로 패널 폭이 뛰지 않게 고정
    # 결정형 바의 부드러운 채움 지속시간 **이자** '촘촘한 갱신' 판정 기준.
    # 두 값을 따로 두면 어긋난다 — 하나로 묶어 둔다(set_progress 주석 참조).
    VAL_TWEEN_MS = 240

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
        # 결정형 갱신 **간격** 측정 — 촘촘하면 tween 을 건너뛴다.
        self._val_gap = QElapsedTimer()
        # 결정형 부드러운 채움 — QVariantAnimation(OutQuart) 로 프레임 균일.
        # ★ tick 마다 OutQuart 를 걸면 매 갱신이 끝에서 감속해 채움이 절뚝인다.
        #   연속 갱신되는 결정형 바는 **등속**이 맞다(전체 곡선은 작업 속도가 만든다).
        self._val_anim = QVariantAnimation(self)
        self._val_anim.setEasingCurve(QEasingCurve.Type.Linear)
        # ★ **람다로 연결하지 않는다.**  PyQt 는 슬롯이 QObject 의 **바인드 메서드**일 때
        #   그 객체가 파괴되면 연결을 자동으로 끊는다.  람다는 `self` 를 클로저에 담아
        #   receiver 를 식별할 수 없으므로 연결이 살아남고, 오버레이가 파괴된 뒤에도
        #   애니메이션 tick 이 **죽은 C++ 객체로** 들어간다 — 파이썬 예외가 아니라
        #   세그폴트다(전체 테스트에서 실측: 애니메이션이 도는 중 오버레이를 지우면 죽었다).
        self._val_anim.valueChanged.connect(self._on_val_tick)

        self._busy = _BusyStripe(self._content)
        self._busy.hide()

        # #8 중지 버튼 — cancelable=True 로 보여진 작업에서만.
        # ★ 인스턴스 스타일시트로 색을 굽지 않는다.  오버레이는 앱 시작 때 한 번 만들어지고
        #   `_recolor_in_place` 는 인스턴스 스타일시트를 못 바꾸므로, 구운 라이트 팔레트가
        #   다크 전환 뒤에도 그대로 남아 글자 대비가 2.19:1 로 무너졌다(실측 캡처).
        #   role 로 옮기면 전역 QSS 가 다시 렌더되면서 두 모드를 모두 따라온다.
        self._cancel_btn = NeonButton(i18n.KO.BTN_STOP, role="danger",
                                      parent=self._content)
        self._cancel_btn.setFixedWidth(160)
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
        # 진행바 스태거 — ★ 위 두 애니메이션과 **같은 방식**으로 여기서 한 번만 만든다.
        #   이전엔 `_stagger_bar` 가 표시마다 새로 만들면서 `start(DeleteWhenStopped)` 로
        #   Qt 에 삭제를 맡기고도 `self._bar_anim` 에 핸들을 남겼다.  370ms 뒤 C++ 객체가
        #   삭제되면 **두 번째** show_overlay 의 `anim.stop()` 이
        #   "wrapped C/C++ object ... has been deleted" 를 내고, PyQt6 가 슬롯 안의
        #   미처리 예외를 qFatal() 로 처리해 앱이 죽었다(실패목록 두 번째 클릭 크래시).
        self._bar_anim = QVariantAnimation(self)
        self._bar_anim.setEasingCurve(motion.EASE_PRIMARY)
        self._bar_anim.setStartValue(0.0)
        self._bar_anim.setEndValue(1.0)
        self._bar_anim.valueChanged.connect(self._on_bar_tick)   # 위와 같은 이유로 바인드 메서드
        # ★ 지연 실행은 `QTimer.singleShot` **정적 호출로 하지 않는다.**  정적 타이머는
        #   위젯의 자식이 아니라서, 오버레이가 지연 시간 안에 파괴되면 **죽은 위젯으로**
        #   콜백이 들어간다 — 파이썬 예외가 아니라 세그폴트가 난다(실측: 로딩을 띄운
        #   다이얼로그를 210ms 안에 닫으면 프로세스가 죽었다).  `self` 를 부모로 둔
        #   QTimer 는 위젯과 함께 파괴되므로 그 뒤 발화 자체가 불가능하다.
        self._bar_timer = QTimer(self)
        self._bar_timer.setSingleShot(True)
        self._bar_timer.timeout.connect(self._run_bar_slide)
        self._hide_timer = QTimer(self)          # 최소 표시 래치 — 같은 이유로 부모 있음
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_latch)

        # 최소 표시 시간 가드 — 초단타 작업이 '깜빡'하지 않게(C9).
        self._shown_elapsed = QElapsedTimer()
        self._show_token = 0
        self._hide_pending = False
        self._input_locked = False       # 떠 있는 동안만 앱 전역 키를 막는다

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

    def _on_val_tick(self, v) -> None:
        self._progress.setValue(int(v))

    def _on_bar_tick(self, v) -> None:
        self._set_bar_slide(float(v))

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

    def _settle_progress(self) -> None:
        """돌던 채움 tween 을 **목표값으로 확정한 뒤** 멈춘다 — 퇴장/숨김 직전에 부른다.

        그냥 ``stop()`` 하면 tween 의 **중간값**이 마지막으로 보이는 프레임이 된다.
        퇴장 페이드가 110~160ms 이라 그 사이 바가 목표에 못 닿은 채 사라진다 — 사용자가
        본 "가끔 바가 안 채워짐"의 나머지 절반이다.  마지막으로 보고된 값(`_target_val`)
        이 곧 진실이므로 그것으로 맞춘 뒤 멈춘다."""
        self._val_anim.stop()
        if not self._progress.isHidden():
            self._progress.setValue(int(self._target_val))

    def _enter_busy(self) -> None:
        """총량을 모르는 상태 — 결정형 바를 치우고 혜성 스윕을 돌린다.

        ★ 결정형 바를 **0 에 세워 두지 않는다.**  이전에는 ``show_overlay`` 가
        `_progress`(range 0..100, value 0) 를 보이는 채로 두고 `_busy` 를 숨겼는데,
        `set_progress` 를 부르지 않는 호출부(OpenVINO 설치·KLA 파일명 읽기·선계산 대기
        등)에서는 **스피너만 돌고 바는 영원히 0** 이었다 — 사용자가 본 "동그라미만
        돌고 바가 채워지지 않는" 그 증상이고, CLAUDE.md 로딩 계약("진행량을 모를 때도
        0 에 멈추지 말고 busy 를 띄운다")을 정면으로 어긴다."""
        self._val_anim.stop()
        self._val_gap.invalidate()         # 다음 결정형의 첫 갱신은 '드문 것'으로 본다
        self._progress.hide()
        self._count_label.setText("")      # 총량을 모르니 숫자는 비운다
        self._busy.show()
        self._busy.start()

    def show_overlay(self, message: str = "", *, cancelable: bool = False) -> None:
        self._label.setText(message)
        self._set_input_lock(True)
        self._cancel_btn.setVisible(bool(cancelable))
        # busy 로 시작한다 — 첫 set_progress(done, total>0) 이 결정형으로 승격시킨다.
        self._enter_busy()
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
            self._fade_anim.setDuration(self.FADE_IN_MS)
            self._fade_anim.start()
            # 위치는 더 길게 — 페이드가 끝난 뒤에도 남은 거리를 감속하며 좁힌다.
            self._rise_anim.setStartValue(0.0)
            self._rise_anim.setEndValue(1.0)
            self._rise_anim.setDuration(self.RISE_IN_MS)
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
        self._bar_anim.stop()
        self._set_bar_slide(0.0)
        self._bar_token = self._show_token
        self._bar_timer.start(max(1, motion.dur(self.BAR_STAGGER_MS)))

    def _run_bar_slide(self) -> None:
        """스태거 타이머 발화 — 그 사이 새 표시/퇴장이 있었으면 무시한다."""
        if getattr(self, "_bar_token", -1) != self._show_token or self._hiding:
            return
        self._bar_anim.stop()
        self._bar_anim.setDuration(max(120, motion.dur(self.BAR_SLIDE_MS)))
        self._bar_anim.start()

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
                self._hide_token = token
                self._hide_timer.start(int(remaining))
            return
        self._begin_fade_out(token)

    def _on_hide_latch(self) -> None:
        self._begin_fade_out(getattr(self, "_hide_token", self._show_token))

    def _begin_fade_out(self, token: int) -> None:
        self._hide_pending = False
        if token != self._show_token or not self.isVisible():
            return                             # 그 사이 새 표시가 시작됨 → 무시
        if not motion.enabled():
            self._finish_hide()                # 래치는 지켰으니 이제 즉시 종료
            return
        self._hiding = True
        # 페이드아웃 동안 보이는 바는 **마지막으로 보고된 값**이어야 한다.
        self._settle_progress()
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
        # ★ 잠금은 **화면이 실제로 밝아지는 순간** 푼다 — `hide_overlay` 에서 풀면
        #   최소 표시 래치·페이드아웃 동안(수백 ms) 화면은 아직 어두운데 키는 이미
        #   통하는 구간이 생긴다.  그게 바로 고치려던 그 증상이다.
        #   `_finish_hide` 는 모든 퇴장 경로가 지나는 단 하나의 지점이다.
        self._set_input_lock(False)
        self._settle_progress()        # 멈춘 tween 의 중간값이 남지 않게(모션 off 경로 포함)
        self.hide()
        self._bar_anim.stop()          # 숨은 뒤 tick 이 남아 여백을 흔들지 않게
        self._bar_timer.stop()         # 대기 중인 스태거도 취소(숨은 뒤 들어오지 않게)
        self._busy.stop()
        self._busy.hide()
        self._spinner.stop()
        self._cancel_btn.hide()
        self._fade = 0.0
        self._rise_span = self.RISE_IN_PX      # 다음 등장을 위해 초기화
        self._set_bar_slide(1.0)

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
                self._val_gap.start()                  # 이 시점부터 간격을 잰다
            else:
                cur = self._progress.value()
                # ★ tween 은 **예외**다 — 기본은 정확한 위치(스냅)이고, 아래 세 조건을
                #   모두 피한 '드문 증가'만 부드럽게 채운다.  진행률은 장식이 아니라
                #   정보이므로, 부드러움과 정확함이 부딪히면 정확함이 이긴다.
                #
                #   (1) 완료(`done >= total`)는 항상 스냅.  마지막 증가를 tween 으로 걸면
                #       작업이 끝나 오버레이가 내려가면서 `_finish_hide` 의 stop() 에 잘려
                #       **끝까지 찬 적이 없다**(실측: 400ms 간격 5칸 작업에서 4/5 로 종료).
                #       완료 뒤에는 부드러워야 할 것이 없다.
                #   (2) **돌고 있는 tween 은 재시작하지 않는다.**  재시작이 곧 '따라가지
                #       못함'의 기계다 — 매번 몇 프레임만 돌고 처음으로 밀린다(실측,
                #       고치기 전: 30ms 간격 50회에서 5/50→0% · 15/50→20% · 50/50→88%).
                #       간격 측정과 달리 이 조건은 **타이밍에 의존하지 않는 불변식**이라
                #       불규칙한 갱신에서도, `processEvents()` 로 도는 호출부(실패 사진
                #       재계산 — tween 이 이벤트 루프를 거의 못 받는다)에서도 성립한다.
                #   (3) 촘촘한 갱신(간격 < tween 지속시간)도 스냅.  판정 기준을 지속시간
                #       그 자체로 둔다(값이 하나여야 어긋나지 않는다).
                gap = self._val_gap.restart() if self._val_gap.isValid() else None
                dense = gap is not None and gap < motion.dur(self.VAL_TWEEN_MS)
                running = self._val_anim.state() != self._val_anim.State.Stopped
                if (done <= cur                        # 리셋/감소
                        or done >= int(total)          # (1) 완료
                        or running                     # (2) 진행 중 tween
                        or dense                       # (3) 촘촘
                        or not motion.enabled()):
                    self._val_anim.stop()
                    self._progress.setValue(done)
                else:                                  # 드문 증가 → 부드럽게 tween
                    self._val_anim.stop()
                    self._val_anim.setStartValue(int(cur))
                    self._val_anim.setEndValue(done)
                    self._val_anim.setDuration(motion.dur(self.VAL_TWEEN_MS))
                    self._val_anim.start()
        else:
            self._enter_busy()                          # busy: 혜성 스윕으로 교체
        # ★ 매 tick 마다 _cover_parent() 를 부르지 않는다 — sizeHint + setGeometry 가
        #   진행 갱신 횟수만큼 돌았다(200 tick = 200회).  크기는 부모 리사이즈
        #   (eventFilter)와 표시 시점에만 바뀐다.  단, busy↔결정형 전환은 내용이
        #   바뀌므로 그때만 다시 배치한다.
        if mode_changed:
            self._cover_parent()

    # ------------------------------------------------------------------
    def hideEvent(self, event):  # noqa: N802
        """숨으면 잠금을 풀고, 돌던 것을 전부 멈춘다.

        ★ 이 클래스에 `hideEvent` 가 **두 번** 정의돼 있었다.  파이썬은 뒤에 정의된
        것만 남기므로 앞의 '애니메이션 정지' 본문은 한 줄도 실행되지 않았고, 그 결과
        `_finish_hide` 를 거치지 않는 퇴장(중지 → 페이지 전환)에서 스피너 타이머와
        busy 무한 애니메이션이 **숨은 채로 세션 내내 계속 돌았다**.  둘을 하나로 합친다 —
        같은 이름의 메서드를 다시 만들지 마라(회귀 가드: `test_loading_overlay_hide.py`).

        ★ 순서가 중요하다: **잠금 해제가 맨 앞**이다.  전역 필터가 남으면 앱 전체가
        키보드를 영영 못 받는데(테스트에서 실제로 그렇게 됐다), 아래 정지 중 하나라도
        `RuntimeError`(삭제된 C++ 객체)를 내면 그 뒤 줄에 도달하지 못한다.  `_finish_hide`
        만 믿을 수도 없다 — 부모 창이 닫히거나 페이지가 통째로 교체되면 그 경로를 거치지
        않고 숨겨진다.  `hide()` 는 어느 경로로든 이 이벤트를 낸다."""
        self._set_input_lock(False)
        self._fade_anim.stop()
        self._rise_anim.stop()
        self._settle_progress()        # 값 확정 후 정지 — 중간값으로 얼지 않게
        self._bar_anim.stop()
        self._bar_timer.stop()
        self._hide_timer.stop()        # 대기 중인 최소표시 래치도 취소(토큰 가드의 이중화)
        self._spinner.stop()
        self._busy.stop()
        super().hideEvent(event)

    # 오버레이가 떠 있는 동안 **버리는** 이벤트.
    #
    # ★ `Shortcut` 이 반드시 들어가야 한다.  `QShortcut` 은 `KeyPress` 나
    #   `ShortcutOverride` 를 잡아먹어도 **그대로 발화한다**(실측: 셋 중 `Shortcut` 만
    #   막힌다).  이 하나가 빠져 있어서, 어두워진 화면 위에서 N 을 누르면 자동 매치
    #   중이던 사진이 조용히 '매치 없음' 으로 확정돼 **엑셀에 사실이 아닌 결과**가
    #   남았다.  마우스는 위젯이 이미 막고 있었으므로 사용자는 '지금은 아무것도 안
    #   눌린다' 고 믿고 있었다 — 그래서 틀어진 줄도 몰랐다.
    _BLOCKED = (
        QEvent.Type.Shortcut,          # ← QShortcut 을 실제로 멈추는 유일한 종류
        QEvent.Type.ShortcutOverride,
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
    )

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        etype = event.type()
        if obj is self.parent() and etype == QEvent.Type.Resize:
            self._cover_parent()
            return super().eventFilter(obj, event)
        # 어두워진 동안 뒤 화면은 **키도 받지 않는다**(마우스는 위젯이 이미 막는다).
        # 단 오버레이 자신(‘중지’ 버튼)으로 가는 키는 통과시킨다 — 그러지 않으면
        # 취소가 키보드로 불가능해진다.
        if self._input_locked and etype in self._BLOCKED:
            if not self._is_within_overlay(obj):
                return True
        return super().eventFilter(obj, event)

    def _is_within_overlay(self, obj) -> bool:
        # ★ ``Shortcut`` 이벤트의 수신자는 위젯이 아니라 ``QShortcut`` 객체다 — 소유
        #   위젯은 ``parent()`` 로 얻는다.  지금은 오버레이가 단축키를 갖고 있지 않아
        #   결과가 같지만, 나중에 하나라도 달면(예: Esc 로 중지) 이게 없으면 **자기
        #   단축키를 자기가 막는다**.  같은 함정이 `sheet_host._owner_widget` 에 있다.
        node = obj if isinstance(obj, QWidget) else None
        if node is None:
            parent = obj.parent() if hasattr(obj, "parent") else None
            node = parent if isinstance(parent, QWidget) else None
        depth = 0
        while node is not None and depth < 80:
            if node is self:
                return True
            node = node.parentWidget()
            depth += 1
        return False

    def _set_input_lock(self, on: bool) -> None:
        """앱 전역 필터는 **떠 있는 동안만** 건다.

        전역 필터는 마우스 이동까지 모든 이벤트를 받는다 — 평소에도 걸어 두면 앱 전체가
        조금씩 느려진다(`sheet_host._set_app_filter` 와 같은 이유·같은 방식)."""
        app = QApplication.instance()
        if app is None or on == self._input_locked:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self._input_locked = on

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
