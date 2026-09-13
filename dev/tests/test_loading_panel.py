"""로딩 오버레이 — 반투명 스크림 + 패널이 아래에서 중앙으로 안착(사용자 요청).

CLAUDE.md 로딩 계약(set_progress 의미·결정형/busy)은 그대로 유지되는지도 함께 지킨다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor                             # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget          # noqa: E402

from aoi_verification.app import i18n                        # noqa: E402
from aoi_verification.app.ui import theme                    # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (  # noqa: E402
    LoadingOverlay)

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _spin(qapp, ms: int) -> None:
    """이벤트 루프를 ``ms`` 동안 돌린다(tween 이 진행되게)."""
    from PyQt6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


def _settle_ms(ov) -> int:
    """추격 상한 + 여유 — 마지막 보고 뒤 이만큼 돌리면 표시값이 목표에 닿아 있어야 한다."""
    return ov.VAL_TWEEN_MAX_MS + 150


def _overlay(qapp, w=900, h=600):
    host = QWidget()
    host.resize(w, h)
    host.show()
    ov = LoadingOverlay(host)
    for _ in range(4):
        qapp.processEvents()
    return host, ov


def test_panel_settles_at_center_and_starts_below(qapp):
    """위치 진행도 1 이면 중앙, <1 이면 중앙보다 **아래** — '아래에서 올라와 안착'.

    ★ 위치는 `_on_rise`(불투명도와 분리된 축)가 몰기 때문에 여기서 그것으로 검증한다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        for _ in range(6):
            qapp.processEvents()

        def dy():
            g = ov._panel.geometry()
            return g.y() + g.height() // 2 - ov.height() // 2

        ov._on_rise(1.0)
        assert abs(dy()) <= 2, f"안착 위치가 중앙이 아니다: dy={dy()}"
        ov._on_rise(0.0)
        assert dy() > 0, "시작 위치가 중앙보다 아래여야 한다"
        assert dy() <= ov.RISE_IN_PX + 2
        ov._on_rise(0.5)
        mid = dy()
        assert 0 < mid < ov.RISE_IN_PX, f"중간 프레임이 사이에 없다: {mid}"
    finally:
        host.deleteLater()


def test_position_settles_slower_than_opacity(qapp):
    """★ 사용자 요구는 두 개의 속도다 — "빠르게 나타나고 마지막에 천천히 도착".

    불투명도와 위치를 같은 t 로 몰면 24px 이동이 페이드가 끝나기 전에 소진돼 '안착'이
    사라진다.  위치 지속시간이 페이드보다 길어야 그 문장이 성립한다."""
    assert LoadingOverlay.RISE_IN_MS > LoadingOverlay.FADE_IN_MS
    # 페이드는 등속(스크림 디밍이 슬램하지 않게), 위치는 끝에서 감속.
    host, ov = _overlay(qapp)
    try:
        from PyQt6.QtCore import QEasingCurve
        assert ov._fade_anim.easingCurve().type() == QEasingCurve.Type.Linear
        rise = ov._rise_anim.easingCurve()
        # ★ 곡선 **이름**을 고정하지 않는다 — 중요한 건 "페이드가 끝나는 시점에 위치
        #   이동이 눈에 보일 만큼 남아 있는가"다.  OutQuart 는 이 검사를 통과하지
        #   못했다(잔여 0.8px) — 두 속도가 숫자로만 존재했다.
        t_at_fade_end = LoadingOverlay.FADE_IN_MS / LoadingOverlay.RISE_IN_MS
        remaining = LoadingOverlay.RISE_IN_PX * (1.0 - rise.valueForProgress(
            t_at_fade_end))
        assert remaining >= 4.0, (
            f"페이드 종료 시점 잔여 이동 {remaining:.1f}px — 안착이 눈에 보이지 않는다")
    finally:
        host.deleteLater()


def test_bar_stagger_lands_after_the_panel(qapp):
    """진행바는 패널이 안착한 **뒤에** 들어와야 계층이 순서대로 읽힌다.

    이전 60ms 지연은 진행바가 패널보다 먼저 도착해 순서가 뒤집혀 있었다."""
    assert LoadingOverlay.BAR_STAGGER_MS >= LoadingOverlay.FADE_IN_MS * 0.5


def test_busy_comet_head_leads(qapp):
    """혜성의 **머리**(진행 방향 앞쪽)가 가장 진해야 한다 — 거꾸로 나면 안 된다."""
    import inspect

    from aoi_verification.app.ui.widgets import loading_overlay as lo
    import re
    src = inspect.getsource(lo._BusyStripe.paintEvent)
    m = re.search(r"enumerate\(\(([^)]*)\)\)", src)
    assert m, "혜성 알파 튜플을 찾지 못했다"
    alphas = [float(x) for x in m.group(1).split(",")]
    # enumerate 순서 = 왼(꼬리) → 오른(머리) 이므로 알파는 **증가**해야 한다.
    assert alphas == sorted(alphas), f"혜성 꼬리 알파가 감소 순서(거꾸로)다: {alphas}"
    # 꼬리도 트랙에서 보여야 한다 — 0.25 는 묻혔다.
    assert alphas[0] >= 0.4, f"꼬리 최저 알파 {alphas[0]} 가 트랙에 묻힌다"


def test_exit_travels_less_than_entry(qapp):
    """퇴장은 살짝만 내려간다(입장보다 짧고 얕게)."""
    assert LoadingOverlay.RISE_OUT_PX < LoadingOverlay.RISE_IN_PX


def test_scrim_lets_the_page_show_through(qapp):
    """스크림이 화면을 '전부 가리지' 않아야 한다 — 완전 불투명 금지."""
    for mode in theme.color_mode_keys():
        theme.set_color_mode(mode)
        assert theme.SCRIM_RGBA[3] < 255
        assert theme.SCRIM_RGBA[3] <= 130, f"{mode}: 너무 진하다"
    theme.set_color_mode("light")


def test_panel_has_own_surface_for_readability(qapp):
    """스크림이 옅어도 읽히도록 패널은 자기 면을 갖는다(QSS role)."""
    host, ov = _overlay(qapp)
    try:
        assert ov._panel.property("role") == "loadingPanel"
        assert 'QWidget[role="loadingPanel"]' in _QSS
        out = theme.render_qss(_QSS)
        assert "$" not in out
    finally:
        host.deleteLater()


# ---------------------------------------------------------------------------
# ★ 원형 웨이퍼 맵(개선안 2a) — 격자를 웨이퍼 윤곽으로 자르고, 채움은 중앙에서
#   바깥으로, busy 는 중앙에서 번지는 동심 물결.
# ---------------------------------------------------------------------------
def test_wafer_map_is_clipped_to_a_circle(qapp):
    """13×13 격자에서 반지름 밖의 모서리 다이는 없다 — 원이어야 웨이퍼다."""
    from aoi_verification.app.ui.widgets.loading_overlay import _WaferMap
    wm = _WaferMap()
    n = wm.die_count()
    assert 0 < n < wm.GRID * wm.GRID, f"격자를 자르지 않았다({n})"
    for col, row, rad in wm._dies:
        assert rad <= wm.RADIUS
        assert 0 <= col < wm.GRID and 0 <= row < wm.GRID
    # 네 모서리는 반드시 잘려 나간다.
    corners = {(0, 0), (0, wm.GRID - 1), (wm.GRID - 1, 0), (wm.GRID - 1, wm.GRID - 1)}
    assert not corners & {(c, r) for c, r, _ in wm._dies}
    wm.deleteLater()


def test_wafer_map_fills_from_the_centre_outwards(qapp):
    """채움 순서는 반지름 오름차순이다 — 한 번 켜진 다이보다 먼 다이가 먼저 켜지지 않는다."""
    from aoi_verification.app.ui.widgets.loading_overlay import _WaferMap
    wm = _WaferMap()
    radii = [rad for _c, _r, rad in wm._dies]
    assert radii == sorted(radii), "채움 순서가 중앙→바깥이 아니다"
    assert wm._dies[0][:2] == (wm.GRID // 2, wm.GRID // 2), "첫 다이가 중앙이 아니다"
    wm.setRange(0, 100)
    wm.setValue(0)
    assert wm.lit_count() == 0
    wm.setValue(50)
    assert 0 < wm.lit_count() < wm.die_count()
    wm.setValue(100)
    assert wm.lit_count() == wm.die_count(), "100% 인데 다 켜지지 않았다"
    wm.deleteLater()


def test_wafer_map_ripple_keeps_running_in_determinate(qapp, monkeypatch):
    """물결은 busy 에서 시작해 결정형으로 바뀌어도 **계속** 돈다(사용자 요청 — 파동은
    항상, 진행은 채움으로).  완료색이 켜지면 그때 멈춘다."""
    from aoi_verification.app.ui import motion
    from aoi_verification.app.ui.widgets.loading_overlay import _WaferMap
    monkeypatch.setattr(motion, "enabled", lambda: True)
    wm = _WaferMap()
    from PyQt6.QtCore import QEasingCurve
    # 등속 — 끝에서 감속하는 숨쉬기는 '거의 끝났다' 는 거짓 신호다.
    assert wm._anim.easingCurve().type() == QEasingCurve.Type.Linear
    assert wm._anim.loopCount() == -1
    wm.set_busy(True)
    assert wm._anim.state() != wm._anim.State.Stopped
    # 물결은 반지름이 클수록 늦게 온다(동심 물결) — 바깥 링은 중앙 다이의 파형을
    # 반지름 × RIPPLE_MS 만큼 **시간 이동**한 것과 같다.
    shift = wm.RIPPLE_MS / wm.PULSE_MS
    for rad in (1.0, 3.0, 6.0):
        wm._phase = 0.35
        outer = wm._pulse_alpha(rad)
        wm._phase = (0.35 - rad * shift) % 1.0
        assert abs(outer - wm._pulse_alpha(0.0)) < 1e-9
    wm._phase = 0.0
    assert wm._pulse_alpha(0.0) < wm._pulse_alpha(0.5), "중앙이 먼저 밝아지지 않는다"
    lo, hi = wm.PULSE_MIN_ALPHA, 1.0
    for rad in (0.0, 2.5, 6.6):
        for ph in (0.0, 0.25, 0.5, 0.9):
            wm._phase = ph
            assert lo - 1e-9 <= wm._pulse_alpha(rad) <= hi + 1e-9
    wm.set_busy(False)
    assert wm._anim.state() != wm._anim.State.Stopped, "결정형으로 바뀌자 물결이 멈췄다"
    # 켜진 다이는 파란색으로 **얕게**(.62↔1) 숨쉰다 — busy 의 .18↔1 은 너무 요란했다
    # (사용자가 3안 비교 후 '진폭 축소' 채택).  대기 다이는 회색 정지(paint 가 line2).
    assert wm.PULSE_MIN_ALPHA == 0.62, "busy 와 결정형이 같은 얕은 진폭이어야 한다"
    wm._phase = 0.0
    assert abs(wm._pulse_alpha(0.0) - wm.PULSE_MIN_ALPHA) < 1e-9      # 골
    wm._phase = 0.5
    assert abs(wm._pulse_alpha(0.0) - 1.0) < 1e-9                     # 마루
    # busy → 결정형 전환은 200ms 회색 페이드로 — 전환 시각이 기록된다.
    wm.set_busy(True)
    before = wm._clock.elapsed()
    wm.set_busy(False)
    assert 0 <= wm._greyed_at - before < 50 and wm.GREY_FADE_MS == 200
    from aoi_verification.app.ui.widgets.loading_overlay import _mix
    from PyQt6.QtGui import QColor
    grey, blue = QColor(theme.LINE2), QColor(theme.ACCENT)
    assert _mix(grey, blue, 0.0).name() == grey.name()
    assert _mix(grey, blue, 1.0).name() == blue.name()
    wm.set_done(True)
    assert wm._anim.state() == wm._anim.State.Stopped, "완료색 위에서 물결이 돈다"
    wm.start()
    assert wm._anim.state() == wm._anim.State.Stopped, "완료 뒤 start 가 물결을 되살렸다"
    wm.set_done(False)
    # stop() 은 애니메이션만 멈추고 busy 여부는 남긴다 — 숨었다 다시 보일 때 되살린다.
    wm.set_busy(True)
    wm.stop()
    assert wm.is_busy() and wm._anim.state() == wm._anim.State.Stopped
    wm.start()
    assert wm._anim.state() != wm._anim.State.Stopped
    wm.deleteLater()


def test_stop_button_is_panel_grade_32(qapp):
    """[중지] 는 앱 공통 액션 등급(44)이 아니라 패널 안 보조 조작 32px 이다(개선안 2a).

    ★ 44 로 되돌리면 머리줄이 표제보다 무거워져 패널의 초점이 버튼으로 옮겨 간다.
    화면의 유일한 조작이라 오클릭 위험은 없고 WCAG 최소 목표 크기(24)는 넘는다."""
    theme.apply_to_app(qapp)                 # QSS min-height 가 이겨도 32 여야 한다
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업", cancelable=True)
        for _ in range(4):
            qapp.processEvents()
        assert ov.STOP_BTN_H == 32
        assert ov._cancel_btn.height() == 32, f"실측 {ov._cancel_btn.height()}px"
        assert ov._cancel_btn.width() == 120
    finally:
        host.deleteLater()


def test_wafer_map_sits_beside_the_step_list_inside_the_panel(qapp):
    """맵은 패널 안 왼쪽, 오른쪽 열(스텝·수치)은 맵과 나란히 — 패널 폭은 그대로 424."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업", step=(2, 3), steps=("a", "b", "c"))
        ov.set_progress(3, 10, "작업")
        for _ in range(4):
            qapp.processEvents()
        assert ov._panel.width() == ov.PANEL_W
        wm = ov._wafer.geometry()
        steps = ov._steps.geometry()
        nums = ov._bar_host.geometry()
        assert wm.width() == wm.height() == ov._wafer.SIZE
        assert wm.left() >= ov.PANEL_BORDER_PX + 24
        assert steps.left() >= wm.right() + ov.BODY_GAP_PX, "스텝이 맵과 겹친다"
        assert nums.left() == steps.left(), "수치 묶음이 스텝과 다른 열에 있다"
        assert nums.bottom() <= wm.bottom() + 1, "수치가 맵보다 아래로 내려갔다"
        assert wm.bottom() < ov._panel.height() - ov.PANEL_BORDER_PX
    finally:
        host.deleteLater()


def test_no_nested_graphics_effect(qapp):
    """패널 이펙트 안에 또 이펙트를 겹치면 QPainter 충돌 경고가 난다 — 금지."""
    host, ov = _overlay(qapp)
    try:
        assert ov._bar_host.graphicsEffect() is None
    finally:
        host.deleteLater()


def test_effect_is_detached_when_not_fading(qapp):
    """★ 이펙트가 걸려 있는 동안 스피너 1프레임마다 패널 **전체**가 오프스크린으로 다시
    렌더된다.  페이드가 끝나면(또는 모션이 꺼져 있으면) 떼어 낸다 — 불투명도 1.0 이라
    시각적으로 달라지는 것은 없다."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")            # 헤드리스 → 즉시 최종 상태
        assert ov._panel.graphicsEffect() is None
        assert ov._content_eff is None
        # 페이드를 걸 때만 다시 설치된다.
        ov._attach_effect()
        assert ov._panel.graphicsEffect() is not None
        ov._detach_effect()
        assert ov._panel.graphicsEffect() is None
    finally:
        host.deleteLater()


def test_bar_slide_keeps_panel_height_stable(qapp):
    """진행바 스태거는 위/아래 여백을 맞바꿔 패널 크기를 흔들지 않는다."""
    host, ov = _overlay(qapp)
    try:
        ov._set_bar_slide(0.0)
        m0 = ov._bar_lay.contentsMargins()
        ov._set_bar_slide(1.0)
        m1 = ov._bar_lay.contentsMargins()
        assert m0.top() + m0.bottom() == m1.top() + m1.bottom()
        assert m0.top() > m1.top()          # 아래에서 밀려 올라온다
    finally:
        host.deleteLater()


def test_headless_snaps_to_final_state(qapp):
    """오프스크린/모션 줄이기 면 모션 없이 즉시 최종 상태(테스트·캡처 결정론)."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        assert ov._fade == 1.0
        assert ov._rise == 1.0
        m = ov._bar_lay.contentsMargins()
        assert m.top() == 0                  # 슬라이드가 끝난 상태
        # 래치 이내에 감추라고 하면 아직 살아 있어야 한다(깜빡임 방지).
        ov.hide_overlay()
        assert not ov.isHidden()
        # 래치를 지난 것으로 만들면 모션이 꺼져 있으니 페이드 없이 즉시 종료.
        ov._hide_pending = False
        ov._begin_fade_out(ov._show_token)
        assert ov.isHidden(), "모션이 꺼져 있으면 페이드 없이 즉시 종료해야 한다"
    finally:
        host.deleteLater()


def test_min_display_latch_applies_without_motion(qapp):
    """★ 최소 표시 래치는 모션이 아니라 **타이밍 위생**이다.

    `motion.enabled()` 안쪽에 두면 '모션 줄이기'를 켠 사용자만 깜빡임을 그대로 받는다 —
    모션에 민감해서 끈 사람에게 정확히 깜빡임을 주는 셈이다."""
    import inspect

    from aoi_verification.app.ui.widgets import loading_overlay as lo
    src = inspect.getsource(lo.LoadingOverlay.hide_overlay)
    # 주석은 제외하고 **코드 줄**만 본다(주석에 토큰 이름이 설명으로 등장한다).
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.strip().startswith("#"))
    assert "motion.enabled" not in code, "래치가 모션 게이트 안에 들어갔다"
    assert "MIN_DISPLAY_MS" in code

    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("초단타")
        ov.hide_overlay()               # 래치 이내 → 아직 감추지 않는다
        assert not ov.isHidden(), "래치가 동작하지 않았다(깜빡임)"
        assert ov._hide_pending is True
    finally:
        host.deleteLater()


def test_loading_contract_preserved(qapp):
    """set_progress 의미(결정형/busy·증가 tween·범위변경 스냅)는 불변."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 0, "탐색")               # total<=0 → busy
        assert ov._wafer.is_busy()
        ov.set_progress(5, 10, "처리")              # 결정형
        assert not ov._wafer.is_busy() and ov._wafer.maximum() == 10
        assert ov._wafer.value() == 5
        ov.set_progress(9, 10)                      # 증가
        assert ov._wafer.value() == 9
        ov.set_progress(2, 40)                      # 범위 변경 → 스냅
        assert ov._wafer.maximum() == 40 and ov._wafer.value() == 2
    finally:
        host.deleteLater()


def test_show_overlay_starts_busy_not_a_frozen_zero_bar(qapp):
    """★ ``show_overlay`` 만 부르는 호출부에서 **바가 0 에 얼어 있으면 안 된다.**

    예전 눈금은 결정형 바(range 0..100, value 0)가 보이는 채로 busy 가 숨어 있었다.
    그래서 총량을 모르는 작업(OpenVINO 설치 · KLA 파일명 읽기 · 선계산 대기)에서는
    바가 영원히 0 이었다 — 사용자가 보고한 "바가 채워지지 않는" 버그이고,
    CLAUDE.md 로딩 계약 위반이다.
    """
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("설치 중")             # set_progress 를 부르지 않는 호출부
        assert ov._wafer.is_busy(), "busy 물결이 없다 — 맵이 0 에 얼어 있다"
        assert ov._count_label.text() == "", "총량을 모르는데 숫자를 적었다"
        assert ov._pct_label.text() == "", "총량을 모르는데 퍼센트를 적었다"

        # 총량이 알려지면 결정형으로 **승격**된다.
        ov.set_progress(3, 10, "처리")
        assert not ov._wafer.is_busy()
        assert ov._wafer.maximum() == 10
        assert ov._count_label.text() == i18n.KO.LOADING_COUNT_FMT.format(
            done=3, total=10)
        assert ov._pct_label.text() == "30%"

        # 다시 총량을 잃으면 busy 로 되돌아온다(왕복).
        ov.set_progress(0, 0, "마무리")
        assert ov._wafer.is_busy()

        # 감췄다 다시 띄워도 busy 로 시작한다(이전 결정형 상태가 새지 않게).
        ov.set_progress(7, 10)
        ov._finish_hide()
        ov.show_overlay("다시 시작")
        assert ov._wafer.is_busy()
    finally:
        host.deleteLater()


def test_dense_updates_track_the_real_progress(qapp, monkeypatch):
    """★ 촘촘한 갱신에서 바가 **실제 진행을 따라가야** 한다.

    실사용 버그: '매치 실패 사진 검토'의 유사도 재계산에서 로딩바 틀은 보이는데 채워지지
    않았다.  원인은 그 다이얼로그가 아니라 여기다 — 증가마다 240ms tween 을 **재시작**
    하는데 갱신이 30ms 마다 오므로 tween 이 몇 프레임만 돌고 처음부터 다시 시작한다.
    표시값이 목표를 영원히 못 따라가고, 작업이 끝나면 오버레이가 내려가 **끝까지 찬 적이
    없다**.  실측(수정 전): done 5/50 에 표시 0% · 15/50 에 20% · 50/50 에 88%.

    '부드러움'은 갱신이 **드물 때만** 성립하는 성질이다.  촘촘할 때는 정확한 위치가
    부드러움보다 중요하다 — 진행률은 장식이 아니라 정보다.

    ★ 모션을 켜야 재현된다(offscreen 은 tween 을 안 탄다).
    """
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        need = 50
        ov.show_overlay("점수 계산 중")
        ov.set_progress(0, need, "점수 계산 중")
        from PyQt6.QtCore import QElapsedTimer
        worst = 0.0
        for i in range(1, need + 1):
            t = QElapsedTimer()                 # 후보 1장 재계산 ≈ 30ms
            t.start()
            while t.elapsed() < 30:
                qapp.processEvents()
            ov.set_progress(i, need, f"{i}/{need}")
            qapp.processEvents()
            lag = abs(ov._wafer.value() - i) / need
            worst = max(worst, lag)
        assert worst <= 0.10, f"표시값이 실제 진행에서 최대 {worst * 100:.1f}% 벗어났다"
        _spin(qapp, _settle_ms(ov))             # 완료도 추격한다 — 상한 안에 닿는다
        assert ov._wafer.value() == need, \
            f"작업이 끝났는데 바가 {ov._wafer.value()}/{need} 에서 멈췄다"
    finally:
        host.deleteLater()


def test_completion_glides_in_and_exit_waits_for_it(qapp, monkeypatch):
    """★ 완료(done ≥ total)도 **점진적으로** 찬다(사용자 요청) — 대신 퇴장이 그 추격을
    기다린다.  예전 규칙('완료는 스냅')이 막던 잔여 버그 — 마지막 증가의 tween 이
    `_finish_hide` 의 `stop()` 에 잘려 4/5 로 끝나던 것 — 는 `hide_overlay` 가 추격의
    남은 시간만큼 래치를 늘려서 막는다.  표시값은 단조 증가하고 끝은 정확히 total 이다."""
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        need = 5
        ov.show_overlay("점수 계산 중")
        ov.set_progress(0, need, "점수 계산 중")
        for i in range(1, need + 1):
            _spin(qapp, 400)                    # 한 칸당 400ms — 드문 갱신
            ov.set_progress(i, need, f"{i}/{need}")
            qapp.processEvents()
        # 마지막 보고 직후에는 아직 차오르는 중이어야 한다(= 완료도 스냅이 아니다).
        assert ov._wafer.value() < need, "완료를 한 번에 채웠다(점진 요청 위반)"
        assert ov._val_anim.state() != ov._val_anim.State.Stopped
        # 퇴장 페이드가 **시작되는 순간**의 표시값을 붙잡는다 — 그때 이미 100% 여야
        # '차오르는 마지막 구간을 퇴장이 잘라먹지 않았다' 가 성립한다(샘플링 타이밍에
        # 기대지 않는다).
        at_fade = []
        real_begin = ov._begin_fade_out
        monkeypatch.setattr(ov, "_begin_fade_out",
                            lambda tok: (at_fade.append(ov._wafer.value()),
                                         real_begin(tok)))
        ov.hide_overlay()                       # 퇴장 요청 — 추격이 끝날 때까지 기다린다
        assert ov.is_retiring()
        prev = ov._wafer.value()
        for _ in range(300):
            _spin(qapp, 10)
            cur = ov._wafer.value()
            assert cur >= prev, f"표시값이 뒤로 갔다: {prev} → {cur}"
            prev = cur
            if not ov.isVisible():
                break
        assert not ov.isVisible(), "퇴장이 끝나지 않았다"
        assert at_fade == [need], f"페이드 시작 시점의 표시값 {at_fade} ≠ {need}"
        assert ov._wafer.value() == need
    finally:
        host.hide()
        qapp.processEvents()
        host.deleteLater()
        qapp.processEvents()


def test_retarget_continues_from_displayed_value_and_lands(qapp, monkeypatch):
    """★ 돌고 있는 추격 위로 새 값이 와도 **지금 보이는 값**에서 이어 간다 — 뒤로 가지도,
    처음으로 밀리지도 않는다.  예전 '재시작 금지' 는 고정 240ms 재시작이 남은 거리의
    일정 비율만 움직이는 지수 추격이라 영원히 못 따라가던 것을 막던 규칙이었다.  지금은
    폭에 비례한 등속이라 재시작해도 속도가 유지되고, 상한 `VAL_TWEEN_MAX_MS` 안에 닿는다."""
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 100, "시작")
        ov.set_progress(40, 100, "도약")
        _spin(qapp, 150)                        # 추격 중간 — 0 < 표시값 < 40
        mid = ov._wafer.value()
        assert 0 < mid < 40, f"추격이 안 걸렸거나 이미 끝났다(표시값 {mid})"
        ov.set_progress(60, 100, "다음")          # 돌고 있는 추격 위로 새 목표
        qapp.processEvents()
        assert mid <= ov._wafer.value() < 60, "재시작이 보이는 값을 버렸다"
        _spin(qapp, _settle_ms(ov))
        assert ov._wafer.value() == 60, "상한 안에 목표에 닿지 않았다"
        assert ov._val_anim.state() == ov._val_anim.State.Stopped
    finally:
        host.hide()
        qapp.processEvents()
        host.deleteLater()
        qapp.processEvents()


def test_irregular_updates_stay_monotonic_and_finish(qapp, monkeypatch):
    """불규칙한 갱신(빠름·빠름·느림 혼합)에서도 표시값은 **단조 증가**하고 끝은 정확하다.

    실제 재계산 루프는 캐시 hit(즉시)과 miss(수십~수백 ms)가 섞여 간격이 튄다."""
    from PyQt6.QtCore import QElapsedTimer

    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        need = 30
        ov.show_overlay("점수 계산 중")
        ov.set_progress(0, need, "점수 계산 중")
        prev = 0
        for i in range(1, need + 1):
            wait = 5 if i % 3 else 300          # 두 번 빠르고 한 번 느리게
            t = QElapsedTimer()
            t.start()
            while t.elapsed() < wait:
                qapp.processEvents()
            ov.set_progress(i, need, f"{i}/{need}")
            qapp.processEvents()
            cur = ov._wafer.value()
            assert cur >= prev, f"표시값이 뒤로 갔다: {prev} → {cur}"
            assert cur <= i, f"표시값이 실제 진행({i})을 앞질렀다: {cur}"
            prev = cur
        _spin(qapp, _settle_ms(ov))
        assert ov._wafer.value() == need
    finally:
        host.hide()
        qapp.processEvents()
        host.deleteLater()
        qapp.processEvents()


def test_sparse_updates_still_tween(qapp, monkeypatch):
    """반대쪽도 지킨다 — 갱신이 **드물면** 부드럽게 채운다(스냅으로 퇴화 금지).

    tween 자체를 지워 버리면 단계 전환처럼 큰 도약이 딱딱하게 튄다.  고칠 것은
    '언제 tween 하는가'이지 tween 의 존재가 아니다."""
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 100, "시작")
        from PyQt6.QtCore import QElapsedTimer
        t = QElapsedTimer()
        t.start()
        while t.elapsed() < 600:                # tween 지속시간보다 충분히 김
            qapp.processEvents()
        ov.set_progress(80, 100, "도약")
        qapp.processEvents()
        # 방금 걸었으므로 아직 80 에 닿지 않았어야 한다(= tween 이 걸렸다).
        assert ov._wafer.value() < 80, "드문 갱신인데 tween 없이 스냅했다"
        assert ov._val_anim.state() != ov._val_anim.State.Stopped
    finally:
        host.deleteLater()


def test_percent_and_count_follow_the_displayed_value(qapp, monkeypatch):
    """큰 도약이 와도 %·개수는 **표시값**과 함께 점진적으로 오른다(사용자 요청) —
    맵은 천천히 차는데 숫자만 먼저 80% 를 찍으면 둘이 서로 거짓말한다."""
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 100, "시작")
        ov.set_progress(80, 100, "도약")
        qapp.processEvents()
        assert ov._pct_label.text() != "80%", "숫자가 채움보다 먼저 뛰었다"
        _spin(qapp, 150)
        mid = ov._wafer.value()
        assert 0 < mid < 80
        assert ov._pct_label.text() == f"{mid}%"
        assert ov._count_label.text() == i18n.KO.LOADING_COUNT_FMT.format(
            done=mid, total=100)
        _spin(qapp, _settle_ms(ov))
        assert ov._pct_label.text() == "80%"
        assert ov._count_label.text() == i18n.KO.LOADING_COUNT_FMT.format(
            done=80, total=100)
    finally:
        host.deleteLater()


def test_die_lights_up_with_a_200ms_fade(qapp, monkeypatch):
    """다이 하나가 켜질 때 회색 → 파란색 200ms 색 보간(사용자 요청).  되감기면 취소."""
    from aoi_verification.app.ui import motion
    from aoi_verification.app.ui.widgets.loading_overlay import _WaferMap
    monkeypatch.setattr(motion, "enabled", lambda: True)
    wm = _WaferMap()
    assert wm.FADE_MS == 200
    wm.set_busy(True)
    wm.set_busy(False)                          # 결정형 — 물결은 계속 돈다
    wm.setRange(0, 100)
    wm.setValue(50)
    lit = wm.lit_count()
    assert 0 < lit < wm.die_count()
    now = wm._clock.elapsed()
    # 방금 켜진 다이는 전부 페이드 시작점(0)에 있고, 100ms 뒤 절반, 200ms 뒤 불투명.
    assert all(wm._fade_frac(r, now) < 0.2 for r in range(lit))
    assert abs(wm._fade_frac(0, now + 100) - 0.5) < 0.2
    assert wm._fade_frac(0, now + 250) == 1.0
    assert 0 not in wm._lit_at, "끝난 페이드를 잊지 않았다(딕셔너리가 자란다)"
    # 안 켜진 다이는 페이드 대상이 아니다 → 1.0(불투명 판정은 paint 가 rank<lit 로 가른다)
    assert wm._fade_frac(lit + 1, now) == 1.0
    # 되감기: 값이 줄면 그 위 다이의 페이드는 취소된다.
    wm.setValue(100)
    wm.setValue(10)
    assert all(r < wm.lit_count() for r in wm._lit_at)
    wm.deleteLater()


def test_finish_tick_waits_for_the_fill_to_land(qapp, monkeypatch):
    """마침 틱은 채움이 100% 에 닿은 **뒤** 완료색을 켠다 — 차오르는 다이 위를 초록으로
    덮어 버리면 점진 채움이 보이지 않는다."""
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 100, "시작")
        ov.set_progress(100, 100, "끝")
        qapp.processEvents()
        assert ov._wafer.value() < 100
        fired = []
        ov.finish_tick(then=lambda: fired.append(ov._wafer.value()))
        assert not ov._wafer.is_done(), "채움이 끝나기 전에 완료색을 켰다"
        _spin(qapp, _settle_ms(ov))
        assert ov._wafer.value() == 100
        assert ov._wafer.is_done(), "채움이 끝났는데 완료색이 안 켜졌다"
        _spin(qapp, motion.DUR_FINISH_TICK + 100)
        assert fired == [100], "then 이 200ms 틱 뒤 한 번 불려야 한다"
    finally:
        host.deleteLater()
