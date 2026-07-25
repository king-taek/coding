"""모션 시스템 — 헤드리스/모션줄이기 시 즉시 적용(결정론) + 로딩 오버레이 진리표."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QScrollBar          # noqa: E402

from aoi_verification.app.ui import motion                    # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_enabled_false_when_offscreen(qapp):
    # 헤드리스(offscreen)에서는 항상 즉시 적용.
    assert motion.enabled() is False


def test_animate_scroll_instant_when_disabled(qapp):
    bar = QScrollBar()
    bar.setRange(0, 1000)
    motion.animate_scroll(bar, 500)
    assert bar.value() == 500           # 애니메이션 없이 즉시
    motion.animate_scroll(bar, 99999)   # 범위 클램프
    assert bar.value() == 1000


def test_motion_is_always_on_except_headless(qapp, monkeypatch):
    """★ 모션은 **항상 켜진다**(사용자 결정) — 유일한 예외가 헤드리스다.

    한때 '모션 줄이기' 토글과 OS '동작 줄이기' 감지가 `enabled()` 를 껐다.  둘 다
    제거했지만 **offscreen 게이트는 남긴다**: 사용자 설정이 아니라 테스트·캡처의
    결정성이고, 크래시 회귀 테스트(`test_anim_lifetime`)가 이 게이트를 뒤집어
    애니메이션 경로를 재현한다."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert motion.enabled() is True
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert motion.enabled() is False


def test_dur_scales_with_profile(qapp):
    from aoi_verification.app.ui import theme
    # '도면' 은 담백한 모션(scale 0.8) — 지속시간이 그만큼 짧아진다.
    assert motion.dur(200) == int(200 * theme.PROFILE.motion_scale)


def test_transition_in_commits_immediately_when_disabled(qapp):
    """헤드리스면 진입 애니 없이 즉시 on_commit(스택 전환) — 결정론."""
    from PyQt6.QtWidgets import QWidget
    from PyQt6.QtGui import QPixmap
    container = QWidget()
    pix = QPixmap(10, 10)
    pix.fill()
    done = {"v": False}
    motion.transition_in(container, pix,
                         on_commit=lambda: done.__setitem__("v", True))
    assert done["v"] is True
    container.deleteLater()


def test_loading_overlay_instant_when_disabled(qapp):
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    from PyQt6.QtWidgets import QWidget
    host = QWidget()
    ov = LoadingOverlay(host)
    ov.show_overlay("작업 중")
    assert ov._fade == 1.0              # 즉시 완전 표시
    assert not ov.isHidden()           # show() 호출됨
    ov.set_progress(0, 0, "탐색")        # busy
    assert not ov._busy.isHidden() and ov._progress.isHidden()
    ov.set_progress(5, 10, "처리")       # 결정형
    assert not ov._progress.isHidden() and ov._progress.maximum() == 10
    ov.hide_overlay()
    assert ov.isHidden()               # 즉시 숨김
    ov.deleteLater()
    host.deleteLater()


# ── 곡선 — '빠르게 시작해서 천천히 끝난다' 를 수치로 고정 ──────────────────
def _settle_time_fraction(curve, remaining: float = 0.1) -> float:
    """마지막 ``remaining`` 만큼의 거리를 남기고부터 끝까지 쓰는 **시간 비율**.

    사용자가 체감하는 '천천히 끝난다' 는 남은 **거리**가 아니라 감속에 쓰는
    **시간**이다.  이분 탐색으로 y=1-remaining 이 되는 t 를 찾는다."""
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if curve.valueForProgress(mid) < 1.0 - remaining:
            lo = mid
        else:
            hi = mid
    return 1.0 - (lo + hi) / 2.0


def test_ease_primary_starts_fast_and_settles_long(qapp):
    """★ 두 요구를 **동시에** 만족해야 한다 — 하나만 보면 잘못 고른다.

    이전 기본값 OutQuart 는 둘 다 이 곡선보다 못하다(출발 0.344 / 안착 56.2%).
    ※ '남은 거리' 로 비교하면 정반대 결론이 나온다 — 그래서 시간으로 잰다."""
    from PyQt6.QtCore import QEasingCurve
    out_quart = QEasingCurve(QEasingCurve.Type.OutQuart)

    # (1) 더 빠르게 출발한다.
    assert motion.EASE_PRIMARY.valueForProgress(0.1) > \
        out_quart.valueForProgress(0.1)
    # (2) 감속에 더 **오래** 머문다.
    assert _settle_time_fraction(motion.EASE_PRIMARY) > \
        _settle_time_fraction(out_quart)
    # 절반 이상을 안착에 쓴다 — '뚝 끊기지 않는다' 의 정량 기준.
    assert _settle_time_fraction(motion.EASE_PRIMARY) > 0.6
    # 끝점은 정확히 1.0 이어야 한다(베지어 제어점 실수 방지).
    assert motion.EASE_PRIMARY.valueForProgress(1.0) == pytest.approx(1.0)


def test_transition_duration_makes_the_settle_visible(qapp):
    """곡선만 바꾸고 시간을 그대로 두면 안착이 눈에 안 보인다.

    안착 구간(전체의 60%+)이 최소 140ms 는 돼야 사람이 '천천히 끝났다' 고 느낀다."""
    settle_ms = motion.dur(motion.DUR_BASE) * _settle_time_fraction(
        motion.EASE_PRIMARY)
    assert settle_ms >= 140, f"안착이 {settle_ms:.0f}ms 뿐 — 여전히 뚝 끊긴다"


def test_looping_indicators_stay_linear(qapp):
    """끝없이 도는 표시는 **등속**이다 — 반복마다 가감속이 붙으면 맥박처럼 보인다."""
    from PyQt6.QtCore import QEasingCurve
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    from PyQt6.QtWidgets import QWidget
    host = QWidget()
    ov = LoadingOverlay(host)
    assert ov._busy._anim.easingCurve().type() == QEasingCurve.Type.Linear
    assert ov._val_anim.easingCurve().type() == QEasingCurve.Type.Linear
    # 스크림 페이드도 등속(디밍 슬램 방지) — 별도 테스트가 고정하는 계약.
    assert ov._fade_anim.easingCurve().type() == QEasingCurve.Type.Linear
    host.deleteLater()
