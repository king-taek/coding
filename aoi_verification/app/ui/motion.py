"""공용 모션 시스템 — 애플급 절제된 전환/스크롤/페이드.

원칙(사용자 지정):
- **ease-out**: 빠르게 시작해 끝에서 감속(OutQuart 기본, 큰 이동은 OutExpo).
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
DUR_FAST = 140
DUR_BASE = 200
DUR_SLOW = 280

EASE_PRIMARY = QEasingCurve.Type.OutQuart     # 빠르게→끝에서 감속
EASE_LARGE = QEasingCurve.Type.OutExpo        # 큰 이동
EASE_LEGACY = QEasingCurve.Type.InOutCubic    # 접기류(기존)

_reduce_motion = False


def set_reduce_motion(flag: bool) -> None:
    global _reduce_motion
    _reduce_motion = bool(flag)


def reduce_motion() -> bool:
    return _reduce_motion


def enabled() -> bool:
    """헤드리스(offscreen)·모션 줄이기 면 False → 모든 헬퍼가 즉시 적용."""
    if os.environ.get("QT_QPA_PLATFORM", "") == "offscreen":
        return False
    return not _reduce_motion


def dur(ms: int) -> int:
    """변형 모션 강도로 지속시간 스케일(시간만)."""
    try:
        scale = float(theme.PROFILE.motion_scale)
    except Exception:
        scale = 1.0
    return max(1, int(ms * scale))


# ---------------------------------------------------------------------------
def fade_out_snapshot(container, pixmap, *, duration: int = DUR_BASE,
                      slide_px: int = 8) -> None:
    """``container`` 위에 나가는 화면 스냅샷을 얹고 불투명도 1→0 + 하향 슬라이드.

    새 화면은 이미 표시된 상태에서 스냅샷만 페이드아웃하므로 라이브 위젯에
    이펙트를 걸지 않는다(QScrollArea 리페인트 함정 회피). offscreen/reduced 면
    아무 것도 하지 않는다(호출부가 이미 setCurrentWidget 완료)."""
    if not enabled() or pixmap is None or pixmap.isNull():
        return
    # 이전 오버레이가 남아 있으면 제거(단일 비행).
    prev = container.findChild(QLabel, "_pageFadeOverlay")
    if prev is not None:
        prev.deleteLater()

    overlay = QLabel(container)
    overlay.setObjectName("_pageFadeOverlay")
    overlay.setPixmap(pixmap)
    overlay.setScaledContents(False)
    overlay.setGeometry(container.rect())
    from PyQt6.QtCore import Qt
    overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    eff = QGraphicsOpacityEffect(overlay)
    eff.setOpacity(1.0)
    overlay.setGraphicsEffect(eff)
    overlay.show()
    overlay.raise_()

    anim = QVariantAnimation(overlay)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(dur(duration))
    anim.setEasingCurve(EASE_PRIMARY)

    def _step(t):
        eff.setOpacity(1.0 - float(t))
        overlay.move(0, int(slide_px * float(t)))

    anim.valueChanged.connect(_step)
    anim.finished.connect(overlay.deleteLater)
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
