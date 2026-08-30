"""공용 모션 시스템 — 애플급 절제된 전환/스크롤/페이드.

원칙(사용자 지정):
- **ease-out**: 빠르게 시작해 끝에서 **오래** 감속.  기본 ``EASE_PRIMARY``
  (`cubic-bezier(.16,1,.3,1)` — 근거·실측표는 아래 ``_ease_out_long_tail`` 참조).
  단 **끝없이 도는 표시는 등속**이다(회전 스피너·로딩 줄무늬·결정형 바 채움) —
  반복 구간마다 가감속이 붙으면 맥박처럼 보인다.
  그리고 **로딩 오버레이는 두 축을 분리**한다 — 불투명도는 Linear(스크림 디밍이 슬램하지
  않게), 위치는 OutQuad(잔여 **이동량**이 눈에 보이게).  이유는 ``widgets/loading_overlay.py``
  주석과 ``docs/화면_디자인_도면.md`` 참조.
- 퇴장은 입장보다 짧게.  장식이 아니라 상태·공간 연속성을 전달.
- **결정론**: 헤드리스(offscreen) 면 모든 헬퍼가 즉시 적용(테스트/캡처가 흔들리지
  않게).  이 경우 동작 의미는 애니메이션 없이 동일.

변형별 속도는 ``theme.PROFILE.motion_scale`` 로 스케일(시간만, 이동량 아님)."""

from __future__ import annotations

import os

from PyQt6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF,
                          QPropertyAnimation, QSequentialAnimationGroup,
                          QVariantAnimation, pyqtProperty)
from PyQt6.QtWidgets import (QApplication, QGraphicsEffect,
                             QGraphicsOpacityEffect, QLabel)

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

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        try:
            etype = event.type()
        except RuntimeError:                    # 이미 파괴된 이벤트 — 막지 않는다
            return False
        return etype in BLOCKED_INPUT_EVENTS

# 지속시간 토큰 (ms) — motion_scale 로 스케일.
# ※ 한때 `DUR_FAST`·`EASE_LARGE`(OutExpo)·`EASE_LEGACY`(InOutCubic)·`fade_out_snapshot()`
#   도 있었으나 호출처가 **0곳**이었다.  '큰 이동은 OutExpo' 는 docstring 에만 존재하는
#   규칙이었고 `CollapsibleSection` 은 이제 OutQuart 를 쓴다 — 쓰지 않는 토큰은 규칙처럼
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
# ★ 사용자 결정으로 **기존 값 유지**.  구조개편 24안은 이 페이드를 280ms 로 줄이자고
#   했지만(긴 페이드 동안 어중간한 혼합색이 머문다는 근거), 실제로 보고 되돌렸다 —
#   같은 안의 나머지(재색 정지시간 125→~30ms 분할, 팔레트 ①, 토글 노브)는 그대로다.
DUR_RECOLOR = 700        # 색 모드(어두운 화면) 전환
# 구조개편 21·23·25·26·27안이 **눈으로 정한** 지속시간 — 위 셋과 같이 dur() 를 타지
# 않는다(시안이 밀리초로 명시한 값이라 스케일을 곱하면 지정과 실제가 갈라진다).
DUR_RAIL_LEAD = 140      # 21안 — 여정 레일 눈금이 페이지보다 **먼저** 채워지는 시간
DUR_FINISH_TICK = 200    # 23안 — 수 분짜리 작업이 100% 에 닿는 순간의 마침 틱
DUR_RISE_IN = 220        # 25안 — 검토 행 페이드-라이즈
STAGGER_RISE_MS = 60     # 25안 — 행 사이 지연
DUR_SWIPE_OUT = 180      # 26안 — 결정한 사진이 방향으로 밀려나는 시간
DUR_SWIPE_IN = 120       # 26안 — 다음 사진 페이드인
DUR_KNOB = 180           # 24안 — 다크 토글 노브 슬라이드(누른 즉시의 답)


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


def _ease_soft_decelerate() -> QEasingCurve:
    """더 완만한 감속 (CSS `cubic-bezier(.33,1,.68,1)`) — **색 모드 전환 전용**.

    | 시점 | 기본(머티리얼) | 이 곡선 |
    |---|---|---|
    | t=0.10 | 0.621 | 0.272 |
    | t=0.25 | 0.832 | 0.577 |
    | t=0.50 | 0.950 | 0.872 |
    | **보이는 구간** | 65% | **73%** |

    색 모드 전환은 **화면 전체의 밝기가 뒤집히는** 가장 큰 변화라, 기본 곡선으로도
    앞부분이 급했다(사용자 지정).  출발을 늦추고 변화를 더 고르게 퍼뜨린다.
    """
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.33, 1.0), QPointF(0.68, 1.0),
                            QPointF(1.0, 1.0))
    return c


# QEasingCurve 는 값 타입이라 ``setEasingCurve()`` 가 복사한다 — 하나를 공유해도
# 안전하고, 호출부는 예전처럼 이 이름만 넘기면 된다.
EASE_PRIMARY = _ease_material_decelerate()    # 앱 기본 — 끝까지 보이는 감속
EASE_SOFT = _ease_soft_decelerate()           # 색 모드 전환 — 더 완만하게


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

    다크 모드에서 그 스냅샷을 페이드인하면 '밝은 화면이 먼저 보였다가 어두워지는'
    것으로 보인다.  실측(다크, SelectPage 진입):

    | 방식 | 평균 밝기 |
    |---|---|
    | `w.grab()` (옛 방식) | 85.7 |
    | `stack.grab()` · `WA_StyledBackground` · repolish | 85.7 (전부 그대로) |
    | **`theme.BG` 로 채우고 `DrawChildren` 렌더** | **30.4** |
    | 실제 라이브 화면 | 30.4 |

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
                  duration: int = DUR_BASE, slide_px: int = 20,
                  on_commit=None) -> None:
    """들어오는 화면 스냅샷을 방향성(앞=우→, 뒤=좌→) 슬라이드+페이드로 진입시킨다.

    현재(나가는) 라이브 화면은 아래에 그대로 두고, 새 화면 스냅샷을 위에서
    불투명도 0→1 + 오프셋→0 으로 안착시킨다(들어오는 안무 = C8 지적 보완).  안착
    끝에 ``on_commit`` 으로 스택을 실제 새 화면으로 전환하고 오버레이 제거(무플래시).
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

    eff = QGraphicsOpacityEffect(overlay)
    eff.setOpacity(0.0)
    overlay.setGraphicsEffect(eff)
    dx = slide_px if forward else -slide_px
    overlay.move(dx, 0)
    overlay.show()
    overlay.raise_()

    # 불투명도와 위치를 **각자 속성 애니메이션**으로 — 람다 tick 이 형제 객체를 건드리지
    # 않게(위 crossfade_from 주석의 파괴 순서 함정과 같은 이유).
    fade = QPropertyAnimation(eff, b"opacity", overlay)
    fade.setStartValue(0.0)
    fade.setEndValue(1.0)
    fade.setDuration(dur(duration))
    fade.setEasingCurve(EASE_PRIMARY)
    slide = QPropertyAnimation(overlay, b"pos", overlay)
    slide.setStartValue(QPoint(dx, 0))
    slide.setEndValue(QPoint(0, 0))
    slide.setDuration(dur(duration))
    slide.setEasingCurve(EASE_PRIMARY)

    def _finish():
        _release_input()                   # 키 잠금부터 푼다(아래가 실패해도 열리게)
        _commit()                          # 라이브 새 화면으로 전환 후
        overlay.deleteLater()              # 동일 프레임 스냅샷 제거(무플래시)

    fade.finished.connect(_finish)
    fade.start()
    slide.start()


def crossfade_from(container, old_pixmap, *, duration: int = DUR_RECOLOR,
                   on_done=None) -> None:
    """**옛 화면 스냅샷**을 위에 얹어 빼면서 새 화면을 드러낸다(색만 바뀌는 전환).

    :func:`transition_in` 과 방향이 반대다 — 저쪽은 들어오는 화면을 얹어 넣고, 이쪽은
    **나가는 화면을 걷어낸다.**  다크 모드 전환처럼 레이아웃은 그대로이고 색만 바뀔 때는
    이게 맞다: 위치 이동을 섞으면 '화면이 옮겨졌다'는 거짓 신호가 된다(슬라이드 없음).

    호출부는 **먼저** 새 색으로 화면을 갈아 끼운 뒤(즉시 교체) 이 함수에 옛 스냅샷을
    넘긴다.  ``on_done`` 은 성공·즉시완료·비활성 어느 경로에서도 **정확히 한 번** 불린다 —
    호출부가 여기서 전환 잠금을 풀기 때문에, 안 불리면 토글이 영구히 잠긴다.
    """
    from PyQt6.QtCore import Qt
    done_once = {"v": False}

    def _done():
        if not done_once["v"]:
            done_once["v"] = True
            if on_done is not None:
                on_done()

    if not enabled() or old_pixmap is None or old_pixmap.isNull():
        _done()
        return

    prev = container.findChild(QLabel, "_pageRecolorOverlay")
    if prev is not None:
        prev.deleteLater()

    overlay = QLabel(container)
    overlay.setObjectName("_pageRecolorOverlay")
    overlay.setPixmap(old_pixmap)
    overlay.setScaledContents(False)
    overlay.setGeometry(container.rect())
    # ★ 마우스를 **흡수한다**.  전환 240ms 동안 스택이 담고 있는 것은 아직 **옛 페이지**라
    #   (main_window._show_page 가 스냅샷을 찍고 되돌려 놓는다), 마우스를 통과시키면
    #   눈에 보이는 새 화면이 아니라 방금 떠난 화면의 그 좌표 위젯이 눌린다.
    #   (키·단축키는 여전히 통과한다 — 그건 각 페이지의 isVisible 가드가 막는다.)
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
    eff = QGraphicsOpacityEffect(overlay)
    eff.setOpacity(1.0)
    overlay.setGraphicsEffect(eff)
    overlay.show()
    overlay.raise_()

    # ★ **QPropertyAnimation** 을 쓴다(QVariantAnimation + 람다가 아니다).
    #   Qt 가 대상(`eff`)을 QPointer 로 잡아 두므로 대상이 죽으면 애니메이션이 스스로
    #   멈춘다 — tick 이 죽은 객체로 들어갈 수 없다.
    #
    #   ★ 이게 왜 필요했나: 람다로 `eff.setOpacity` 를 부르면 tick 이 애니메이션의
    #   **형제**(둘 다 overlay 의 자식)를 건드린다.  형제 사이의 파괴 순서는 보장되지
    #   않아서, `eff` 가 먼저 사라진 뒤 마지막 tick 이 발화하면 세그폴트가 난다(실측).
    #   규칙: **tick 은 자기 부모(또는 부모의 상태)만 건드린다 — 형제는 안 된다.**
    anim = QPropertyAnimation(eff, b"opacity", overlay)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setDuration(int(duration))     # 사용자 지정 실측 ms — 스케일 없음
    anim.setEasingCurve(EASE_SOFT)      # 색 전환은 더 완만하게(사용자 지정)

    def _finish():
        overlay.deleteLater()
        _done()

    anim.finished.connect(_finish)
    anim.start()


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
    # ★ 반드시 stop() **뒤에** 현재값을 시작값으로 잡는다 — 실행 중인 애니메이션의
    #   시작값 변경은 Qt 가 무시한다(스크롤이 옛 위치에서 튄다).
    anim.setStartValue(int(bar.value()))
    anim.setEndValue(target)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)
    bar.setProperty("_motionAnim", anim)
    anim.start()


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

    def configure(self, *, offset_from, offset_to, opacity_from, opacity_to):
        self._from = QPointF(float(offset_from[0]), float(offset_from[1]))
        self._to = QPointF(float(offset_to[0]), float(offset_to[1]))
        self._op_from = float(opacity_from)
        self._op_to = float(opacity_to)

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = float(value)
        # ★ update() 가 아니라 updateBoundingRect() — 오프셋만큼 위젯 **밖**을 칠하므로
        #   갱신 영역을 넓히지 않으면 밀려난 부분이 잔상으로 남는다.
        self.updateBoundingRect()

    progress = pyqtProperty(float, _get_progress, _set_progress)

    def boundingRectFor(self, rect):  # noqa: N802
        return rect.adjusted(
            min(0.0, self._from.x(), self._to.x()),
            min(0.0, self._from.y(), self._to.y()),
            max(0.0, self._from.x(), self._to.x()),
            max(0.0, self._from.y(), self._to.y()))

    def draw(self, painter):
        t = max(0.0, min(1.0, self._progress))
        dx = self._from.x() + (self._to.x() - self._from.x()) * t
        dy = self._from.y() + (self._to.y() - self._from.y()) * t
        op = self._op_from + (self._op_to - self._op_from) * t
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, op)))
        painter.translate(dx, dy)
        # drawSource 는 소스를 있는 그대로 그린다 — 픽스맵으로 굽지 않아 글자가
        # 흐려지지 않는다.
        self.drawSource(painter)
        painter.restore()


def _run_offset_fade(widget, *, offset_from, offset_to, opacity_from,
                     opacity_to, duration, delay_ms, on_done):
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
                  opacity_from=opacity_from, opacity_to=opacity_to)
    widget.setGraphicsEffect(eff)          # 위젯이 소유권을 가져간다

    anim = QPropertyAnimation(eff, b"progress", widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(int(duration))
    anim.setEasingCurve(EASE_PRIMARY)

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


def rise_in(widget, *, delay_ms: int = 0, rise_px: int = 18,
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
    들어온다.  그 동안 자리는 떠나는 고스트가 덮고 있다."""
    return _run_offset_fade(widget, offset_from=(0, 0), offset_to=(0, 0),
                            opacity_from=0.0, opacity_to=1.0,
                            duration=duration, delay_ms=delay_ms,
                            on_done=on_done)


def swipe_out(widget, *, dx: int = 64, duration: int = DUR_SWIPE_OUT,
              on_done=None):
    """결정한 방향으로 밀려나며 사라진다 — 부호가 곧 방향이다(+오른쪽/−왼쪽, 26안).

    ★ 살아 있는 위젯에 걸지 마라.  다음 사진이 곧바로 그 자리에 들어오므로
      **떠나는 그림의 사본(고스트)** 에 걸어야 한다."""
    return _run_offset_fade(widget, offset_from=(0, 0), offset_to=(dx, 0),
                            opacity_from=1.0, opacity_to=0.0,
                            duration=duration, delay_ms=0, on_done=on_done)
