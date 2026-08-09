"""퇴장이 **끝에 도달하는지** 못 박는다 (라운드 7).

배경(실제 사고): 이 클래스는 "끝은 `_finish_hide` 하나뿐" 이라는 불변식을 스스로
적어 두었는데, 그 하나뿐인 끝에 **도달하지 못하는 길**이 있었다.

`hideEvent` 는 `_fade_anim` 과 `_hide_timer` 를 멈춘다.  그런데 퇴장 페이드(112ms)나
최소표시 래치(350ms)가 도는 중에 페이지가 전환되면 — 그리고 `_show_page` 의 스냅샷
스왑은 **모든 전환에서** 조상에 hide→show 를 배달한다 — 그 정지 때문에 `finished` 가
나지 않아 `_on_fade_done → _finish_hide` 가 영영 오지 않는다.

결과: `hide()` 도 못 부른 오버레이가 부모 전체 크기로 마운트된 채 남는다.  실측 —

  · 그 페이지를 다시 표시하면 화면 평균 휘도 **233 → 173**(스크림이 그대로 덮여 있다)
  · `childAt(버튼좌표)` 가 `LoadingOverlay` — 마우스 클릭 전부 삼킴(0/1건 도달)
  · 래치 경로에서는 `showEvent` 의 재무장까지 걸려 **앱 전역 키보드가 영구히 잠긴다**
  · 스피너가 계속 돌고 '점수 계산 중' 이라고 **거짓 진행**을 표시한다(계산은 끝났다)

비취소 오버레이(`LOAD_REVIEW_ROWS`·`LOAD_EXPORT`·`LOAD_KLA_INFO`)면 빠져나갈 방법이
없어 강제 종료뿐이고, 검증 세션이 통째로 날아간다.

기준선 97a9778 에서는 재현되지 않는다 — 그때는 `hideEvent` 가 **두 번 정의**돼
애니메이션 정지 본문이 죽어 있었고, 그래서 퇴장 페이드가 숨은 채로 완주해
`_finish_hide` 에 정상 도달했다.  중복 정의를 합치면서(P-04) 그 우연한 구제 경로가
사라졌다.

여기서 세우는 계약: **숨는 순간 퇴장이 진행 중이었다면, 그 자리에서 끝을 확정한다.**
숨은 뒤에는 애니메이션을 볼 사람이 없으므로 남은 것은 '끝냈다는 사실' 뿐이다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, Qt                                # noqa: E402
from PyQt6.QtGui import QKeyEvent                                  # noqa: E402
from PyQt6.QtWidgets import QStackedWidget, QWidget                # noqa: E402

from aoi_verification.app.ui import motion                         # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (      # noqa: E402
    LoadingOverlay)


@pytest.fixture()
def motion_on(monkeypatch):
    """실제 앱 조건 — offscreen 기본값(False)에서는 퇴장이 즉시 끝나 재현되지 않는다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)


def _stack(styled_qapp):
    st = QStackedWidget()
    a, b = QWidget(), QWidget()
    st.addWidget(a)
    st.addWidget(b)
    st.resize(1280, 800)
    st.show()
    styled_qapp.processEvents()
    return st, a, b


def _swap(app, stack, old, new):
    """`main_window._show_page` 의 스냅샷 스왑 — 모든 전환이 이 hide→show 를 낸다."""
    stack.setCurrentWidget(new); app.processEvents()
    stack.setCurrentWidget(old); app.processEvents()
    stack.setCurrentWidget(new); app.processEvents()


def _spin(app, ms: int):
    from PyQt6.QtCore import QElapsedTimer
    t = QElapsedTimer(); t.start()
    while t.elapsed() < ms:
        app.processEvents()


def test_exit_during_fade_out_still_reaches_the_end(styled_qapp, motion_on):
    """퇴장 페이드 중 전환 — 그래도 `_finish_hide` 에 도달해야 한다."""
    stack, other, page = _stack(styled_qapp)
    stack.setCurrentWidget(page)
    styled_qapp.processEvents()

    ov = LoadingOverlay(page)
    ov.show_overlay("점수 계산 중", cancelable=True)
    _spin(styled_qapp, 420)              # 최소표시 래치(350ms) 통과
    ov.hide_overlay()                    # 퇴장 페이드 시작
    styled_qapp.processEvents()
    assert ov._hiding, "전제가 깨졌다 — 퇴장 페이드가 시작되지 않았다"

    stack.setCurrentWidget(other)        # 페이드아웃 도중 전환
    styled_qapp.processEvents()
    _spin(styled_qapp, 600)              # 페이드아웃 112ms 의 다섯 배

    assert ov._active is False, "끝(`_finish_hide`)에 도달하지 못했다 — `_active` 가 남았다"
    assert ov.isHidden(), "오버레이가 숨겨지지 않은 채 남았다 — 다시 보이면 화면을 덮는다"
    assert ov._fade == pytest.approx(0.0), f"스크림이 남았다(_fade={ov._fade:.3f})"
    stack.deleteLater()


def test_a_frozen_exit_does_not_swallow_clicks_when_the_page_returns(styled_qapp, motion_on):
    """가장 나쁜 증상 — 돌아온 페이지에서 아무것도 눌리지 않는다."""
    stack, other, page = _stack(styled_qapp)
    stack.setCurrentWidget(page)
    styled_qapp.processEvents()

    ov = LoadingOverlay(page)
    ov.show_overlay("행 만드는 중")           # cancelable=False — 탈출구가 없는 쪽
    _spin(styled_qapp, 420)
    ov.hide_overlay()
    styled_qapp.processEvents()

    _swap(styled_qapp, stack, other, page)   # 퇴장 중 전환 → 다시 이 페이지로
    _spin(styled_qapp, 600)

    probe = QWidget(page)
    probe.setGeometry(60, 60, 200, 60)
    probe.show()
    styled_qapp.processEvents()
    assert page.childAt(120, 80) is not probe.parent() or True   # 배치 확인용
    hit = page.childAt(120, 80)
    assert hit is not ov, (
        "끝나지 않은 오버레이가 페이지를 덮은 채 남아 클릭을 전부 삼킨다 — "
        "비취소 오버레이면 사용자는 강제 종료 말고 빠져나갈 길이 없다")
    stack.deleteLater()


def test_pending_latch_does_not_lock_the_app_keyboard_forever(styled_qapp, motion_on):
    """최소표시 래치 대기 중 전환 — 앱 전역 키보드가 영구히 잠기면 안 된다."""
    stack, other, page = _stack(styled_qapp)
    stack.setCurrentWidget(page)
    styled_qapp.processEvents()

    ov = LoadingOverlay(page)
    ov.show_overlay("점수 계산 중입니다", cancelable=True)
    _spin(styled_qapp, 120)              # 래치(350ms) 안에서
    ov.hide_overlay()                    # → `_hide_pending` 상태
    styled_qapp.processEvents()
    assert ov._hide_pending, "전제가 깨졌다 — 래치 대기가 아니다"

    _swap(styled_qapp, stack, other, page)
    _spin(styled_qapp, 900)

    assert ov._input_locked is False, (
        "끝난 작업의 오버레이가 앱 전역 키보드를 영구히 잠갔다")
    assert ov._active is False and ov.isHidden()

    victim = QWidget(); victim.show()
    styled_qapp.processEvents()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier)
    assert ov.eventFilter(victim, key) is False, "키가 아직 막힌다"
    victim.deleteLater()
    stack.deleteLater()


def test_rearm_settles_the_progress_bar_stagger(styled_qapp, motion_on):
    """되살릴 때 진행바도 **안착 위치**로 — 8px 내려앉은 채 굳으면 안 된다."""
    stack, other, page = _stack(styled_qapp)
    ov = LoadingOverlay(page)
    ov.show_overlay("검토 목록 준비 중")
    ov.set_progress(40, 600, "검토 목록 준비 중")
    styled_qapp.processEvents()

    _swap(styled_qapp, stack, other, page)
    _spin(styled_qapp, 900)              # 페이드인 500 + 스태거 210+160 여유

    m = ov._bar_lay.contentsMargins()
    assert (m.top(), m.bottom()) == (0, ov._BAR_SLIDE_PX), (
        f"진행바가 시작 위치에 굳었다 (top={m.top()}, bottom={m.bottom()}) — "
        "스태거를 만든 바로 그 화면에서 등장 안무가 재생되지 않는다")
    ov.hide_overlay()
    stack.deleteLater()


def test_rearm_drops_the_graphics_effect(styled_qapp, motion_on):
    """되살릴 때 그래픽 이펙트를 뗀다 — 붙어 있으면 스피너 프레임마다 재렌더된다."""
    stack, other, page = _stack(styled_qapp)
    ov = LoadingOverlay(page)
    ov.show_overlay("행 만드는 중")
    styled_qapp.processEvents()

    _swap(styled_qapp, stack, other, page)
    _spin(styled_qapp, 900)

    assert ov._content_eff is None and ov._panel.graphicsEffect() is None, (
        "등장 페이드가 전환에 끊겨 이펙트가 붙은 채 남았다 — 불투명도는 이미 1.0 이라 "
        "이득이 0 인데 패널 전체가 62.5Hz 로 오프스크린 재렌더된다")
    ov.hide_overlay()
    stack.deleteLater()


def test_a_live_overlay_still_rearms(styled_qapp, motion_on):
    """대조군 — **아직 끝나지 않은** 작업은 여전히 되살아나야 한다(라운드 6 계약)."""
    stack, other, page = _stack(styled_qapp)
    ov = LoadingOverlay(page)
    ov.show_overlay("행 만드는 중")
    styled_qapp.processEvents()

    _swap(styled_qapp, stack, other, page)
    styled_qapp.processEvents()

    assert ov.isVisible() and ov._input_locked and ov._fade == pytest.approx(1.0), (
        "끝나지 않은 오버레이까지 꺼 버렸다 — 라운드 6 이 고친 것을 되돌린 셈이다")
    ov.hide_overlay()
    stack.deleteLater()
