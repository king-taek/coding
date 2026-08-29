"""로딩 오버레이 — 반투명 스크림 + 패널이 아래에서 중앙으로 안착(사용자 요청).

CLAUDE.md 로딩 계약(set_progress 의미·결정형/busy)은 그대로 유지되는지도 함께 지킨다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

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
        assert not ov._busy.isHidden() and ov._progress.isHidden()
        ov.set_progress(5, 10, "처리")              # 결정형
        assert not ov._progress.isHidden() and ov._progress.maximum() == 10
        assert ov._progress.value() == 5
        ov.set_progress(9, 10)                      # 증가
        assert ov._progress.value() == 9
        ov.set_progress(2, 40)                      # 범위 변경 → 스냅
        assert ov._progress.maximum() == 40 and ov._progress.value() == 2
    finally:
        host.deleteLater()


def test_show_overlay_starts_busy_not_a_frozen_zero_bar(qapp):
    """★ ``show_overlay`` 만 부르는 호출부에서 **바가 0 에 얼어 있으면 안 된다.**

    이전에는 `_progress`(range 0..100, value 0) 가 보이는 채로 `_busy` 가 숨어 있었다.
    그래서 총량을 모르는 작업(OpenVINO 설치 · KLA 파일명 읽기 · 선계산 대기)에서는
    바가 영원히 0 이었다 — 사용자가 보고한 "바가 채워지지 않는" 버그이고,
    CLAUDE.md 로딩 계약 위반이다.
    """
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("설치 중")             # set_progress 를 부르지 않는 호출부
        assert not ov._busy.isHidden(), "busy 표시가 없다 — 바가 0 에 얼어 있다"
        assert ov._progress.isHidden(), "결정형 바가 0 으로 보이고 있다"
        assert ov._count_label.text() == "", "총량을 모르는데 숫자를 적었다"
        assert ov._pct_label.text() == "", "총량을 모르는데 퍼센트를 적었다"

        # 총량이 알려지면 결정형으로 **승격**된다.
        ov.set_progress(3, 10, "처리")
        assert not ov._progress.isHidden() and ov._busy.isHidden()
        assert ov._progress.maximum() == 10
        assert ov._count_label.text() == i18n.KO.LOADING_COUNT_FMT.format(
            done=3, total=10)
        assert ov._pct_label.text() == "30%"

        # 다시 총량을 잃으면 busy 로 되돌아온다(왕복).
        ov.set_progress(0, 0, "마무리")
        assert not ov._busy.isHidden() and ov._progress.isHidden()

        # 감췄다 다시 띄워도 busy 로 시작한다(이전 결정형 상태가 새지 않게).
        ov.set_progress(7, 10)
        ov._finish_hide()
        ov.show_overlay("다시 시작")
        assert not ov._busy.isHidden() and ov._progress.isHidden()
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
            lag = abs(ov._progress.value() - i) / need
            worst = max(worst, lag)
        assert worst <= 0.10, f"표시값이 실제 진행에서 최대 {worst * 100:.1f}% 벗어났다"
        assert ov._progress.value() == need, \
            f"작업이 끝났는데 바가 {ov._progress.value()}/{need} 에서 멈췄다"
    finally:
        host.deleteLater()


def test_last_update_is_not_left_mid_tween(qapp, monkeypatch):
    """★ **완료는 tween 하지 않는다** — 마지막 증가가 tween 이면 바가 못 채워진다.

    '촘촘한 갱신' 수정이 못 막은 잔여 버그다(사용자: "개선됐지만 여전히 가끔").
    갱신이 드물면(예: 400ms 간격) 증가마다 tween 이 걸리는데, **마지막** 증가의 tween 은
    작업이 끝나 오버레이가 내려가면서 `_finish_hide` 의 `stop()` 에 잘린다.  실측(수정 전,
    5칸 작업):

        루프 종료 직후 bar = 4 / 5   (100% 여야 한다)
        숨긴 뒤        bar = 4 / 5

    완료는 **정보**이고 뒤에 부드러워야 할 것이 없다 → `done >= total` 은 항상 스냅한다.
    그리고 퇴장 경로는 tween 을 멈추기 전에 목표값으로 확정해, 페이드아웃 동안 보이는
    마지막 프레임이 '멈춘 tween 의 중간값'이 되지 않게 한다."""
    from PyQt6.QtCore import QElapsedTimer

    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        need = 5
        ov.show_overlay("점수 계산 중")
        ov.set_progress(0, need, "점수 계산 중")
        for i in range(1, need + 1):
            t = QElapsedTimer()                 # 한 칸당 400ms — tween 보다 드물다
            t.start()
            while t.elapsed() < 400:
                qapp.processEvents()
            ov.set_progress(i, need, f"{i}/{need}")
            qapp.processEvents()
        assert ov._progress.value() == need, (
            f"작업이 끝났는데 바가 {ov._progress.value()}/{need} 에서 멈췄다 "
            "(마지막 증가가 tween 으로 걸렸다)")
        ov.hide_overlay()
        qapp.processEvents()
        assert ov._progress.value() == need, \
            "퇴장 페이드 동안 바가 목표값 아래로 남았다"
    finally:
        host.hide()
        qapp.processEvents()
        host.deleteLater()
        qapp.processEvents()


def test_running_tween_is_never_restarted(qapp, monkeypatch):
    """★ **돌고 있는 tween 을 재시작하지 않는다** — 재시작이 '따라가지 못함'의 기계다.

    간격 측정만으로는 부족하다: 갱신이 불규칙하면(빠름·빠름·느림) 판정이 틀리고, tween 은
    이벤트 루프가 돌 때만 진행하므로 `processEvents()` 로 도는 호출부(실패 사진 재계산)
    에서는 몇 프레임만 돌고 계속 처음으로 밀린다.  '재시작 금지'는 간격과 달리
    **타이밍에 의존하지 않는 불변식**이라 어느 호출부에서도 성립한다."""
    from PyQt6.QtCore import QElapsedTimer

    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 100, "시작")
        t = QElapsedTimer()
        t.start()
        while t.elapsed() < 600:                # 드문 갱신 → tween 이 걸린다
            qapp.processEvents()
        ov.set_progress(40, 100, "도약")
        qapp.processEvents()
        assert ov._val_anim.state() != ov._val_anim.State.Stopped, "tween 이 안 걸렸다"
        started_at = ov._progress.value()
        # tween 이 도는 **중에** 다음 값이 온다 → 재시작이 아니라 즉시 스냅.
        ov.set_progress(60, 100, "다음")
        assert ov._progress.value() == 60, (
            f"돌고 있는 tween 을 재시작했다(값 {ov._progress.value()}, "
            f"tween 시작값 {started_at}) — 이 재시작이 반복되면 목표를 영원히 못 따라간다")
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
            cur = ov._progress.value()
            assert cur >= prev, f"표시값이 뒤로 갔다: {prev} → {cur}"
            assert cur <= i, f"표시값이 실제 진행({i})을 앞질렀다: {cur}"
            prev = cur
        assert ov._progress.value() == need
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
        assert ov._progress.value() < 80, "드문 갱신인데 tween 없이 스냅했다"
        assert ov._val_anim.state() != ov._val_anim.State.Stopped
    finally:
        host.deleteLater()
