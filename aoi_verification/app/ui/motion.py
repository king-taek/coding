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

from PyQt6.QtCore import (QEasingCurve, QPoint, QPointF, QPropertyAnimation,
                          QVariantAnimation)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel

from . import theme

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
DUR_BASE = 300           # 실제 240ms(motion_scale 0.8) — 안착 ≈161ms
DUR_SLOW = 400
DUR_SWITCH = 200         # 토글 손잡이 이동 — `switch_row.ToggleSwitch` 가 쓰는 값


def _ease_out_long_tail() -> QEasingCurve:
    """빠르게 출발해 **오래 감속하며** 안착하는 곡선 (CSS `cubic-bezier(.16,1,.3,1)`).

    ★ 왜 표준 ``OutQuart`` 가 아닌가 — 두 지표를 함께 봐야 한다(실측):

    | 곡선 | t=0.1 진행률(출발 속도) | 마지막 10%에 쓰는 **시간** |
    |---|---|---|
    | OutQuad            | 0.190 | 31.6% |
    | OutCubic           | 0.271 | 46.4% |
    | OutQuart (이전 기본) | 0.344 | 56.2% |
    | **이 곡선**          | **0.494** | **67.1%** |

    사용자 요구는 "처음엔 빠르게, 나중엔 천천히 끝나게" 였다.  이 곡선은 출발이
    가장 빠르면서 감속 구간이 **시간상** 가장 길다 — 두 요구를 동시에 만족하는
    유일한 후보다.

    ※ 반대로 '남은 **거리**'는 OutQuart 보다 작다.  그래서 잔여 이동량을 눈으로
    확인해야 하는 자리(로딩 패널의 32px 상승)는 여전히 OutQuad 를 쓴다 —
    ``widgets/loading_overlay.py`` 주석과 ``dev/tests/test_loading_panel.py`` 참조.
    """
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    c.addCubicBezierSegment(QPointF(0.16, 1.0), QPointF(0.30, 1.0),
                            QPointF(1.0, 1.0))
    return c


# QEasingCurve 는 값 타입이라 ``setEasingCurve()`` 가 복사한다 — 하나를 공유해도
# 안전하고, 호출부는 예전처럼 이 이름만 넘기면 된다.
EASE_PRIMARY = _ease_out_long_tail()          # 빠르게→끝에서 오래 감속


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
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
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
        _commit()                          # 라이브 새 화면으로 전환 후
        overlay.deleteLater()              # 동일 프레임 스냅샷 제거(무플래시)

    fade.finished.connect(_finish)
    fade.start()
    slide.start()


DUR_RECOLOR = 420        # 색 모드 전환 — 사용자 요청으로 느리게(220 → 420), 끝에서 감속


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
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
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
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)

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
    prev = bar.property("_motionAnim")
    if prev is not None:
        try:
            prev.stop()
        except Exception:
            pass
    # `value` 는 QAbstractSlider 의 실제 Qt 속성이라 속성 애니메이션으로 직접 몬다
    # (람다 없음 → 대상이 죽으면 Qt 가 알아서 멈춘다).
    anim = QPropertyAnimation(bar, b"value", bar)
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
    anim = QVariantAnimation(widget)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)
    # ★ 여기만 람다가 남는다 — `attr` 은 Qt 속성이 아니라 파이썬 멤버라서
    #   QPropertyAnimation 으로 몰 수 없다.  안전한 이유는 tick 이 건드리는 대상이
    #   애니메이션의 **부모**(widget)이기 때문이다: Qt 는 자식을 먼저 파괴하므로 anim 이
    #   widget 보다 먼저 죽는다.  형제를 건드리면 순서가 보장되지 않아 위험하다.
    anim.valueChanged.connect(lambda v: (setattr(widget, attr, float(v)),
                                         widget.update()))
    anim.start()
