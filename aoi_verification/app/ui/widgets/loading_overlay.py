"""로딩 오버레이 — 제도 시트의 **타이틀블록**으로 구성한 진행 표시.

부모 위젯 위에 반투명 스크림 + 패널을 띄운다.  패널 구성(위에서 아래로):

    ┌────────────────────────────────────┐ ← 상단 전폭 4px 진행 눈금
    │ 단계 02 / 03                [중지] │   결정형=accent 채움(스냅)
    │ 썸네일 생성 중                      │   busy=혜성 스윕(등속)
    │ ●─── 폴더 스캔 ─○─ 썸네일 ─·─ 준비  │ ← 여정 스텝(선택)
    │ 62%          남은 시간 약 1분 20초  │
    │              진행 298 / 480        │
    └────────────────────────────────────┘

★ **회전 링(스피너)을 두지 않는다.**  링은 상태 정보가 없는 장식인데, 62.5Hz 타이머로
  상시 돌아 UI 스레드가 바쁠 때 가장 먼저 끊겼다 — 사용자가 본 "로딩 표현이 버벅거린다"
  가 정확히 그것이다.  총량을 모르는 구간의 '살아 있다' 신호는 상단 눈금의 혜성 스윕이
  전담한다(결정형일 때 이 패널의 상시 애니메이션은 **0 개**다).

렉을 만들지 않기 위한 규칙: 라벨은 **값이 바뀐 경우에만** `setText`(`_set_text`),
남은 시간은 타이머 없이 `set_progress` 안의 상수 시간 산술로만 구하고 **1초에 한 번**
갱신한다, 등장이 끝나면 그래픽 이펙트를 즉시 뗀다(`_detach_effect`).

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

from PyQt6.QtCore import (QEasingCurve, QElapsedTimer, QEvent, QPoint, QRect, Qt,
                          QTimer, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import QColor, QPainter, QPen, QPolygon
from PyQt6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QHBoxLayout,
                             QLabel, QProgressBar, QSizePolicy, QVBoxLayout,
                             QWidget)

from ... import i18n
from ...config import Fonts as _Fonts
from .. import theme
from .. import motion
from .neon_button import NeonButton



def _fmt_duration(seconds: float) -> str:
    """초 → 사람이 읽는 길이.  ★ 추정치이므로 필요 이상으로 정밀하게 적지 않는다
    (10분 남았는데 '9분 47초' 라고 적으면 초 단위가 계속 흔들려 신뢰를 잃는다)."""
    s = int(max(0, round(seconds)))
    if s >= 3600:
        return i18n.KO.DURATION_HOUR_FMT.format(h=s // 3600, m=(s % 3600) // 60)
    if s >= 60:
        return i18n.KO.DURATION_MIN_FMT.format(m=s // 60, s=s % 60)
    return i18n.KO.DURATION_SEC_FMT.format(s=s)


# QSS 의 `$font_mono` 는 CSS 문법의 폴백 목록이라 QFont(문자열)로는 못 쓴다.
# QPainter 로 직접 그리는 수치에 같은 서체를 주려고 목록만 떼어 낸다 —
# 여기서 목록을 새로 적으면 QSS 와 갈라지므로 config 를 단일 출처로 둔다.
_MONO_FAMILIES = [s.strip().strip('"\'')
                  for s in _Fonts.MONO.split(",") if s.strip()]


class _JourneySteps(QWidget):
    """작업 큐 — 완료(✓) · 진행 중 · 대기를 **세로 목록**으로 보여 준다.

    ★ 위젯을 단계 수만큼 만들지 않고 **한 번에 그린다.**  단계는 서너 개뿐이고
      내용이 바뀔 때만 다시 그리면 되므로, 위젯 트리를 만들었다 지웠다 하는 것보다
      싸고 레이아웃이 흔들리지 않는다(패널 높이 고정에도 유리하다).
    ★ 줄마다 **자기 수치**(``298 / 480``)를 들고 있다.  차단 오버레이는 화면을 가리는
      대가로 '전체 중 어디쯤 · 몇 개 남았나' 를 돌려줘야 한다 — 단계가 넘어갈 때
      진행바가 0 으로 스냅해도 지나온 단계의 수치는 **마지막 값으로 얼려** 남는다.
      현재 단계의 수치는 :meth:`LoadingOverlay.set_progress` 가 먹인다.
      (구조개편 11안-B: 차단은 유지하되 작업 큐로 보상한다.)
    ★ 가로 점 행이 아니라 세로 목록인 이유: 가로로는 단계당 폭이 패널 폭 ÷ n 뿐이라
      라벨 옆에 수치를 놓을 자리가 없다.  세로면 라벨은 왼쪽, 수치는 오른쪽 끝으로
      고정돼 자릿수가 바뀌어도 줄이 흔들리지 않는다.
    """

    MARK_D = 18                 # 원형 표식 지름
    ROW_H = 30

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._labels: tuple[str, ...] = ()
        self._index = 0
        # 단계 인덱스 → (done, total).  지나간 단계도 남는다(그게 '작업 큐'다).
        self._counts: dict[int, tuple[int, int]] = {}
        self.setFixedHeight(self.ROW_H)

    def set_steps(self, labels, index: int) -> None:
        labels = tuple(str(x) for x in (labels or ()))
        index = max(0, min(int(index), len(labels) - 1)) if labels else 0
        if (labels, index) == (self._labels, self._index):
            return                      # 같은 내용 → 다시 그리지 않는다
        if labels != self._labels:
            self._counts = {}           # 다른 여정 → 옛 수치를 물려주지 않는다
        self._labels, self._index = labels, index
        # 높이는 줄 수에 따라 달라진다 — 호출부(`set_stage`)가 패널을 다시 잰다.
        self.setFixedHeight(self.ROW_H * len(labels) if labels else self.ROW_H)
        self.setVisible(bool(labels))
        self.update()

    def set_counts(self, done: int, total: int) -> None:
        """현재 단계의 수치를 적는다.  ``total <= 0``(busy)면 그 줄의 수치를 지운다."""
        if not self._labels:
            return
        new = (int(done), int(total)) if total > 0 else None
        if self._counts.get(self._index) == new:
            return                      # 값이 그대로면 다시 그리지 않는다
        if new is None:
            self._counts.pop(self._index, None)
        else:
            self._counts[self._index] = new
        self.update()

    def paintEvent(self, event):  # noqa: N802
        if not self._labels:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent, line, line2 = (QColor(theme.ACCENT), QColor(theme.LINE),
                               QColor(theme.LINE2))
        ink, mute, on_accent = (QColor(theme.INK), QColor(theme.MUTE),
                                QColor(theme.ON_ACCENT))
        w, d = self.width(), self.MARK_D
        last = len(self._labels) - 1
        base_pt = self.font().pointSizeF()
        for i, text in enumerate(self._labels):
            top = i * self.ROW_H
            cy = top + self.ROW_H // 2
            done_step, current = i < self._index, i == self._index
            # 줄 구분선 — 마지막 줄 아래에는 긋지 않는다(패널 테두리와 겹친다).
            if i < last:
                p.setPen(QPen(line))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(0, top + self.ROW_H - 1, w, top + self.ROW_H - 1)
            # 표식 — 완료=채운 원+✓ / 현재=2px 테두리+번호 / 대기=옅은 테두리+번호.
            mark = QRect(0, cy - d // 2, d, d)
            if done_step:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent)
            else:
                pen = QPen(accent if current else line2)
                pen.setWidth(2 if current else 1)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(mark)
            if done_step:
                # ★ 체크 표시는 **글자가 아니라 선**으로 그린다.  동봉 폰트
                #   (NanumSquare)에는 U+2713 글리프가 없어 '✓' 를 찍으면 두부(□)가
                #   나온다 — PC 마다 설치 폰트가 달라 그때그때 다른 결과가 된다.
                pen = QPen(on_accent)
                pen.setWidth(2)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                cx0 = mark.center().x() + 1
                cy0 = mark.center().y() + 1
                p.drawPolyline(QPolygon([QPoint(cx0 - 5, cy0 - 1),
                                         QPoint(cx0 - 2, cy0 + 2),
                                         QPoint(cx0 + 4, cy0 - 4)]))
            else:
                f = self.font()
                f.setPointSizeF(max(7.0, base_pt - 1.0) if base_pt > 0 else 8.0)
                f.setBold(True)
                p.setFont(f)
                p.setPen(accent if current else mute)
                p.drawText(mark, int(Qt.AlignmentFlag.AlignCenter), str(i + 1))
            # 이름(왼쪽) — 현재 단계만 본문 잉크(굵게), 나머지는 보조색.
            f = self.font()
            f.setBold(current)
            p.setFont(f)
            p.setPen(ink if current else mute)
            name_x = d + 10
            p.drawText(QRect(name_x, top, max(0, w - name_x - 96), self.ROW_H),
                       int(Qt.AlignmentFlag.AlignLeft
                           | Qt.AlignmentFlag.AlignVCenter), text)
            # 수치(오른쪽 끝) — 아직 시작하지 않은 단계는 '대기'.
            count = self._counts.get(i)
            if count is not None:
                right = i18n.KO.LOADING_STEP_COUNT_FMT.format(done=count[0],
                                                              total=count[1])
            elif done_step or current:
                right = ""
            else:
                right = i18n.KO.LOADING_STEP_PENDING
            if right:
                # 수치는 모노 — 자릿수가 바뀌어도 오른쪽 끝이 흔들리지 않는다
                # (패널의 다른 수치 라벨과 같은 규약).  ★ '대기' 같은 **한글**에는
                #   모노를 씌우지 않는다: 동봉 모노 계열에 한글 글리프가 없어
                #   PC 마다 대체 글꼴이 달라진다.
                f = self.font()
                if count is not None:
                    f.setFamilies(_MONO_FAMILIES)
                f.setBold(current)
                p.setFont(f)
                p.drawText(QRect(w - 96, top, 96, self.ROW_H),
                           int(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter), right)


class _BusyStripe(QWidget):
    """무한(busy) 진행 — 폭 24% 세그먼트가 **등속**으로 순환하는 '혜성 스윕'.

    Qt 기본 블록 왕복 대신 꼬리 알파 그라데이션.  이징은 의도적으로 ``Linear`` 다 —
    끝에서 감속하는 '숨쉬기'는 총량을 모르는 작업에 '거의 끝났다'는 거짓 신호를 준다."""

    def __init__(self, parent=None, width: int | None = None,
                 height: int = 6) -> None:
        """``width`` 를 주면 그 폭으로 고정, 주지 않으면 **레이아웃을 따른다.**

        두 쓰임이 실제로 다르다:
        · 로딩 패널의 상단 눈금 — 폭을 고정하면 패널이 클램프되거나 넓어질 때 눈금과
          어긋나, 결정형 눈금과 busy 스윕이 '같은 자리' 라는 계약이 깨진다(스윕이
          왼쪽 일부만 덮는다).  그래서 폭을 주지 않는다.
        · 시작 스플래시 — 옆의 결정형 바가 `BAR_W` 로 고정돼 있어, busy 도 **같은
          폭**이어야 전환할 때 폭이 뛰지 않는다.  그래서 폭을 준다.
        ★ 맨 QWidget 은 쓸 만한 sizeHint 가 없어 기본 100px 에 머무르므로, 확장 쪽은
          크기 정책을 명시해야 한다."""
        super().__init__(parent)
        self.setFixedHeight(height)
        if width is None:
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(width)
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
    # 패널 테두리 두께 — QSS 의 `QWidget[role="loadingPanel"] { border: 1px … }` 와
    # **같은 값이어야 한다.**  상단 진행 눈금을 그 테두리 안쪽에 앉히는 데 쓴다
    # (회귀 가드: test_loading_panel 이 눈금 top 이 패널 top 보다 이만큼 아래인지 잰다).
    PANEL_BORDER_PX = 1
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

        # ★ 바깥 레이아웃 여백은 **테두리 두께(1px)뿐**이다 — 상단 진행 눈금이 패널
        #   폭을 거의 전부 써야 '치수선' 으로 읽히되, 패널의 `1px solid $line` 테두리
        #   **안쪽**에 앉아야 한다.  예전에는 여백이 0 이라 눈금이 테두리를 덮었고,
        #   눈금은 각진 모서리(`border-radius: 0`)인데 패널은 둥근 모서리라 눈금의
        #   양 끝이 밖으로 삐져나왔다 — 사용자가 본 "파란 바가 위에 덧붙여진 느낌"이
        #   그것이다.  QSS 쪽에서 눈금 상단 모서리를 `$radius_inner` 로 둥글린다.
        #   본문 여백은 안쪽 레이아웃이 준다.
        v = QVBoxLayout(self._panel)
        v.setContentsMargins(self.PANEL_BORDER_PX, self.PANEL_BORDER_PX,
                             self.PANEL_BORDER_PX, 0)
        v.setSpacing(0)

        self._progress = QProgressBar(self._content)
        self._progress.setProperty("role", "loadingRule")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        # ★ 숫자를 바 **안**에 두지 않는다 — 채움(accent)이 글자 아래를 지나는 순간
        #   대비가 2.41(라이트)/1.85(다크)로 붕괴한다(실측).  바 밖 모노 라벨로 옮겨
        #   어떤 진행률에서도 같은 대비를 유지한다.
        self._progress.setTextVisible(False)
        self._count_label = QLabel("", self._content)
        self._count_label.setProperty("role", "progressCount")
        self._count_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                       | Qt.AlignmentFlag.AlignVCenter)
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

        # busy 는 **같은 자리**(패널 상단 눈금)를 결정형과 나눠 쓴다 — 총량을 모르는
        # 단계에서는 그 눈금이 혜성 스윕으로 바뀔 뿐, 자리가 옮겨 다니지 않는다.
        self._busy = _BusyStripe(self._content, height=4)
        self._busy.hide()

        # 단계 서수 · 단계 이름 · 여정 스텝 ------------------------------
        self._stage_label = QLabel("", self._content)
        self._stage_label.setProperty("role", "loadingStage")
        self._stage_label.hide()          # 단계 정보를 준 호출부에서만 보인다
        self._label = QLabel("", self._content)
        self._label.setProperty("role", "loadingTitle")
        self._label.setWordWrap(True)
        # ★ 폭을 못 박는다 — 안 그러면 긴 메시지가 패널을 옆으로 늘린다.
        #   `_place_panel` 이 `max(PANEL_W, hint.width())` 로 폭을 정하므로, 자식이
        #   무한정 넓어지면 패널이 따라 넓어진다.  실제 호출부가 있다:
        #   `main_window._start_openvino_install` 은 pip 출력 80자를 그대로 실어
        #   보내는데, 이 표제는 20px 이라 한 줄만으로도 424 를 훌쩍 넘긴다.
        #   (= PANEL_W 424 − 안쪽 좌우 여백 24×2)
        self._label.setFixedWidth(self.PANEL_W - 48)
        self._steps = _JourneySteps(self._content)
        self._steps.hide()
        # 큰 진행률 · 남은 시간 ------------------------------------------
        self._pct_label = QLabel("", self._content)
        self._pct_label.setProperty("role", "loadingPct")
        self._eta_label = QLabel("", self._content)
        self._eta_label.setProperty("role", "loadingEta")
        self._eta_label.setAlignment(Qt.AlignmentFlag.AlignRight
                                     | Qt.AlignmentFlag.AlignVCenter)
        self._reset_eta()

        # #8 중지 버튼 — cancelable=True 로 보여진 작업에서만.
        # ★ 인스턴스 스타일시트로 색을 굽지 않는다.  오버레이는 앱 시작 때 한 번 만들어지고
        #   `_recolor_in_place` 는 인스턴스 스타일시트를 못 바꾸므로, 구운 라이트 팔레트가
        #   다크 전환 뒤에도 그대로 남아 글자 대비가 2.19:1 로 무너졌다(실측 캡처).
        #   role 로 옮기면 전역 QSS 가 다시 렌더되면서 두 모드를 모두 따라온다.
        self._cancel_btn = NeonButton(i18n.KO.BTN_STOP, role="danger",
                                      parent=self._content)
        self._cancel_btn.setFixedWidth(120)
        self._cancel_btn.clicked.connect(self.cancel_requested.emit)
        self._cancel_btn.hide()

        # ── 패널 조립 — 상단 눈금(전폭) → 본문(여백 안) ───────────────────
        v.addWidget(self._progress)
        v.addWidget(self._busy)

        inner = QVBoxLayout()
        inner.setContentsMargins(24, 16, 24, 18)
        inner.setSpacing(10)
        # 머리줄: 단계 서수 ↔ [중지](우상단 — 파괴적이지 않은 유일한 조작이라
        # 본문 흐름 밖 구석에 둔다).
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self._stage_label)
        head.addStretch(1)
        head.addWidget(self._cancel_btn)
        inner.addLayout(head)
        inner.addWidget(self._label)
        inner.addWidget(self._steps)

        # 아래줄: 큰 퍼센트 ↔ (남은 시간 / 진행 수치).  이 묶음만 스태거로 들어온다.
        self._bar_host = QWidget(self._panel)
        # ★ 맨 QWidget 은 전역 `QWidget { background-color: $bg }` 를 물려받아 패널 면
        #   ($panel) 위에 색이 다른 띠로 보인다(실측).  투명으로 못 박는다.
        self._bar_host.setProperty("role", "loadingBarHost")
        _bar_lay = QVBoxLayout(self._bar_host)
        _bar_lay.setContentsMargins(0, 0, 0, 0)
        _bar_lay.setSpacing(6)
        _metrics = QHBoxLayout()
        _metrics.setContentsMargins(0, 0, 0, 0)
        _metrics.addWidget(self._pct_label)
        _metrics.addStretch(1)
        _right = QVBoxLayout()
        _right.setContentsMargins(0, 0, 0, 0)
        _right.setSpacing(2)
        _right.addWidget(self._eta_label)
        _right.addWidget(self._count_label)
        _metrics.addLayout(_right)
        _bar_lay.addLayout(_metrics)
        # ★ 여기에 두 번째 QGraphicsOpacityEffect 를 걸지 않는다 — 패널이 이미 이펙트로
        #   렌더되는 중이라 이펙트를 겹치면 "A paint device can only be painted by one
        #   painter at a time" 경고가 난다.  대신 위/아래 여백을 맞바꿔(합은 일정)
        #   패널 크기를 흔들지 않고 살짝 밀려 들어오게 한다.
        self._bar_lay = _bar_lay
        inner.addWidget(self._bar_host)
        v.addLayout(inner)

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
        # ★ '아직 살아 있어야 하는 오버레이인가' — 페이지 전환처럼 **잠깐 숨었다
        #   다시 보이는** 경우에 스스로를 되살리기 위한 표식이다(`showEvent` 참조).
        #   `hideEvent` 는 이걸 건드리지 않는다 — 그 잠깐의 숨김이 곧 '끝났다' 는
        #   뜻은 아니기 때문이다.  끝은 `_finish_hide` 하나뿐이다.
        self._active = False
        self._arming = False             # `show_overlay` 안의 재진입 방지

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

    # -- 남은 시간(ETA) ------------------------------------------------
    # ★ 이 추정은 **상수 시간 산술**만 한다 — 타이머를 하나도 더 만들지 않고
    #   `set_progress` 호출 안에서 계산한다(로딩이 무거워지면 안 되는 화면이다).
    EMA_ALPHA = 0.15          # 처리 속도 지수 평활 계수(낮을수록 둔하고 안정적)
    ETA_MIN_SAMPLES = 5       # 이보다 적으면 추정을 내놓지 않는다("—")
    ETA_REFRESH_MS = 1000     # 표시 갱신은 1초에 한 번만
    ETA_MIN_DELTA = 0.10      # 직전 표시 대비 10% 미만 변동은 무시(숫자 튐 방지)
    ETA_HUSH_S = 2            # 2초 이내로 남으면 문구를 지운다(곧 사라질 값)

    def _reset_eta(self) -> None:
        """총량이 바뀌면 반드시 부른다 — **다른 일이 시작됐다**는 뜻이라, 이전
        단계의 처리율을 물려주면 추정치가 조용히 거짓말을 한다(바를 스냅하는 것과
        같은 이유다)."""
        self._eta_rate: float | None = None      # items/sec, EMA
        self._eta_samples = 0
        self._eta_last_done = 0
        self._eta_sample_clock = QElapsedTimer()
        self._eta_paint_clock = QElapsedTimer()
        self._eta_shown_s: float | None = None
        if hasattr(self, "_eta_label"):
            self._eta_label.setText("")

    def _feed_eta(self, done: int, total: int) -> None:
        """진행 표본 하나를 먹이고, 필요하면 표시를 갱신한다."""
        if not self._eta_sample_clock.isValid():
            self._eta_sample_clock.start()
            self._eta_last_done = done
        else:
            # ★ 잰 시간이 0ms 면 **기준점을 옮기지 않는다.**  옮겨 버리면 그 사이의
            #   진행분이 통째로 사라져, 갱신이 촘촘한 작업에서는 표본이 영영 쌓이지
            #   않고 남은 시간이 "—" 로 굳는다(실측: 같은 ms 안에 여러 번 보고하는
            #   호출부에서 그렇게 됐다).  잴 수 있을 때까지 델타를 모아 둔다.
            dt_ms = self._eta_sample_clock.elapsed()
            delta = done - self._eta_last_done
            if dt_ms > 0 and delta > 0:
                rate = delta / (dt_ms / 1000.0)
                self._eta_rate = (rate if self._eta_rate is None else
                                  self.EMA_ALPHA * rate
                                  + (1.0 - self.EMA_ALPHA) * self._eta_rate)
                self._eta_samples += 1
                self._eta_sample_clock.restart()
                self._eta_last_done = done

        if self._eta_samples < self.ETA_MIN_SAMPLES or not self._eta_rate:
            self._set_text(self._eta_label, i18n.KO.LOADING_ETA_UNKNOWN)
            return
        remain = max(0.0, (total - done) / self._eta_rate)
        if remain <= self.ETA_HUSH_S:
            # 곧 끝난다 — '약 1초' 같은 문구는 정보가 아니라 소음이다.
            self._set_text(self._eta_label, "")
            return
        # 1초에 한 번, 그리고 눈에 띄게 달라졌을 때만 다시 적는다.
        if self._eta_paint_clock.isValid():
            if self._eta_paint_clock.elapsed() < self.ETA_REFRESH_MS:
                return
            prev = self._eta_shown_s
            if prev and abs(remain - prev) / prev < self.ETA_MIN_DELTA:
                return
        self._eta_paint_clock.restart()
        self._eta_shown_s = remain
        self._set_text(self._eta_label,
                       i18n.KO.LOADING_ETA_FMT.format(text=_fmt_duration(remain)))

    @staticmethod
    def _set_text(label: QLabel, text: str) -> None:
        """값이 **바뀌었을 때만** 적는다 — 같은 문자열을 다시 넣어도 Qt 는 레이아웃
        갱신과 리페인트를 예약한다(초당 수십 번 불리는 경로라 그것이 곧 렉이다)."""
        if label.text() != text:
            label.setText(text)

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
        # 총량을 모르니 수치도 추정도 없다 — 비우고 다음 결정형을 위해 리셋한다.
        self._set_text(self._count_label, "")
        self._set_text(self._pct_label, "")
        self._reset_eta()
        self._busy.show()
        self._busy.start()

    def show_overlay(self, message: str = "", *, cancelable: bool = False,
                     step: tuple[int, int] | None = None,
                     steps: tuple[str, ...] | None = None) -> None:
        """오버레이를 띄운다.

        ``step``/``steps`` 는 **선택 인자**다 — 주지 않으면 단계 줄과 여정 행이
        숨겨져 지금까지와 똑같은 '문구 하나' 모드로 뜬다(하위 호환).  조건부 단계가
        끼어 총 단계 수가 세션마다 달라지는 흐름은 그대로 두면 된다.
        """
        self._active = True
        self._arming = True              # 아래 `show()` 가 부를 showEvent 의 재무장 억제
        self._set_text(self._label, message)
        self._apply_stage(step, steps)
        self._set_input_lock(True)
        self._cancel_btn.setVisible(bool(cancelable))
        # busy 로 시작한다 — 첫 set_progress(done, total>0) 이 결정형으로 승격시킨다.
        self._enter_busy()
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
        self._arming = False

    def set_stage(self, step: tuple[int, int] | None,
                  steps: tuple[str, ...] | None = None) -> None:
        """이미 떠 있는 오버레이의 **단계만** 바꾼다.

        `show_overlay` 를 다시 부르면 등장 모션과 최소표시 래치가 되감기므로, 여정
        중간에 단계가 넘어갈 때는 이쪽을 쓴다(덮개는 그대로 유지된다)."""
        self._apply_stage(step, steps)
        # ★ 단계 줄과 여정 행이 붙고 떨어지면 패널 **내용의 높이**가 달라진다.  패널
        #   기하는 `_place_panel` 의 setGeometry 가 sizeHint 로 정하므로, 여기서 다시
        #   재지 않으면 옛 높이에 새 내용이 눌려 들어가 하단 줄(퍼센트·남은 시간·수치)이
        #   잘린다.  `show_overlay` 는 `_cover_parent` 로 이미 다시 잰다.
        self._place_panel()

    def _apply_stage(self, step, steps) -> None:
        """단계 서수 줄과 여정 행을 세운다 — 둘 다 정보를 준 호출부에서만 보인다."""
        labels = tuple(steps or ())
        if step:
            idx, total = int(step[0]), int(step[1])
            self._set_text(self._stage_label,
                           i18n.KO.LOADING_STAGE_FMT.format(idx=idx, total=total))
            self._stage_label.show()
            self._steps.set_steps(labels, idx - 1)
        else:
            self._set_text(self._stage_label, "")
            self._stage_label.hide()
            self._steps.set_steps((), 0)
        queued = bool(labels and step)
        self._steps.setVisible(queued)
        # ★ 작업 큐가 뜨면 현재 단계의 수치는 **큐의 줄**이 말한다 — 바 아래 모노
        #   라벨을 같이 켜 두면 같은 숫자가 한 패널에 두 번 적힌다(ko.py 단일 출처
        #   규칙).  텍스트는 계속 채워 두고 **표시만** 끈다: 큐 유무는 한 번 보여
        #   주는 동안 고정이라 이 토글이 매 틱 패널 높이를 흔들지 않는다.
        self._count_label.setVisible(not queued)

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

    def is_retiring(self) -> bool:
        """퇴장이 **예약되었거나 진행 중**인가 — 보이더라도 곧 사라진다.

        ``isVisible()`` 만 보고 '아직 덮고 있다' 고 판단하면 안 되는 구간이 둘 있다:
        최소 표시 래치(``_hide_pending``, 최대 350ms)와 퇴장 페이드(``_hiding``).
        이어지는 작업이 덮개를 **이어받을 때**(`match_page._update_auto_progress`)
        이걸 보지 않으면 몇 백 ms 뒤 덮개가 조용히 사라진 채로 작업이 계속 돈다.
        """
        return bool(getattr(self, "_hiding", False)
                    or getattr(self, "_hide_pending", False))

    def hide_overlay(self, then=None) -> None:
        # ★ `hide_overlay` 는 **동기 종료가 아니다** — 최소표시 래치와 페이드아웃이
        #   남아 있으면 타이머만 걸고 돌아온다.  '덮개가 걷힌 뒤' 에 할 일은 반드시
        #   이 콜백으로 걸어야 한다(바로 다음 줄에서 하면 아직 덮여 있다).
        self._hide_then = then
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
        self._active = False           # 여기가 유일한 '끝' 이다
        after = getattr(self, "_hide_then", None)
        self._hide_then = None
        self._set_input_lock(False)
        self._settle_progress()        # 멈춘 tween 의 중간값이 남지 않게(모션 off 경로 포함)
        self._set_done_state(False)     # 다음 작업이 완료색으로 시작하지 않게
        self._set_state(self._progress, "")
        self.hide()
        self._bar_anim.stop()          # 숨은 뒤 tick 이 남아 여백을 흔들지 않게
        self._bar_timer.stop()         # 대기 중인 스태거도 취소(숨은 뒤 들어오지 않게)
        self._busy.stop()
        self._busy.hide()
        self._cancel_btn.hide()
        if after is not None:
            after()
        self._fade = 0.0
        self._rise_span = self.RISE_IN_PX      # 다음 등장을 위해 초기화
        self._set_bar_slide(1.0)

    def _set_done_state(self, done: bool) -> None:
        """완료 표시(pass 색)를 켜고 끈다 — 값이 바뀔 때만 다시 폴리시한다.

        ★ 매 tick 마다 unpolish/polish 하면 진행 갱신 횟수만큼 스타일 재계산이
        돈다(200 tick = 200회).  이 앱이 '문구는 바뀔 때만 쓴다' 로 세운 규칙과
        같은 이유다."""
        done = bool(done)
        if getattr(self, "_done_state", False) == done:
            return
        self._done_state = done
        # ★ 색이 바뀌는 것은 **문구 하나**다.  23안-B 목업을 실측하면 pass 색은
        #   "유사도 계산 완료" 스팬에만 걸려 있고, 눈금은 accent 그대로 · "100 %" 는
        #   기본 잉크다.  셋을 다 칠하면 '한 화면에 강조 하나' 가 무너지고 완료가
        #   경고처럼 커진다.  눈금은 아래 `finish_tick` 이 그 한 지점에서만 만진다.
        self._set_state(self._label, "done" if done else "")

    @staticmethod
    def _set_state(widget, value: str) -> None:
        """QSS 상태 속성을 갈아 끼우고 다시 폴리시한다(값이 바뀔 때만 부른다)."""
        widget.setProperty("state", value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def finish_tick(self, then=None) -> None:
        """수 분짜리 작업이 끝나는 **그 한 지점**에서 눈금을 200ms 머금었다 보낸다.

        ★ 23안은 B(모션 0)를 채택하면서, '수 분 작업의 끝' 단 한 지점만은 주변시에
        걸리는 200ms 틱의 실익이 있다고 못박았다 — 스캔·저장처럼 짧은 작업에는
        달지 않는다.  그래서 이건 자동이 아니라 **호출부가 고르는** 신호다.
        ★ 여기서 하는 일은 완료색을 켠 뒤 그만큼 붙잡아 두는 것뿐이다(애니메이션
        객체를 새로 만들지 않는다).  ``then`` 은 보통 ``hide_overlay`` 다."""
        self._set_done_state(True)
        # 눈금이 '한 번 빛나고 멈춘다' — 이 200ms 동안만 완료색이다(A안의 그 틱).
        self._set_state(self._progress, "done")
        if not motion.enabled():
            if then is not None:
                then()
            return
        timer = getattr(self, "_finish_timer", None)
        if timer is None:
            # 정적 QTimer.singleShot 금지 — 오버레이가 먼저 죽으면 세그폴트다.
            timer = QTimer(self)
            timer.setSingleShot(True)
            self._finish_timer = timer
        try:
            timer.timeout.disconnect()
        except TypeError:
            pass
        if then is not None:
            timer.timeout.connect(then)
        timer.start(motion.DUR_FINISH_TICK)

    def set_progress(self, done: int, total: int, message: str = "") -> None:
        if message:
            self._set_text(self._label, message)
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
            self._set_text(self._count_label,
                           i18n.KO.LOADING_COUNT_FMT.format(done=done,
                                                            total=int(total)))
            self._set_text(self._pct_label, f"{int(done * 100 / max(1, total))}%")
            # ★ 23안-B — 100% 에 닿는 순간의 **마침 신호는 색이다**(모션 0).
            #   수 분짜리 작업이 끝나면 오버레이는 140ms 페이드로 조용히 사라져,
            #   다른 창을 보던 사용자는 끝을 놓쳤다.  design-v2 가 이미 세운
            #   '진행 정보 → statusPass 전환' 규칙을 여기 그대로 적용한다.
            self._set_done_state(done >= int(total))
            if self._progress.maximum() != total:      # 단계 전환/총량 변경 → 스냅
                self._val_anim.stop()
                self._progress.setRange(0, total)
                self._progress.setValue(done)
                self._val_gap.start()                  # 이 시점부터 간격을 잰다
                # ★ 총량이 바뀌었다 = 다른 일이 시작됐다 — 이전 단계의 처리율을
                #   물려받으면 추정치가 조용히 거짓말을 한다.
                self._reset_eta()
            else:
                # ★ 표본은 **같은 총량이 이어질 때만** 먹인다.  총량이 바뀐 호출에서
                #   먹이면 이전 단계의 처리율로 새 총량의 남은 시간을 한 번 계산했다가
                #   바로 뒤 `_reset_eta` 가 버리는 꼴이었다 — 계산도 낭비지만, 무엇보다
                #   '버리니까 괜찮다' 는 **순서**에 불변식을 기대게 된다.
                self._feed_eta(done, int(total))
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
            self._set_done_state(False)                 # 다시 진행 중이다
            self._enter_busy()                          # busy: 혜성 스윕으로 교체
        # 작업 큐의 현재 줄에 수치를 먹인다 — busy 면 그 줄의 수치를 지운다.
        # (값이 그대로면 `set_counts` 가 다시 그리지 않는다.)
        self._steps.set_counts(done, int(total))
        # ★ 매 tick 마다 _cover_parent() 를 부르지 않는다 — sizeHint + setGeometry 가
        #   진행 갱신 횟수만큼 돌았다(200 tick = 200회).  크기는 부모 리사이즈
        #   (eventFilter)와 표시 시점에만 바뀐다.  단, busy↔결정형 전환은 내용이
        #   바뀌므로 그때만 다시 배치한다.
        if mode_changed:
            self._cover_parent()

    # ------------------------------------------------------------------
    def showEvent(self, event):  # noqa: N802
        """뜨는 순간 **기하와 상태를 모두** 되살린다.

        기하: `show_overlay` 도 `_cover_parent` 를 부르지만, 그때 부모가 아직 화면에
        놓이기 전이면(스택에 담겨만 있는 페이지) 낡은 크기를 쓴다.

        상태: ★ 페이지 전환은 스냅샷을 찍느라 **보였다 → 숨었다 → 다시 보인다** 를
        한 번에 한다(`main_window._show_page`).  그 중간의 숨김이 `hideEvent` 를
        발화시켜 페이드·스피너·입력잠금을 전부 꺼 버리는데, 그건 '작업이 끝났다' 는
        뜻이 아니다.  되살리지 않으면 화면을 가득 덮은 **완전히 투명한 막**이 남아
        — 스크림도 스피너도 안 보이는데 마우스는 전부 삼켜져 '앱이 죽은 것처럼'
        보이고, 잠금은 풀려 있어 키보드로 [검토 완료] 가 눌린다(못 본 행까지 확정).
        실측: 600행 진입에서 약 4초 동안 그 상태였다.

        `match_page` 는 자기 `showEvent` 에서 `show_overlay` 를 다시 불러 우연히
        피하고 있었지만, 그건 페이지마다 기억해야 하는 규칙이다 — 오버레이가
        스스로 자기 상태를 지키게 한다."""
        super().showEvent(event)
        self._cover_parent()
        if self._active and not self._arming and not self._hiding:
            self._rearm()

    def _rearm(self) -> None:
        """`hideEvent` 가 꺼 놓은 것들을 되살린다 — 아직 끝나지 않은 작업이므로."""
        self._set_input_lock(True)
        if not self._busy.isHidden():      # 총량 미상이면 혜성 스윕도 다시
            self._busy.start()
        # 페이드/상승 애니메이션은 이미 멈췄다 — 다시 재생하면 깜빡이므로 최종값으로.
        self._on_fade(1.0)
        self._on_rise(1.0)
        # ★ 진행바 스태거도 **안착 위치로** 되돌린다.  `hideEvent` 가 `_bar_anim` 과
        #   `_bar_timer` 를 멈추므로, 되살리지 않으면 진행바와 숫자가 시작 위치
        #   (8px 아래)에 굳은 채 로딩 내내 남는다 — 스태거를 만든 바로 그 화면
        #   (600행 검토 진입)에서 등장 안무가 한 번도 재생되지 않았다(실측 (8,0)).
        self._set_bar_slide(1.0)
        # ★ 등장 페이드가 전환에 끊기면 `finished` 가 안 나 `_on_fade_done` 의
        #   `_detach_effect` 에 도달하지 못한다.  불투명도는 이미 1.0 이라 이펙트는
        #   시각적 이득이 0 인데, 붙어 있는 동안 스피너가 1프레임 돌 때마다 패널
        #   전체가 오프스크린으로 다시 렌더된다(62.5Hz) — 정작 그 시간에 돌아야 할
        #   백그라운드 작업의 UI 스레드 여유를 갉아먹는다.  여기서 떼어 낸다.
        self._detach_effect()

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
        않고 숨겨진다.  `hide()` 는 어느 경로로든 이 이벤트를 낸다.

        ★ **퇴장이 진행 중이었다면 여기서 끝을 확정한다.**  아래에서 `_fade_anim` 과
        `_hide_timer` 를 멈추는데, 그러면 `finished` 가 나지 않아
        `_on_fade_done → _finish_hide` 가 **영영 오지 않는다.**  퇴장 페이드(112ms)나
        최소표시 래치(350ms) 가 도는 중에 페이지가 전환되면 정확히 그렇게 된다 —
        모든 페이지 전환이 스냅샷 스왑으로 조상에 hide→show 를 배달하기 때문이다.

        그렇게 '끝' 에 닿지 못한 오버레이는 `hide()` 도 못 부른 채 부모 전체 크기로
        마운트된 채 남는다.  그 페이지가 다시 보이는 순간 화면이 통째로 어두워지고
        (실측 휘도 233 → 173) 클릭이 전부 삼켜지며, 래치 경로에서는 `showEvent` 의
        재무장까지 걸려 **앱 전역 키보드가 영구히 잠긴다.**  비취소 오버레이면
        빠져나갈 방법이 없다 — 강제 종료뿐이다.

        숨은 뒤에는 그 애니메이션을 볼 사람이 없으므로, 남은 것은 '끝냈다는 사실'
        뿐이다.  그것을 지금 확정한다."""
        self._set_input_lock(False)
        if self._hiding or self._hide_pending:
            self._hiding = False
            self._hide_pending = False
            self._finish_hide()          # 유일한 '끝' 에 반드시 도달시킨다
        self._fade_anim.stop()
        self._rise_anim.stop()
        self._settle_progress()        # 값 확정 후 정지 — 중간값으로 얼지 않게
        self._bar_anim.stop()
        self._bar_timer.stop()
        self._hide_timer.stop()        # 대기 중인 최소표시 래치도 취소(토큰 가드의 이중화)
        self._busy.stop()
        super().hideEvent(event)

    # 오버레이가 떠 있는 동안 **버리는** 이벤트 — 목록의 주인은 `motion` 이다.
    #
    # ★ `Shortcut` 이 반드시 들어가야 한다.  `QShortcut` 은 `KeyPress` 나
    #   `ShortcutOverride` 를 잡아먹어도 **그대로 발화한다**(실측: 셋 중 `Shortcut` 만
    #   막힌다).  이 하나가 빠져 있어서, 어두워진 화면 위에서 N 을 누르면 자동 매치
    #   중이던 사진이 조용히 '매치 없음' 으로 확정돼 **엑셀에 사실이 아닌 결과**가
    #   남았다.  마우스는 위젯이 이미 막고 있었으므로 사용자는 '지금은 아무것도 안
    #   눌린다' 고 믿고 있었다 — 그래서 틀어진 줄도 몰랐다.
    # ★ 같은 목록을 **페이지 전환 스냅샷도 쓴다**(`motion.transition_in`).  두 벌로
    #   나누지 마라 — 한쪽만 갱신되면 '어떤 덮개는 단축키를 통과시킨다' 가 된다.
    _BLOCKED = motion.BLOCKED_INPUT_EVENTS

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # ★ ``getattr`` 로 읽는다 — 잠금이 걸린 채 오버레이가 파괴되면 앱에 걸어 둔
        #   전역 필터가 잠시 살아남아, 파이썬 속성이 이미 사라진 객체로 이벤트가
        #   들어온다(그때 `AttributeError` 가 콘솔을 채웠다).  이 경로에서는 아무것도
        #   막지 않는 게 맞다 — 잠글 주체가 이미 없다.
        if not hasattr(self, "_input_locked"):
            return False
        etype = event.type()
        # ★ 부모 크기 추종은 **보이든 안 보이든** 한다.  이건 입력을 삼키는 일이 아니라
        #   기하를 맞추는 일이라, 아래 가시성 가드보다 **위**에 있어야 한다.
        #   숨어 있는 동안 부모가 커진 것을 놓치면 나중에 떴을 때 낡은 크기로 **일부만**
        #   덮는다 — 실제로 그렇게 됐다: 검토 페이지는 스택에 담겨만 있어 첫 전환 전까지
        #   640x480 인데, `load_state` 가 그 상태에서 오버레이를 띄우고 그 다음에 전환된다.
        #   그 결과 1280x800 화면의 좌상단 640x480 만 어두워지고 [검토 완료] 가 밝게
        #   노출돼, **아직 만들어지지 않은 행까지 전부 '유지' 로 확정**됐다.
        if obj is self.parent() and etype == QEvent.Type.Resize:
            self._cover_parent()
            return super().eventFilter(obj, event)
        # ★ **보이지 않으면 입력은 막지 않는다.**  전역 필터는 잠금 플래그만 보고
        #   키를 버렸는데, 부모 페이지가 숨겨진 채 `show_overlay` 가 불리면(전환 경쟁)
        #   오버레이는 화면에 나타나지 못하면서 잠금만 걸린다 — 그러면 보상 해제인
        #   `hideEvent` 도 영영 오지 않아 **앱 전체가 키보드를 못 받는 채로 남는다.**
        #   어두운 화면도 [중지] 버튼도 없으니 사용자는 원인을 알 수 없다.
        #   "가려서 못 누르게 한다" 가 이 잠금의 뜻이므로, 가리고 있지 않으면 권한도 없다.
        if not self.isVisible():
            return False
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
