"""공용 모션 시스템 — 애플급 절제된 전환/스크롤/페이드.

원칙(사용자 지정):
- **ease-out**: 빠르게 시작해 끝에서 감속.  기본 ``EASE_PRIMARY``(OutQuart).
  단 **로딩 오버레이는 두 축을 분리**한다 — 불투명도는 Linear(스크림 디밍이 슬램하지
  않게), 위치는 OutQuad(끝에서 감속하며 안착).  이유는 ``widgets/loading_overlay.py``
  주석과 ``docs/화면_디자인_도면.md`` 참조.
- 퇴장은 입장보다 짧게.  장식이 아니라 상태·공간 연속성을 전달.
- **결정론**: 헤드리스(offscreen) 또는 '모션 줄이기' 면 모든 헬퍼가 즉시 적용
  (테스트/캡처가 흔들리지 않게).  이 경우 동작 의미는 애니메이션 없이 동일.

변형별 속도는 ``theme.PROFILE.motion_scale`` 로 스케일(시간만, 이동량 아님)."""

from __future__ import annotations

import os

from PyQt6.QtCore import QEasingCurve, QPoint, QVariantAnimation
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QLabel

from . import theme

# 지속시간 토큰 (ms) — motion_scale 로 스케일.
# ※ 한때 `DUR_FAST`·`EASE_LARGE`(OutExpo)·`EASE_LEGACY`(InOutCubic)·`fade_out_snapshot()`
#   도 있었으나 호출처가 **0곳**이었다.  '큰 이동은 OutExpo' 는 docstring 에만 존재하는
#   규칙이었고 `CollapsibleSection` 은 이제 OutQuart 를 쓴다 — 쓰지 않는 토큰은 규칙처럼
#   읽혀 다음 사람을 오도하므로 지웠다.  필요해지면 그때 다시 만들면 된다.
DUR_BASE = 200
DUR_SLOW = 280

EASE_PRIMARY = QEasingCurve.Type.OutQuart     # 빠르게→끝에서 감속

_reduce_motion = False
_os_reduce_cache: bool | None = None


def set_reduce_motion(flag: bool) -> None:
    global _reduce_motion
    _reduce_motion = bool(flag)


def reduce_motion() -> bool:
    return _reduce_motion


def os_reduce_motion() -> bool:
    """OS 의 '동작 줄이기/애니메이션 표시 끄기' 설정을 최선 노력으로 감지(1회 캐시).

    Windows: SPI_GETCLIENTAREAANIMATION(=False 면 애니메이션 끔). 그 외/실패 시 False
    (앱 토글에만 의존). 접근성 포워드 — 사용자가 OS 에서 끄면 앱도 자동으로 따른다."""
    global _os_reduce_cache
    if _os_reduce_cache is not None:
        return _os_reduce_cache
    result = False
    try:
        import ctypes
        SPI_GETCLIENTAREAANIMATION = 0x1042
        enabled_flag = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled_flag), 0)
        if ok:
            result = (enabled_flag.value == 0)     # 애니메이션 꺼짐 → 줄이기 True
    except Exception:
        result = False                             # 비 Windows·실패 → 앱 토글만
    _os_reduce_cache = result
    return result


def enabled() -> bool:
    """헤드리스(offscreen)·모션 줄이기·OS 동작 줄이기 면 False → 헬퍼 즉시 적용."""
    if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        return False
    if _reduce_motion:
        return False
    return not os_reduce_motion()


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
    offscreen/reduced 면 즉시 ``on_commit`` 만."""
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

    anim = QVariantAnimation(overlay)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)

    def _step(t):
        t = float(t)
        eff.setOpacity(t)
        overlay.move(int(dx * (1.0 - t)), 0)

    def _finish():
        _commit()                          # 라이브 새 화면으로 전환 후
        overlay.deleteLater()              # 동일 프레임 스냅샷 제거(무플래시)

    anim.valueChanged.connect(_step)
    anim.finished.connect(_finish)
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


def animate_scroll(bar, target: int, *, duration: int = DUR_BASE) -> None:
    """스크롤바 값을 target 으로 부드럽게(OutQuart). reduced/headless 면 즉시."""
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
    anim = QVariantAnimation(bar)
    anim.setStartValue(int(bar.value()))
    anim.setEndValue(target)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)
    anim.valueChanged.connect(lambda v: bar.setValue(int(v)))
    bar.setProperty("_motionAnim", anim)
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)


def ensure_visible_animated(area, host, widget, *, margin: int = 40) -> None:
    """스크롤 영역에서 widget 이 보이도록 — 필요한 만큼만 부드럽게 스크롤.

    reduced/headless 면 기존 ``ensureWidgetVisible`` 과 동일(바이트 폴백)."""
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

    widget.paintEvent 가 이 값을 읽어 틴트를 그린다. reduced/headless 면 no-op."""
    if not enabled():
        setattr(widget, attr, 0.0)
        return
    setattr(widget, attr, 1.0)
    anim = QVariantAnimation(widget)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)
    anim.valueChanged.connect(lambda v: (setattr(widget, attr, float(v)),
                                         widget.update()))
    anim.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
