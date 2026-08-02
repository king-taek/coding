"""모션 곡선·지속시간 계약 — "곡선인데 등속처럼 보인다" 재발 방지.

사용자 지적: 색 모드 전환이 **툭** 바뀌고 끝난다.  조사해 보니 곡선이 없어서가 아니라
옛 곡선 `cubic-bezier(.16,1,.3,1)` 이 전체 시간의 절반 지점에서 이미 97.2% 진행돼,
남은 절반의 변화가 0.03 이었다 — **불투명도 채널에서는 보이지 않는 양**이다.

그래서 이 파일이 지키는 것은 '곡선이 걸려 있는가' 가 아니라 **변화가 시간 전체에
퍼져 있는가**(= 보이는 구간)다.  이 지표가 없으면 다음 사람이 또 '더 강한 감속'을
골라 같은 함정에 빠진다.

Qt 만 필요하고 창을 띄우지 않는다 — 빠른 레인에서 돈다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtCore")

from aoi_verification.app.ui import motion                     # noqa: E402


def _visible_span(curve) -> float:
    """진행률 0.02 → 0.98 에 쓰는 **시간의 비율**.

    이 값이 작다 = 나머지 시간에는 화면에서 아무 일도 일어나지 않는다 = 컷처럼 읽힌다.
    """
    lo = hi = None
    for i in range(1001):
        t = i / 1000
        v = curve.valueForProgress(t)
        if v >= 0.02 and lo is None:
            lo = t
        if v <= 0.98:
            hi = t
    return hi - lo


# ── 곡선 ────────────────────────────────────────────────────────────────
def test_primary_curve_matches_the_documented_table():
    """``motion._ease_material_decelerate`` 독스트링의 표를 그대로 못 박는다.

    ★ 표를 **눈대중으로** 적으면 안 된다 — 곡선을 바꿀 때 계산해서 갱신하라는 뜻으로
    여기에 같은 숫자를 둔다(한 번 실제로 어긋난 적이 있다)."""
    c = motion.EASE_PRIMARY
    for t, expect in ((0.10, 0.621), (0.25, 0.832),
                      (0.50, 0.950), (0.75, 0.991)):
        assert c.valueForProgress(t) == pytest.approx(expect, abs=0.002), \
            f"t={t} 에서 곡선이 표와 다르다"


def test_soft_curve_matches_the_documented_table():
    c = motion.EASE_SOFT
    for t, expect in ((0.10, 0.272), (0.25, 0.577), (0.50, 0.872)):
        assert c.valueForProgress(t) == pytest.approx(expect, abs=0.002)


def test_curves_are_decelerating():
    """처음에 빠르고 끝으로 갈수록 느려진다(사용자 요구의 핵심)."""
    for name, c in (("기본", motion.EASE_PRIMARY), ("완만", motion.EASE_SOFT)):
        first = c.valueForProgress(0.25) - c.valueForProgress(0.0)
        last = c.valueForProgress(1.0) - c.valueForProgress(0.75)
        assert first > last * 3, f"{name} 곡선이 감속하지 않는다"


def test_change_is_spread_across_the_duration():
    """★ 이 파일의 핵심 지표 — 죽은 꼬리가 길면 컷처럼 보인다.

    옛 곡선 `cubic-bezier(.16,1,.3,1)` 은 이 값이 54% 였다(나머지 46% 의 시간 동안
    화면이 멈춰 있었다).  60% 를 하한으로 둔다."""
    for name, c in (("기본", motion.EASE_PRIMARY), ("완만", motion.EASE_SOFT)):
        span = _visible_span(c)
        assert span >= 0.60, (
            f"{name} 곡선의 보이는 구간이 {span:.0%} — 나머지 시간에 아무 일도"
            " 일어나지 않아 '툭 끊긴다'고 읽힌다")


def test_recolor_uses_the_softer_curve():
    """색 모드 전환은 화면 전체 밝기가 뒤집히는 가장 큰 변화 — 더 완만하게(사용자 지정)."""
    assert _visible_span(motion.EASE_SOFT) > _visible_span(motion.EASE_PRIMARY)
    src = inspect.getsource(motion.crossfade_from)
    assert "EASE_SOFT" in src


# ── 지속시간 ────────────────────────────────────────────────────────────
def test_user_specified_durations():
    """사용자가 실측 ms 로 지정한 값 — 바꾸려면 사용자에게 물어야 한다."""
    assert motion.DUR_SHEET == 400        # 작은 화면 팝업
    assert motion.DUR_LOADING == 500      # 로딩 화면 팝업
    assert motion.DUR_RECOLOR == 700      # 색 모드 전환


def test_specified_durations_are_not_scaled():
    """★ ``dur()`` 을 태우면 안 된다 — 0.8 이 곱해져 지정값과 실제값이 갈라진다.

    호출부가 `motion.dur(DUR_SHEET)` 로 바뀌면 400 지정이 320 으로 나간다.  숫자만
    지키고 경로를 안 지키면 이 계약은 헛돈다."""
    assert motion.dur(motion.DUR_SHEET) != motion.DUR_SHEET, \
        "motion_scale 이 1.0 이면 이 가드가 아무것도 못 잡는다"

    from aoi_verification.app.ui.widgets import loading_overlay, sheet_host

    for mod, fn in ((sheet_host.SheetHost, "_enter"),
                    (sheet_host.SheetHost, "_fade_out_ghost")):
        src = inspect.getsource(getattr(mod, fn))
        assert "motion.dur(" not in src, f"{fn} 이 지정 지속시간에 스케일을 먹인다"

    src = inspect.getsource(loading_overlay.LoadingOverlay.show_overlay)
    assert "motion.dur(self.FADE_IN_MS)" not in src
    assert "motion.dur(self.RISE_IN_MS)" not in src


def test_loading_panel_entrance_is_the_specified_length():
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay

    assert LoadingOverlay.RISE_IN_MS == motion.DUR_LOADING
    # 두 속도(빠르게 나타나고 → 천천히 안착)를 유지한다.
    assert LoadingOverlay.FADE_IN_MS < LoadingOverlay.RISE_IN_MS


# ── 예외: 끝없이 도는 표시는 등속 ────────────────────────────────────────
def test_indeterminate_indicators_stay_linear():
    """사용자 결정: 스피너·무한 진행 줄무늬는 **등속 유지**.

    곡선을 주면 한 바퀴마다 빨라졌다 느려져 맥박처럼 뛴다."""
    from PyQt6.QtCore import QEasingCurve
    from aoi_verification.app.ui.widgets.loading_overlay import _BusyStripe

    src = inspect.getsource(_BusyStripe.__init__)
    assert "QEasingCurve.Type.Linear" in src, "무한 진행 표시가 등속을 잃었다"
    assert QEasingCurve(QEasingCurve.Type.Linear).valueForProgress(0.5) == 0.5
