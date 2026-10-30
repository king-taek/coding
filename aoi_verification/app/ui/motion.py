"""공용 모션 시스템 — 애플급 절제된 전환/스크롤/페이드.

원칙(사용자 지정):
- **ease-out**: 빠르게 시작해 끝에서 **오래** 감속.  기본 ``EASE_PRIMARY``
  (`cubic-bezier(.16,1,.3,1)` — 근거·실측표는 아래 ``_ease_out_long_tail`` 참조).
  단 **끝없이 도는 표시는 등속**이다(회전 스피너·로딩 줄무늬·결정형 바 채움) —
  반복 구간마다 가감속이 붙으면 맥박처럼 보인다.
  그리고 **로딩 오버레이는 두 축을 분리**한다 — 불투명도는 Linear(스크림 디밍이 슬램하지
  않게), 위치는 OutQuad(잔여 **이동량**이 눈에 보이게).  이유는 ``widgets/loading_overlay.py``
  주석과 ``docs/화면_디자인_도면.md`` 참조.
- 퇴장은 입장보다 짧게, 그리고 **가속(ease-in)** 으로 — ``EASE_EXIT``.  떠나는 것에
  감속 곡선을 쓰면 첫 몇 프레임에 거의 다 사라져 남은 시간이 빈 화면이 된다.
  장식이 아니라 상태·공간 연속성을 전달.
- **결정론**: 헤드리스(offscreen) 면 모든 헬퍼가 즉시 적용(테스트/캡처가 흔들리지
  않게).  이 경우 동작 의미는 애니메이션 없이 동일.

변형별 속도는 ``theme.PROFILE.motion_scale`` 로 스케일(시간만, 이동량 아님)."""

from __future__ import annotations

import os

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QParallelAnimationGroup,
                          QPoint, QPointF, QPropertyAnimation, QRectF,
                          QSequentialAnimationGroup, QTimer, QVariantAnimation,
                          pyqtProperty)
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (QApplication, QGraphicsEffect,
                             QGraphicsOpacityEffect, QLabel, QWidget)

from .teardown import tolerate_teardown
from . import theme

# ── '지금 보이는 그림이 라이브 위젯이 아니다' 동안 버리는 입력 이벤트 ──────────
# ★ 목록은 **여기 하나뿐**이다 — `LoadingOverlay._BLOCKED` 도 이걸 그대로 쓴다.
#   두 벌로 두면 한쪽만 갱신돼 '어떤 덮개는 단축키를 통과시킨다' 가 된다(실제로
#   전환 스냅샷이 그랬다: 마우스만 흡수하고 `Shortcut` 은 그대로 발화했다).
# ★ `Shortcut` 이 반드시 들어가야 한다.  `QShortcut` 은 `KeyPress`·
#   `ShortcutOverride` 를 버려도 **그대로 발화한다**(실측: 셋 중 `Shortcut` 만 막는다).
BLOCKED_INPUT_EVENTS = (
    QEvent.Type.Shortcut,          # ← QShortcut 을 실제로 멈추는 유일한 종류
    QEvent.Type.ShortcutOverride,
    QEvent.Type.KeyPress,
    QEvent.Type.KeyRelease,
)


class _InputSwallow(QObject):
    """전환 스냅샷이 떠 있는 동안 키·단축키를 버리는 앱 전역 필터.

    ★ **오버레이(QLabel)를 부모로 만든다.**  전환이 어떤 이유로든 끝을 못 봐도
    (컨테이너 파괴·애니메이션 중단) 오버레이와 함께 죽으므로 잠금은 **열리는 쪽**
    으로 실패한다.  앱 전역 키 필터가 살아남는 실패는 '키보드가 영영 안 먹는 앱'
    이라 절대 만들면 안 된다(`loading_overlay.hideEvent` 주석의 실측 사고).
    """

    @tolerate_teardown
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        try:
            etype = event.type()
        except RuntimeError:                    # 이미 파괴된 이벤트 — 막지 않는다
            return False
        return etype in BLOCKED_INPUT_EVENTS

# 지속시간 토큰 (ms) — motion_scale 로 스케일.
# ※ 한때 `DUR_FAST`·`EASE_LARGE`(OutExpo)·`EASE_LEGACY`(InOutCubic)·`fade_out_snapshot()`
#   도 있었으나 호출처가 **0곳**이었다.  '큰 이동은 OutExpo' 는 docstring 에만 존재하는
#   규칙이었다 — 쓰지 않는 토큰은 규칙처럼
#   읽혀 다음 사람을 오도하므로 지웠다.  필요해지면 그때 다시 만들면 된다.
# ★ 이 모듈의 애니메이션은 **`DeleteWhenStopped` 를 쓰지 않는다.**
#
#   모든 애니메이션이 대상 위젯(또는 자신이 만든 오버레이)을 부모로 갖는다 — 부모가
#   죽으면 함께 죽으므로 자기 삭제로 얻는 것이 없다.  반면 잃는 것이 크다: 자연 종료
#   시점에 C++ 객체가 사라지므로, 그 객체를 가리키는 참조(파이썬 속성이든
#   `destroyed.connect(anim.stop)` 같은 **시그널 연결**이든)는 모두 dangling 이 된다.
#   그 뒤 참조가 쓰이면 파이썬 예외가 아니라 **세그폴트**다.
#
#   실측한 두 사고가 정확히 이것이었다:
#   (1) `self._anim` 에 핸들을 보관 → 두 번째 호출의 stop() 이 RuntimeError → qFatal
#   (2) 위 (1)을 막으려고 `destroyed.connect(anim.stop)` 을 걸었다가, 애니메이션이 먼저
#       자기 삭제된 뒤 대상이 파괴되면서 그 연결이 발화 → 세그폴트.
#   가드를 덧붙이는 대신 **원인(자기 삭제)을 없앤다.**
# ★ 지속시간은 '감속이 눈에 보일 만큼' 이어야 한다.  아래 EASE_PRIMARY 는 마지막
#   10%를 **전체 시간의 67%**에 걸쳐 놓는데, 총 160ms 였을 땐 그 안착이 107ms 라
#   사실상 안 보였다("마지막에 뚝 끊긴다").  곡선만 바꾸고 시간을 그대로 두면
#   체감이 거의 안 달라진다 — 둘은 함께 정해야 한다.
DUR_BASE = 300           # 실제 240ms(motion_scale 0.8)
DUR_SLOW = 400

# ── 사용자가 **실제 밀리초로 지정한** 지속시간 ────────────────────────────────
# ★ 이 셋은 `dur()` 스케일을 타지 않는다.  `motion_scale`(0.8)을 곱하면 400ms 지정이
#   320ms 로 나가 '지정한 값'과 '실제 값'이 갈라진다 — 사용자가 눈으로 정한 수치이므로
#   그대로 쓴다.  스케일이 필요한 자리는 여전히 `dur()` 를 쓴다.
DUR_SHEET = 400          # 작은 화면 팝업(시트) 등장/퇴장 — 발원점을 모를 때의 폴백
DUR_LOADING = 500        # 로딩 화면 팝업 등장
# 구조개편 21·23·25·26·27안이 **눈으로 정한** 지속시간 — 위 셋과 같이 dur() 를 타지
# 않는다(시안이 밀리초로 명시한 값이라 스케일을 곱하면 지정과 실제가 갈라진다).
DUR_RAIL_LEAD = 140      # 21안 — 여정 레일 눈금이 페이지보다 **먼저** 채워지는 시간
DUR_FINISH_TICK = 200    # 23안 — 수 분짜리 작업이 100% 에 닿는 순간의 마침 틱
DUR_RISE_IN = 220        # 25안 — 검토 행 페이드-라이즈
STAGGER_RISE_MS = 30     # 25안 — 행 사이 지연(07안에서 60→30: 다 보일 때까지가 절반)
RISE_PX = 10             # 25안 — 행이 올라오는 거리(07안에서 18→10)
DUR_SWIPE_OUT = 180      # 26안 — 결정한 사진이 방향으로 밀려나는 시간
DUR_SWIPE_IN = 120       # 26안 — 다음 사진 페이드인
DUR_SWIPE_OVER = 120     # 선별 — 다음 사진 **위에서** 떠나는 사진 잔상이 옅어지는 시간(2026-10-25)
DUR_KNOB = 180           # 24안 — 토글 스위치 노브 슬라이드(누른 즉시의 답)
DUR_TOAST_IN = 160       # 자리 예약 토스트 — 나타남(등장)
DUR_TOAST_OUT = 200      # 자리 예약 토스트 — 사라짐(퇴장, 가속)

# ── 2026-10 모션 개선안(사용자가 실제 화면 녹화로 비교해 16개 전부 채택) ──────────
# 위와 같이 **눈으로 정한 밀리초**라 dur() 를 타지 않는다.  번호는 비교 페이지의 안 번호.
DUR_PAGE_EXIT = 80       # 01 — 나가는 화면이 가속으로 빠지는 시간
PAGE_EXIT_PX = 8         # 01 — 나가는 화면이 반대쪽으로 밀리는 거리
DUR_PAGE_ENTER = 220     # 01 — 들어오는 화면 안착(나간 **뒤** 시작)
DUR_RAIL_ARRIVE = 200    # 02 — 레일 선이 다 찬 순간 다음 단계 고리가 한 번 퍼지는 시간
DUR_RAIL_FOLD = 200      # 왼쪽 메뉴가 아이콘만 ↔ 이름까지로 접히고 펴지는 폭 이동
DUR_RAIL_HANDOFF = 70    # 03 — 레일이 이만큼 찼을 때 화면 전환을 시작(옛: 다 찬 140ms 뒤)
DUR_SHEET_OPEN = 280     # 04 — 누른 자리에서 커지며 열리는 시트(발원점을 알 때)
SHEET_OPEN_SCALE = 0.94  # 04 — 시작 배율
SHEET_OPEN_RISE = 8      # 04 — 시작할 때 아래로 내려가 있는 거리
DUR_SHEET_CLOSE = 160    # 05 — 닫히는 시트 본체(가속).  스크림은 여전히 즉시 사라진다
SHEET_CLOSE_SCALE = 0.98
SHEET_CLOSE_DROP = 6
DUR_STAMP_FLASH = 560    # 06 — 선별 결과가 들어간 곳의 한 번 반짝임
DUR_ROW_RESIZE = 220     # 08·09 — 검토 행이 접히고 펼쳐지는 높이 변화
DUR_ROW_SHRINK = 160     # 09 — '접기' 는 펼침보다 짧게(가속)
DUR_TILE_IN = 160        # 08·09 — 새로 보이는 후보 칸 페이드인
STAGGER_TILE_MS = 30     # 09 — 새 후보 칸 사이 지연
DUR_MORPH = 300          # 11 — '크게 보기' 가 행의 사진 자리에서 커지는 시간
DUR_ZOOM_STEP = 120      # 12 — 휠 한 칸의 확대·축소
DUR_PRESS_IN = 80        # 14 — 버튼이 눌려 들어가는 시간
DUR_PRESS_OUT = 160      # 14 — 떼었을 때 돌아오는 시간
PRESS_SCALE = 0.97
PRESS_DARKEN = 0.10      # 14 — 눌렸을 때 덧칠하는 검정의 불투명도
KNOB_STRETCH_PX = 9      # 15 — 노브가 움직이는 도중 길어지는 최대 길이
DUR_TILE_TOGGLE = 140    # 16 — 슬롯 타일의 옛 모습이 새 모습으로 바뀌는 시간
DUR_CHECK_POP = 180      # 16 — 체크 표시가 톡 나타남
DUR_CHECK_OUT = 120      # 16 — 체크 표시가 들어감(가속)
DUR_TICK_DRAW = 240      # 17 — '복사됨' 옆 체크가 그려지는 시간
TOAST_RISE_PX = 6        # 17 — 토스트가 떠오르는 거리
TOAST_LEAVE_PX = 4       # 17 — 토스트가 사라지며 올라가는 거리


def _ease_material_decelerate() -> QEasingCurve:
    """머터리얼 '강조 감속' 곡선 (CSS `cubic-bezier(.05,.7,.1,1)`) — 앱의 기본 곡선.

    ★ 이전 기본은 `cubic-bezier(.16,1,.3,1)` 이었는데 **곡선인데 등속처럼 보인다**는
    지적을 받았다.  원인은 곡선이 없어서가 아니라 **감속이 안 보이는 자리에서
    끝나기 때문**이었다(실측):

    | 시점 | 옛 곡선 | 이 곡선 |
    |---|---|---|
    | t=0.10 | 0.494 | 0.621 |
    | t=0.25 | 0.826 | 0.832 |
    | t=0.50 | 0.972 | 0.950 |
    | t=0.75 | 0.998 | 0.991 |
    | **보이는 구간**(0.02→0.98 에 쓰는 시간) | **54%** | **65%** |

    옛 곡선은 전체 시간의 절반 지점에서 이미 97.2% 진행돼, 남은 절반에 일어나는 변화가
    0.03 이었다.  **불투명도 채널에서는 그 0.03 이 보이지 않는다**(위치라면 남은 거리가
    눈에 보이지만 투명도는 0.03 이 남아도 그냥 투명이다).  그래서 사용자가 실제로 본
    것은 '툭 바뀌고 → 아무 일 없음' 이었다.

    이 곡선은 죽은 꼬리가 짧아(54% → 65%) 감속이 **끝까지 보인다**.  회귀 가드는
    ``dev/tests/test_motion_curve.py`` 가 이 표를 그대로 못 박는다 — 곡선을 바꿀 땐
    숫자를 **계산해서** 갱신하라(눈대중으로 적으면 표가 거짓말을 한다).

    ※ 예외는 하나다 — **끝없이 도는 표시는 등속**이다(회전 스피너·무한 진행 줄무늬).
      반복 구간마다 가감속이 붙으면 맥박처럼 뛴다.  `loading_overlay` 의 Linear 는
      의도된 것이니 곡선으로 바꾸지 마라.
    """
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.05, 0.70), QPointF(0.10, 1.0),
                            QPointF(1.0, 1.0))
    return c


def _ease_material_accelerate() -> QEasingCurve:
    """머터리얼 '강조 가속' 곡선 (CSS `cubic-bezier(.3,0,.8,.15)`) — **퇴장** 전용.

    ★ 떠나는 것에 ``EASE_PRIMARY``(감속)를 쓰면 안 된다.  후보 선별 스와이프(180ms)
    에서 실측: 잔상 불투명도가 20ms 에 0.35, **90ms 에 0.05** — 눈에는 이미 없다.
    다음 사진은 180ms 에 들어오므로 그 사이 ~90ms 가 **빈 화면**이었다(26안이 막으려던
    바로 그 '한순간 비어 보임').  가속 곡선은 같은 시점에 0.85 를 남기고 끝에서 빠지므로
    떠남과 들어옴이 이어진다.

    | 시점(180ms 중) | 감속(옛) | 이 곡선 |
    |---|---|---|
    | 40ms | 0.193 | 0.972 |
    | 90ms | 0.050 | 0.846 |
    | 140ms | 0.007 | 0.553 |
    (남은 불투명도 = 1 − 진행률.  회귀 가드 ``dev/tests/test_motion_curve.py``.)
    """
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.30, 0.0), QPointF(0.80, 0.15),
                            QPointF(1.0, 1.0))
    return c


# QEasingCurve 는 값 타입이라 ``setEasingCurve()`` 가 복사한다 — 하나를 공유해도
# 안전하고, 호출부는 예전처럼 이 이름만 넘기면 된다.
EASE_PRIMARY = _ease_material_decelerate()    # 앱 기본 — 끝까지 보이는 감속
EASE_EXIT = _ease_material_accelerate()       # 퇴장 — 끝에서 빠져나가는 가속
# 11 — 사진 자리 → 화면 전체처럼 **먼 거리**를 커지는 모션.  기본 감속은 첫 프레임에
# 거리의 약 80% 를 가 버려 실측 녹화에서 '툭 나타남' 으로만 보였다.  더 완만한 감속이라야
# 어디서 커졌는지가 눈에 남는다.
EASE_MORPH = QEasingCurve(QEasingCurve.Type.OutCubic)


def enabled() -> bool:
    """모션을 실제로 그릴지 — **헤드리스(offscreen)에서만 False**.

    ★ 사용자 결정으로 모션은 항상 켜져 있다.  '모션 줄이기' 토글과 OS '동작 줄이기'
    감지(`set_reduce_motion`·`reduce_motion`·`os_reduce_motion`)는 제거했다.

    ★ 그러나 **offscreen 게이트는 남긴다.**  이것은 사용자 설정이 아니라 테스트·캡처의
    **결정성**이다: 지우면 모든 헤드리스 검증이 시간에 의존해 흔들리고, 크래시 회귀
    테스트가 `enabled` 를 켜서 애니메이션 경로를 재현하는 수단도 사라진다
    (`dev/tests/test_anim_lifetime.py` 가 이 방식으로 강제종료 2건을 재현한다).
    """
    return os.environ.get("QT_QPA_PLATFORM", "") != "offscreen"


def dur(ms: int) -> int:
    """변형 모션 강도로 지속시간 스케일(시간만)."""
    try:
        scale = float(theme.PROFILE.motion_scale)
    except Exception:
        scale = 1.0
    return max(1, int(ms * scale))


def snapshot(widget):
    """위젯을 **테마 배경 위에** 찍는다 — 전환 스냅샷 전용.

    ★ ``QWidget.grab()`` 을 그냥 쓰면 안 된다.  페이지는 자기 배경을 칠하지 않고
    (투명) **창이 뒤에서** 칠해 준다.  그래서 페이지만 떼어 grab 하면 빈 자리가
    Qt 기본 팔레트 Window 색 — 테마와 무관한 **밝은 회색 `#efefef`** — 으로 채워진다.

    그 스냅샷을 페이드인하면 전환 동안 화면 바탕(`theme.BG`)과 다른 색이 비친다.
    (예전 어두운 화면에서 실측: `w.grab()` 평균 밝기 85.7 ↔ 라이브 30.4 — 배경을 먼저
    칠하는 이 방식이 라이브와 같은 값을 냈다.)

    배경을 먼저 칠하고 ``DrawWindowBackground`` **없이** 자식만 렌더해야 한다 —
    그 플래그를 주면 Qt 가 다시 기본 팔레트로 덮어쓴다.
    """
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QColor, QPixmap, QRegion
    from PyQt6.QtWidgets import QWidget

    dpr = widget.devicePixelRatioF() or 1.0
    pm = QPixmap(max(1, int(widget.width() * dpr)),
                 max(1, int(widget.height() * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor(theme.BG))
    widget.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return pm


def transition_in(container, new_pixmap, *, forward: bool = True,
                  duration: int = DUR_PAGE_ENTER, slide_px: int = 20,
                  on_commit=None) -> None:
    """나가는 화면을 짧게 비우고, 들어오는 화면 스냅샷을 방향성(앞=우→, 뒤=좌→)
    슬라이드+페이드로 진입시킨다(01안).

    1. 나가는 **라이브** 화면이 ``DUR_PAGE_EXIT`` 동안 가속으로 흐려지며 반대쪽으로
       ``PAGE_EXIT_PX`` 밀린다.  예전엔 옛 화면을 그대로 둔 채 새 화면을 겹쳐 올려서,
       두 화면의 글자가 한동안 포개져 읽혔다.
    2. 그다음 새 화면 스냅샷이 ``duration`` 동안 불투명도 0→1 + 오프셋→0 으로 안착하고,
       끝에 ``on_commit`` 으로 스택을 실제 새 화면으로 바꾼 뒤 오버레이를 지운다(무플래시).

    ★ 나가는 화면은 **찍지 않는다**(이펙트로 흐린다).  옛 화면까지 스냅샷으로 뜨면
      전체 화면 렌더가 한 번 더 들어 첫 프레임이 60~70ms 멈췄다(1920×1080 실측).
    헤드리스면 즉시 ``on_commit`` 만."""
    from PyQt6.QtCore import Qt
    committed = {"done": False}

    def _commit():
        if not committed["done"]:
            committed["done"] = True
            if on_commit is not None:
                on_commit()

    if not enabled() or new_pixmap is None or new_pixmap.isNull():
        _commit()
        return
    prev = container.findChild(QLabel, "_pageFadeOverlay")
    if prev is not None:
        prev.deleteLater()
    # 끊긴 앞 전환이 남긴 '나가는 화면' 이펙트를 걷는다 — 남으면 그 화면이 투명한 채로 남는다.
    for page in container.findChildren(QWidget):
        eff = page.graphicsEffect()
        if eff is not None and eff.objectName() == "_pageExitEffect":
            page.setGraphicsEffect(None)

    overlay = QLabel(container)
    overlay.setObjectName("_pageFadeOverlay")
    overlay.setPixmap(new_pixmap)
    overlay.setScaledContents(False)
    overlay.setGeometry(container.rect())
    # ★ 마우스를 **흡수한다**.  전환 240ms 동안 스택이 담고 있는 것은 아직 **옛 페이지**라
    #   (main_window._show_page 가 스냅샷을 찍고 되돌려 놓는다), 마우스를 통과시키면
    #   눈에 보이는 새 화면이 아니라 방금 떠난 화면의 그 좌표 위젯이 눌린다.
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
    # ★ **키·단축키도 함께 흡수한다.**  예전엔 이걸 '각 페이지의 isVisible 가드'에
    #   위임한다고 적어 두었는데, 그 위임은 **구조적으로 성립하지 않는다**: 여기서
    #   문제인 것은 *나가는* 페이지이고 그쪽은 전환 246ms 내내 진짜로
    #   `isVisible()=True`(스택의 current 이기도 하다) — 가드가 구분할 수단이 없다.
    #   실측: 전환 중 Ctrl+Z 한 번에 MatchPage 가 마지막 결정을 되돌려 `finished` 가
    #   **2회** 나갔고, 엑셀 미탐 시트가 5행 → 10행(같은 사진 5장 중복)이 됐다.
    #   '보이는 그림과 라이브 위젯이 다르다'는 사실을 아는 곳은 여기뿐이므로 여기서 막는다.
    swallow = _InputSwallow(overlay)
    _app = QApplication.instance()
    if _app is not None:
        _app.installEventFilter(swallow)

    def _release_input():
        a = QApplication.instance()
        if a is not None:
            a.removeEventFilter(swallow)

    # 나가는 화면 — 이미 다른 이펙트를 가진 화면은 건드리지 않는다(위젯당 이펙트 하나).
    leaving = getattr(container, "currentWidget", lambda: None)()
    exit_eff = None
    if leaving is not None and leaving.graphicsEffect() is None:
        exit_eff = _OffsetFade(leaving)
        exit_eff.setObjectName("_pageExitEffect")
        exit_eff.configure(offset_from=(0, 0),
                           offset_to=(-PAGE_EXIT_PX if forward else PAGE_EXIT_PX, 0),
                           opacity_from=1.0, opacity_to=0.0)
        leaving.setGraphicsEffect(exit_eff)
        leave = QPropertyAnimation(exit_eff, b"progress", leaving)
        leave.setStartValue(0.0)
        leave.setEndValue(1.0)
        leave.setDuration(DUR_PAGE_EXIT)
        leave.setEasingCurve(EASE_EXIT)
        leave.start()

    def _drop_exit_effect():
        # 자기가 건 이펙트일 때만 — 그사이 다른 이펙트가 걸렸으면 그대로 둔다.
        try:
            if leaving is not None and leaving.graphicsEffect() is exit_eff:
                leaving.setGraphicsEffect(None)
        except RuntimeError:
            pass

    eff = QGraphicsOpacityEffect(overlay)
    eff.setOpacity(0.0)
    overlay.setGraphicsEffect(eff)
    dx = slide_px if forward else -slide_px
    overlay.move(dx, 0)
    overlay.show()
    overlay.raise_()

    # 불투명도와 위치를 **각자 속성 애니메이션**으로 — 람다 tick 이 형제 객체를 건드리지
    # 않게.  ★ 람다로 `eff.setOpacity` 를 부르면 tick 이 애니메이션의 **형제**(둘 다
    # overlay 의 자식)를 건드린다.  형제 사이의 파괴 순서는 보장되지 않아서, `eff` 가
    # 먼저 사라진 뒤 마지막 tick 이 발화하면 세그폴트가 난다(실측).  QPropertyAnimation
    # 은 대상을 QPointer 로 잡아 대상이 죽으면 스스로 멈춘다.
    fade = QPropertyAnimation(eff, b"opacity")
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setDuration(int(duration))
    fade.setEasingCurve(EASE_PRIMARY)
    slide = QPropertyAnimation(overlay, b"pos")
    slide.setStartValue(QPoint(dx, 0))
    slide.setEndValue(QPoint(0, 0))
    slide.setDuration(int(duration))
    slide.setEasingCurve(EASE_PRIMARY)
    # 나간 **뒤** 들어온다 — 두 화면이 포개진 채 읽히는 구간을 없앤다.
    enter = QParallelAnimationGroup()
    enter.addAnimation(fade)
    enter.addAnimation(slide)
    seq = QSequentialAnimationGroup(overlay)
    if exit_eff is not None:
        seq.addPause(DUR_PAGE_EXIT)
    seq.addAnimation(enter)

    def _finish():
        _release_input()                   # 키 잠금부터 푼다(아래가 실패해도 열리게)
        _drop_exit_effect()
        _commit()                          # 라이브 새 화면으로 전환 후
        overlay.deleteLater()              # 동일 프레임 스냅샷 제거(무플래시)

    seq.finished.connect(_finish)
    seq.start()


def animate_scroll(bar, target: int, *, duration: int = DUR_BASE) -> None:
    """스크롤바 값을 target 으로 부드럽게(OutQuart). 헤드리스면 즉시."""
    target = max(bar.minimum(), min(int(target), bar.maximum()))
    if not enabled():
        bar.setValue(target)
        return
    # ★ 스크롤바당 애니메이션 **하나만** 만들어 재사용한다.  예전엔 스크롤할 때마다
    #   새로 만들어(정지만 하고 파괴는 안 해) 스크롤바 아래에 죽은 객체가 쌓였다.
    # `value` 는 QAbstractSlider 의 실제 Qt 속성이라 속성 애니메이션으로 직접 몬다
    # (람다 없음 → 대상이 죽으면 Qt 가 알아서 멈춘다).
    anim = getattr(bar, "_motionScrollAnim", None)
    if anim is None:
        anim = QPropertyAnimation(bar, b"value", bar)
        bar._motionScrollAnim = anim
    anim.stop()
    # ★ 먼 거리는 **마지막 한 화면만** 굴린다.  240ms 에 수만 px 를 지나가면 내용이
    #   번져 읽을 수 없는 띠가 되고 도착 위치의 맥락도 안 생긴다(검토 600행에서 맨
    #   아래 행으로 점프).  두 화면 넘게 떨어져 있으면 목표 한 화면 앞으로 순간 이동한
    #   뒤 거기서부터 감속해 안착한다 — 방향과 도착은 보이고, 번짐은 없다.
    page = max(1, int(bar.pageStep()))
    cur = int(bar.value())
    if abs(target - cur) > 2 * page:
        bar.setValue(target - page if target > cur else target + page)
    # ★ 반드시 stop() **뒤에** 현재값을 시작값으로 잡는다 — 실행 중인 애니메이션의
    #   시작값 변경은 Qt 가 무시한다(스크롤이 옛 위치에서 튄다).
    anim.setStartValue(int(bar.value()))
    anim.setEndValue(target)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)
    bar.setProperty("_motionAnim", anim)
    anim.start()


def animate_height(widget, h0: int, h1: int, *, duration: int,
                   easing=EASE_PRIMARY) -> None:
    """레이아웃 안 위젯의 높이를 ``h0 → h1`` 로 몬다 — 검토 행 접힘·펼침(08·09안).

    높이를 최소=최대로 묶어 몰고, 끝나면 묶음을 풀어 레이아웃에 돌려준다.
    ★ 최대만 묶으면 줄어드는 쪽이 안 된다 — 레이아웃이 곧바로 새 sizeHint(작은 값)로
      내려 버린다.  최소도 함께 묶어야 그 사이가 보인다.
    ★ 애니메이션은 위젯당 하나를 재사용하고 tick 은 부모(위젯)만 건드린다(이 모듈 규약).
    헤드리스면 아무것도 안 한다(레이아웃이 곧바로 새 높이를 준다)."""
    if not enabled() or int(h0) == int(h1):
        return
    anim = getattr(widget, "_motionHeightAnim", None)
    if anim is None:
        widget._motionHeightMin = widget.minimumHeight()
        anim = QVariantAnimation(widget)
        anim.valueChanged.connect(lambda v: widget.setFixedHeight(int(v)))
        anim.finished.connect(lambda: _release_height(widget))
        widget._motionHeightAnim = anim
    anim.stop()
    anim.setStartValue(float(h0))
    anim.setEndValue(float(h1))
    anim.setDuration(int(duration))
    anim.setEasingCurve(easing)
    widget.setFixedHeight(int(h0))
    anim.start()


def _release_height(widget) -> None:
    widget.setMinimumHeight(getattr(widget, "_motionHeightMin", 0))
    widget.setMaximumHeight(16777215)          # QWIDGETSIZE_MAX


def ensure_visible_animated(area, host, widget, *, margin: int = 40) -> None:
    """스크롤 영역에서 widget 이 보이도록 — 필요한 만큼만 부드럽게 스크롤.

    헤드리스면 기존 ``ensureWidgetVisible`` 과 동일(바이트 폴백)."""
    if not enabled():
        area.ensureWidgetVisible(widget, 0, margin)
        return
    bar = area.verticalScrollBar()
    vp_h = area.viewport().height()
    top = widget.mapTo(host, QPoint(0, 0)).y()
    h = widget.height()
    cur = bar.value()
    if top - margin < cur:
        animate_scroll(bar, top - margin)
    elif top + h + margin > cur + vp_h:
        animate_scroll(bar, top + h + margin - vp_h)


def pulse(widget, *, attr: str = "_pulse", duration: int = DUR_SLOW) -> None:
    """widget 의 float 멤버(attr)를 1.0→0.0 으로 트윈하며 update() — 상태 펄스.

    widget.paintEvent 가 이 값을 읽어 틴트를 그린다. 헤드리스면 no-op."""
    if not enabled():
        setattr(widget, attr, 0.0)
        return
    setattr(widget, attr, 1.0)
    # ★ 위젯당 하나만 만들어 재사용(호출마다 새로 만들면 상태가 바뀔 때마다 쌓인다).
    #   시작값 1.0 은 위에서 명시적으로 되돌려 놓았다 — 중간값에서 재시작하면 펄스가
    #   약해진다(스크롤과 달리 여기는 '이어가기' 가 아니라 '다시 치기' 다).
    anim = getattr(widget, "_motionPulseAnim", None)
    if anim is None:
        anim = QVariantAnimation(widget)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(EASE_PRIMARY)
        widget._motionPulseAnim = anim
        _connect_pulse_tick(anim, widget, attr)
    anim.stop()
    anim.setDuration(dur(duration))
    anim.start()


def _connect_pulse_tick(anim, widget, attr: str) -> None:
    """★ tick 연결은 **생성 시 한 번만** — 재사용하며 매번 연결하면 N번 발화한다.

    여기만 람다가 남는다 — `attr` 은 Qt 속성이 아니라 파이썬 멤버라서
    QPropertyAnimation 으로 몰 수 없다.  안전한 이유는 tick 이 건드리는 대상이
    애니메이션의 **부모**(widget)이기 때문이다: Qt 는 자식을 먼저 파괴하므로 anim 이
    widget 보다 먼저 죽는다.  형제를 건드리면 순서가 보장되지 않아 위험하다."""
    anim.valueChanged.connect(lambda v: (setattr(widget, attr, float(v)),
                                         widget.update()))


# ── 등장/퇴장 — 오프셋 + 페이드를 **하나의 그래픽스 이펙트**로 ───────────────────
#
# ★ 왜 이펙트인가.  대상이 전부 **레이아웃이 자리를 정하는** 위젯이다(검토 행은
#   QVBoxLayout, 선별 사진은 QScrollArea 안).  `move()` 로 밀면 다음 레이아웃 패스가
#   즉시 되돌리고, 마진으로 밀면 sizeHint 가 바뀌어 이웃이 함께 출렁인다.
#   `QGraphicsEffect` 는 **그리기 단계**에만 끼어들어 레이아웃을 건드리지 않는다 —
#   시안이 26안에서 "레이아웃 불변 — 리플로 0" 으로 못박은 성질이 이것이다.
# ★ 위젯 하나에 이펙트는 **하나뿐**이다(Qt 제약).  그래서 이동과 페이드를 각각 따로
#   걸지 않고 한 클래스가 둘 다 한다 — 둘을 걸려다 조용히 하나가 사라지는 일을
#   애초에 없앤다.
class _OffsetFade(QGraphicsEffect):
    """``progress`` 0→1 로 (오프셋, 불투명도)를 함께 보간해 그린다.

    ``progress`` 는 **Qt 속성**이라 `QPropertyAnimation` 이 직접 몬다 — 람다 tick 이
    없고, 애니메이션이 형제를 건드릴 일도 없다(이 모듈의 수명 규칙)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._progress = 0.0
        self._from = QPointF(0.0, 0.0)
        self._to = QPointF(0.0, 0.0)
        self._op_from = 0.0
        self._op_to = 1.0
        self._sc_from = 1.0
        self._sc_to = 1.0
        self._origin = QPointF(0.0, 0.0)

    def configure(self, *, offset_from, offset_to, opacity_from, opacity_to,
                  scale_from=1.0, scale_to=1.0, origin=None):
        """``scale_*`` 는 ``origin``(위젯 좌표) 을 고정점으로 한 배율 — 1 이하만 쓴다
        (커지면 위젯 밖을 칠해 갱신 영역이 모자란다)."""
        self._from = QPointF(float(offset_from[0]), float(offset_from[1]))
        self._to = QPointF(float(offset_to[0]), float(offset_to[1]))
        self._op_from = float(opacity_from)
        self._op_to = float(opacity_to)
        self._sc_from = min(1.0, float(scale_from))
        self._sc_to = min(1.0, float(scale_to))
        if origin is not None:
            self._origin = QPointF(origin)

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = float(value)
        # ★ update() 가 아니라 updateBoundingRect() — 오프셋만큼 위젯 **밖**을 칠하므로
        #   갱신 영역을 넓히지 않으면 밀려난 부분이 잔상으로 남는다.
        self.updateBoundingRect()

    progress = pyqtProperty(float, _get_progress, _set_progress)

    def boundingRectFor(self, rect):  # noqa: N802
        r = rect.adjusted(
            min(0.0, self._from.x(), self._to.x()),
            min(0.0, self._from.y(), self._to.y()),
            max(0.0, self._from.x(), self._to.x()),
            max(0.0, self._from.y(), self._to.y()))
        # 고정점이 위젯 **밖**이면(행 사진 → 시트, 11안) 줄어든 그림이 위젯 밖에 놓인다.
        # 배율은 선형이라 가장 작은 배율의 사각형까지만 합치면 그 사이가 다 들어간다.
        s = min(self._sc_from, self._sc_to)
        if s < 1.0:
            o = self._origin
            small = QRectF(o.x() + (rect.left() - o.x()) * s,
                           o.y() + (rect.top() - o.y()) * s,
                           rect.width() * s, rect.height() * s)
            r = r.united(small.translated(self._from)).united(small.translated(self._to))
        return r

    def draw(self, painter):
        t = max(0.0, min(1.0, self._progress))
        dx = self._from.x() + (self._to.x() - self._from.x()) * t
        dy = self._from.y() + (self._to.y() - self._from.y()) * t
        op = self._op_from + (self._op_to - self._op_from) * t
        sc = self._sc_from + (self._sc_to - self._sc_from) * t
        if op >= 0.999 and abs(sc - 1.0) <= 1e-4 and abs(dx) < 0.01 and abs(dy) < 0.01:
            # 제자리·불투명 — 소스를 있는 그대로 그린다(픽스맵으로 굽지 않아 글자가
            # 흐려지지 않는다).
            self.drawSource(painter)
            return
        from PyQt6.QtCore import Qt
        # ★ 위젯 소스의 ``drawSource`` 는 painter 의 불투명도·변환을 **무시한다**(실측:
        #   불투명도 0.3·배율 0.3 을 줘도 원래 모습 그대로 그려졌다).  소스를 픽스맵으로
        #   받아 그 위에 상태를 입혀야 실제로 흐려지고·밀리고·커진다.
        pm, off = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, op)))
        painter.translate(dx, dy)
        if abs(sc - 1.0) > 1e-4:
            o = self._origin
            painter.translate(o)
            painter.scale(sc, sc)
            painter.translate(-o)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(off, pm)
        painter.restore()


def _run_offset_fade(widget, *, offset_from, offset_to, opacity_from,
                     opacity_to, duration, delay_ms, on_done,
                     easing=EASE_PRIMARY, scale_from=1.0, scale_to=1.0,
                     origin=None):
    """공통 몸통 — 이펙트를 걸고 한 번 재생한 뒤 **떼어 낸다**.

    ★ 끝나면 반드시 `setGraphicsEffect(None)`.  이펙트가 붙어 있는 동안 Qt 는 그
      위젯을 오프스크린으로 다시 그린다(로딩 패널이 같은 이유로 뗀다) — 검토 행
      수백 개에 남기면 스크롤이 무거워진다.
    ★ 애니메이션의 부모는 **위젯**이다(이펙트가 아니다).  끝에서 이펙트를 지우는데
      애니메이션이 이펙트의 자식이면 자기 `finished` 를 처리하는 도중 자신이
      삭제된다.  대상은 `QPropertyAnimation` 이 QPointer 로 들고 있어 안전하다.
    ★ 지속시간은 `dur()` 를 타지 않는다 — 시안이 밀리초로 정한 값이다.
    """
    if not enabled():
        if on_done is not None:
            on_done()
        return None
    eff = _OffsetFade(widget)
    eff.configure(offset_from=offset_from, offset_to=offset_to,
                  opacity_from=opacity_from, opacity_to=opacity_to,
                  scale_from=scale_from, scale_to=scale_to, origin=origin)
    widget.setGraphicsEffect(eff)          # 위젯이 소유권을 가져간다

    anim = QPropertyAnimation(eff, b"progress", widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(int(duration))
    anim.setEasingCurve(easing)

    def _finish():
        # ★ **자기가 건 이펙트일 때만** 뗀다.  연타로 같은 위젯에 새 등장이 걸리면
        #   Qt 가 옛 이펙트를 지우는데, 그 뒤 옛 애니메이션의 finished 가 도착해
        #   무조건 None 을 넣으면 **방금 건 새 이펙트가 사라진다**(사진이 그대로
        #   멈춰 보인다).  선별 화면의 →/← 연타에서 실제로 닿는 경로다.
        try:
            if widget.graphicsEffect() is eff:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass                            # 위젯이 이미 사라졌다
        if on_done is not None:
            on_done()

    anim.finished.connect(_finish)
    if delay_ms > 0:
        group = QSequentialAnimationGroup(widget)
        group.addPause(int(delay_ms))
        group.addAnimation(anim)
        group.start()
        return group
    anim.start()
    return anim


def rise_in(widget, *, delay_ms: int = 0, rise_px: int = RISE_PX,
            duration: int = DUR_RISE_IN, on_done=None):
    """아래에서 올라오며 나타난다 — 목록·카드의 **1회성** 등장(25안).

    `delay_ms` 로 스태거를 만든다(행마다 조금씩 늦춰 하나씩 안착)."""
    return _run_offset_fade(widget, offset_from=(0, rise_px), offset_to=(0, 0),
                            opacity_from=0.0, opacity_to=1.0,
                            duration=duration, delay_ms=delay_ms,
                            on_done=on_done)


def fade_in(widget, *, delay_ms: int = 0, duration: int = DUR_SWIPE_IN,
            on_done=None):
    """제자리 페이드인 — 스와이프로 떠난 자리에 들어오는 다음 장(26안).

    ``delay_ms`` 동안은 투명하다(CSS 의 ``backwards`` 와 같은 뜻) — 시안이
    `dsFadeIn .12s linear .18s backwards` 로 적은 그대로, 앞 사진이 다 빠진 **뒤**
    들어온다.  그 동안 자리는 떠나는 고스트가 덮고 있다.

    ★ 곡선도 시안 그대로 **등속**이다.  120ms 에 감속 곡선을 씌우면 12ms 만에 62%
      가 떠 사실상 '툭 나타남' 이 된다."""
    return _run_offset_fade(widget, offset_from=(0, 0), offset_to=(0, 0),
                            opacity_from=0.0, opacity_to=1.0,
                            duration=duration, delay_ms=delay_ms,
                            on_done=on_done,
                            easing=QEasingCurve(QEasingCurve.Type.Linear))


def swipe_out(widget, *, dx: int = 64, duration: int = DUR_SWIPE_OUT,
              on_done=None):
    """결정한 방향으로 밀려나며 사라진다 — 부호가 곧 방향이다(+오른쪽/−왼쪽, 26안).

    ★ 살아 있는 위젯에 걸지 마라.  다음 사진이 곧바로 그 자리에 들어오므로
      **떠나는 그림의 사본(고스트)** 에 걸어야 한다."""
    return _run_offset_fade(widget, offset_from=(0, 0), offset_to=(dx, 0),
                            opacity_from=1.0, opacity_to=0.0,
                            duration=duration, delay_ms=0, on_done=on_done,
                            easing=EASE_EXIT)


def grow_in(widget, *, origin, scale_from: float, opacity_from: float = 0.0,
            rise_px: int = 0, duration: int = DUR_SHEET_OPEN, on_done=None,
            easing=EASE_PRIMARY):
    """``origin``(위젯 좌표) 에서 커지며 나타난다 — 시트가 누른 자리에서 열림(04안)·
    '크게 보기' 가 행 사진 자리에서 커짐(11안).  어디서 왔는지가 눈에 이어진다."""
    return _run_offset_fade(widget, offset_from=(0, rise_px), offset_to=(0, 0),
                            opacity_from=opacity_from, opacity_to=1.0,
                            duration=duration, delay_ms=0, on_done=on_done,
                            easing=easing, scale_from=scale_from, scale_to=1.0,
                            origin=origin)


def shrink_out(widget, *, scale_to: float = SHEET_CLOSE_SCALE,
               drop_px: int = SHEET_CLOSE_DROP,
               duration: int = DUR_SHEET_CLOSE, on_done=None):
    """살짝 작아지며 아래로 빠져 사라진다(퇴장 = 가속, 05안).  가운데가 고정점."""
    return _run_offset_fade(widget, offset_from=(0, 0), offset_to=(0, drop_px),
                            opacity_from=1.0, opacity_to=0.0,
                            duration=duration, delay_ms=0, on_done=on_done,
                            easing=EASE_EXIT, scale_from=1.0, scale_to=scale_to,
                            origin=QPointF(widget.width() / 2,
                                           widget.height() / 2))


class PressEffect(QGraphicsEffect):
    """눌린 버튼 — ``progress`` 0→1 로 가운데 기준 축소 + 어둡게(14안).

    ★ 어둡게는 소스 픽스맵 **위에만** 덧칠한다(SourceAtop).  화면에 바로 검정을 깔면
      둥근 모서리 바깥의 배경까지 어두워진다."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._progress = 0.0

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = float(value)
        self.update()

    progress = pyqtProperty(float, _get_progress, _set_progress)

    def draw(self, painter):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor
        t = max(0.0, min(1.0, self._progress))
        if t <= 1e-4:
            self.drawSource(painter)
            return
        pm, off = self.sourcePixmap(Qt.CoordinateSystem.LogicalCoordinates)
        q = QPainter(pm)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        q.fillRect(pm.rect(), QColor(0, 0, 0, int(255 * PRESS_DARKEN * t)))
        q.end()
        r = self.boundingRect()
        c = r.center()
        sc = 1.0 - (1.0 - PRESS_SCALE) * t
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(c)
        painter.scale(sc, sc)
        painter.translate(-c)
        painter.drawPixmap(off, pm)
        painter.restore()


class _FlashFrame(QWidget):
    """대상 위에 테두리 + 옅은 면을 한 번 띄웠다가 지운다 — ``flash_outline`` 의 몸통.

    애니메이션은 이 위젯의 자식이고 tick 은 **부모(이 위젯)** 만 건드린다(파괴 순서가
    안전한 쪽).  끝나면 스스로 지운다."""

    def __init__(self, target, color, duration: int) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor
        super().__init__(target)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(target.rect())
        self._color = QColor(color)
        self._k = 1.0
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(int(duration))
        anim.setEasingCurve(EASE_PRIMARY)
        anim.valueChanged.connect(self._tick)
        anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        anim.start()

    def _tick(self, value) -> None:
        self._k = float(value)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QColor, QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(self._color)
        fill.setAlphaF(0.16 * self._k)
        edge = QColor(self._color)
        edge.setAlphaF(0.9 * self._k)
        pen = QPen(edge)
        pen.setWidthF(2.5)
        p.setPen(pen)
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), 8, 8)


def flash_outline(target, color, *, duration: int = DUR_STAMP_FLASH) -> None:
    """``target`` 이 한 번 반짝인다 — '방금 그것이 여기로 들어갔다'(06안).  헤드리스면 no-op."""
    if not enabled() or target is None:
        return
    for old in target.findChildren(_FlashFrame):
        old.deleteLater()                  # 연타 — 겹쳐 진해지지 않게 앞 것은 치운다
    _FlashFrame(target, color, duration)


def crossfade_from(widget, pixmap, *, duration: int = DUR_TILE_TOGGLE) -> None:
    """``pixmap``(바뀌기 전 모습)을 위에 얹고 걷어 내 새 모습으로 넘어간다(16안).

    QSS 로 칠하는 상태(``:checked``)는 Qt 가 한 프레임에 바꾸므로, 이렇게 옛 그림을
    덮었다가 흐려야 색이 '바뀌는' 것으로 보인다.  헤드리스면 no-op."""
    from PyQt6.QtCore import Qt
    if not enabled() or pixmap is None or pixmap.isNull():
        return
    ghost = QLabel(widget)
    ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    ghost.setPixmap(pixmap)
    ghost.setGeometry(widget.rect())
    ghost.show()
    ghost.raise_()
    _run_offset_fade(ghost, offset_from=(0, 0), offset_to=(0, 0),
                     opacity_from=1.0, opacity_to=0.0, duration=duration,
                     delay_ms=0, on_done=ghost.deleteLater)


class CheckBadge(QWidget):
    """선택 표시 — 강조색 원 + 체크(선).  켜질 때 톡 커지고, 꺼질 때 빠르게 들어간다(16안).

    ★ 체크는 **글자가 아니라 선**이다 — 동봉 폰트에 ✓ 글리프가 없어 두부(□)로 나올
      수 있다(`journey_rail` 과 같은 이유).
    ★ 애니메이션은 이 위젯의 자식이고 tick 은 부모(이 위젯)만 건드린다."""

    SIZE = 20

    def __init__(self, parent, *, on: bool = False) -> None:
        from PyQt6.QtCore import Qt
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.resize(self.SIZE, self.SIZE)
        self._t = 1.0 if on else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._tick)
        self.show()

    def set_on(self, on: bool) -> None:
        self._anim.stop()
        end = 1.0 if on else 0.0
        if not enabled() or not self.isVisible():
            self._tick(end)
            return
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(end)
        self._anim.setDuration(DUR_CHECK_POP if on else DUR_CHECK_OUT)
        self._anim.setEasingCurve(EASE_PRIMARY if on else EASE_EXIT)
        self._anim.start()

    def _tick(self, value) -> None:
        self._t = float(value)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QColor, QPen, QPolygonF
        if self._t <= 0.001:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self.SIZE / 2.0
        k = 0.6 + 0.4 * self._t
        p.setOpacity(min(1.0, self._t * 1.2))
        p.translate(c, c)
        p.scale(k, k)
        p.translate(-c, -c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.ACCENT))
        p.drawEllipse(QRectF(1, 1, self.SIZE - 2, self.SIZE - 2))
        pen = QPen(QColor(theme.ON_ACCENT))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(6, 10), QPointF(9, 13), QPointF(14, 7)]))


def fade_out(widget, *, duration: int = DUR_TOAST_OUT, on_done=None):
    """제자리에서 사라진다(퇴장 = 가속).  레이아웃은 그대로 — 자리를 비우지 않는다."""
    return _run_offset_fade(widget, offset_from=(0, 0), offset_to=(0, 0),
                            opacity_from=1.0, opacity_to=0.0,
                            duration=duration, delay_ms=0, on_done=on_done,
                            easing=EASE_EXIT)


class _ToastTick(QWidget):
    """토스트 글자 왼쪽에 그려지는 체크 — 한 획씩 그어진다(17안).

    색은 라벨 글자색을 따른다(복사됨=보조색, 확정됨=통과색).  라벨의 등장·퇴장
    이펙트는 자식까지 함께 그리므로 이 표시도 같이 떠오르고 사라진다."""

    W = 18

    def __init__(self, label) -> None:
        from PyQt6.QtCore import Qt
        super().__init__(label)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(EASE_PRIMARY)
        self._anim.valueChanged.connect(self._tick)

    def play(self) -> None:
        self.setGeometry(0, 0, self.W, self.parentWidget().height())
        self.show()
        self._anim.stop()
        self._anim.setDuration(DUR_TICK_DRAW)
        self._t = 0.0
        self._anim.start()

    def _tick(self, value) -> None:
        self._t = float(value)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        import math
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QPalette, QPen
        if self._t <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.parentWidget().palette().color(QPalette.ColorRole.WindowText))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cy = self.height() / 2.0
        a, b, c = QPointF(3, cy), QPointF(7, cy + 4), QPointF(15, cy - 5)
        l1 = math.dist((a.x(), a.y()), (b.x(), b.y()))
        l2 = math.dist((b.x(), b.y()), (c.x(), c.y()))
        drawn = (l1 + l2) * self._t
        if drawn <= l1:
            p.drawLine(a, a + (b - a) * (drawn / l1))
        else:
            p.drawLine(a, b)
            p.drawLine(b, b + (c - b) * ((drawn - l1) / l2))


def flash_text(label, text: str, *, hold_ms: int) -> None:
    """자리 예약 라벨에 짧은 알림을 띄웠다가 지운다 — '복사됨'·'확정됨' 토스트.

    아래에서 살짝 떠오르며 나타나고 왼쪽에 체크가 그어진 뒤(17안), ``hold_ms`` 뒤
    살짝 위로 빠지며 가속 페이드아웃하고 글자를 비운다.  예전엔 글자가 한 프레임에
    생기고 한 프레임에 사라져 '깜빡' 으로만 읽혔다.

    ★ 타이머는 **라벨이 부모인 하나**를 재사용한다.  호출마다 새 지연 호출을 걸면
      연속 복사 때 **앞 호출의 지우기가 뒤 알림을 일찍 지운다**(1.5초 간격 두 번 →
      두 번째 알림이 0.3초만 보였다).  재시작 한 번이면 늘 마지막 알림 기준이다.
    ★ 사라지는 도중 새 알림이 오면 옛 페이드의 지우기가 새 글자를 지우면 안 된다 —
      세대 번호로 막는다."""
    gen = getattr(label, "_motionToastGen", 0) + 1
    label._motionToastGen = gen
    label.setText(text)
    if enabled():
        tick = getattr(label, "_motionToastTick", None)
        if tick is None:
            tick = _ToastTick(label)
            label._motionToastTick = tick
            # 체크 자리를 글자 앞에 비워 둔다.
            m = label.contentsMargins()
            label.setContentsMargins(m.left() + _ToastTick.W + 2, m.top(),
                                     m.right(), m.bottom())
        tick.play()
    _run_offset_fade(label, offset_from=(0, TOAST_RISE_PX), offset_to=(0, 0),
                     opacity_from=0.0, opacity_to=1.0, duration=DUR_TOAST_IN,
                     delay_ms=0, on_done=None)
    timer = getattr(label, "_motionToastTimer", None)
    if timer is None:
        timer = QTimer(label)
        timer.setSingleShot(True)
        # tick 이 건드리는 것은 타이머의 **부모**(label)뿐 — 파괴 순서가 안전하다.
        timer.timeout.connect(lambda: _toast_out(label))
        label._motionToastTimer = timer
    timer.start(int(hold_ms))


def _toast_out(label) -> None:
    gen = getattr(label, "_motionToastGen", 0)

    def _clear():
        if getattr(label, "_motionToastGen", 0) == gen:
            label.clear()
            tick = getattr(label, "_motionToastTick", None)
            if tick is not None:
                tick.hide()

    _run_offset_fade(label, offset_from=(0, 0), offset_to=(0, -TOAST_LEAVE_PX),
                     opacity_from=1.0, opacity_to=0.0, duration=DUR_TOAST_OUT,
                     delay_ms=0, on_done=_clear, easing=EASE_EXIT)
