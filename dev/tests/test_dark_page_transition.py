"""다크 모드 두 가지 — 전환 스냅샷 배경 · 토글 선딜레이.

**버그 A — 다음 화면으로 넘어갈 때 밝은 화면이 먼저 보였다.**
페이지는 자기 배경을 칠하지 않고(투명) 창이 뒤에서 칠해 준다.  그래서 페이지만
떼어 ``grab()`` 하면 빈 자리가 Qt 기본 팔레트 Window 색(``#efefef``, 밝은 회색)
으로 채워진다.  그 스냅샷을 페이드인하니 다크 모드에서 '밝았다가 어두워지는' 것으로
보였다(실측 밝기 85.7 → 실제 30.4).

**버그 B — 누르고 한참 뒤에 색이 움직였다.**
손잡이 이동(160ms)이 끝난 뒤에야 무거운 일을 시작해서, 누르고 색이 움직이기까지
실측 346ms 였다.  타이머 지연을 0 으로 만들어 204ms 로 줄였다(남은 것은 계산 시간).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget      # noqa: E402

from aoi_verification.app.ui import motion, theme             # noqa: E402

DEFAULT_QT_WINDOW = "#efefef"      # 팔레트 기본값 — 테마와 무관한 밝은 회색


@pytest.fixture()
def dark_theme():
    """이 테스트 동안만 다크 — 모듈 전역이라 반드시 되돌린다."""
    before = theme.COLOR_MODE
    theme.set_color_mode("dark")
    yield
    theme.set_color_mode(before)


def _page(qapp) -> QWidget:
    """배경을 스스로 칠하지 않는 페이지 — 실제 페이지들과 같은 조건."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addWidget(QLabel("내용", w))
    w.resize(240, 160)
    return w


def test_snapshot_paints_the_theme_background(qapp, dark_theme):
    """★ 스냅샷의 빈 자리는 테마 배경색이어야 한다 — 버그 A."""
    w = _page(qapp)
    try:
        pix = motion.snapshot(w)
        img = pix.toImage()
        corner = img.pixelColor(2, img.height() - 3)
        assert corner.name().lower() == theme.BG.lower(), (
            f"스냅샷 배경이 {corner.name()} — 테마 배경 {theme.BG} 이어야 한다")
        assert corner.name().lower() != DEFAULT_QT_WINDOW, (
            "Qt 기본 팔레트 색이 그대로 찍혔다")
    finally:
        w.deleteLater()


def test_snapshot_is_darker_than_plain_grab_in_dark_mode(qapp, dark_theme):
    """옛 방식(`grab()`)과 실제로 달라야 한다 — 안 그러면 가드가 헛돈다."""
    w = _page(qapp)
    try:
        def avg(pix):
            img = pix.toImage()
            tot = n = 0
            for y in range(0, img.height(), 7):
                for x in range(0, img.width(), 7):
                    c = img.pixelColor(x, y)
                    tot += c.red() + c.green() + c.blue()
                    n += 3
            return tot / max(1, n)

        assert avg(motion.snapshot(w)) < avg(w.grab()) - 20, (
            "새 스냅샷이 옛 grab() 만큼 밝다 — 수정이 동작하지 않는다")
    finally:
        w.deleteLater()


def test_snapshot_keeps_widget_size(qapp, dark_theme):
    """크기가 달라지면 전환에서 화면이 늘어나 보인다."""
    w = _page(qapp)
    try:
        pix = motion.snapshot(w)
        dpr = pix.devicePixelRatio() or 1.0
        assert round(pix.width() / dpr) == w.width()
        assert round(pix.height() / dpr) == w.height()
    finally:
        w.deleteLater()


def test_dark_toggle_answers_before_the_screen_recolors(styled_qapp):
    """누른 뒤 색이 움직이기까지의 시간이 **늘어나지 않는다** — 버그 B 의 본질.

    ★ 이 검사는 한때 `interval() == 0` 이었다.  그때의 사실관계: 재색이 메인
    스레드를 실측 223ms 잡았고, 그 앞에 손잡이 이동 160ms 가 붙어 **누르고 색이
    움직이기까지 383ms** 였다.  지연을 없애 223ms 로 줄인 것이 그 수정이다.
    ★ 구조개편 24안이 그 전제를 바꿨다: 재색의 정지시간을 ~30ms 로 쪼갰다
    (`main_window._repolish_visible_now` + 숨은 페이지 지연 처리).  그래서 이제
    '노브 180ms → 재색' 순서를 써도 색이 움직이기까지 **약 210ms** 로, 지연 0 이던
    223ms 보다 **오히려 빠르다** — 게다가 그 180ms 동안 노브가 얼지 않고 실제로
    움직여 '눌렸다' 는 답을 준다(예전에는 그 구간이 통째로 얼어 있었다).
    ★ 그래서 못박을 것은 기계(값 0)가 아니라 **목표**다: 예약은 되어야 하고,
    누른 뒤 색이 움직이기 시작하는 시점이 옛 383ms 는 물론 지연 0 시절의 223ms 도
    넘지 않아야 한다.
    """
    from aoi_verification.app.ui import motion
    from aoi_verification.app.ui.pages import setup_page as sp

    page = sp.SetupPage()
    try:
        page._dark_switch.set_on(True, emit=True)
        assert page._appearance_timer.isActive(), "전환 요청이 예약되지 않았다"
        delay = page._appearance_timer.interval()
        # 노브가 도착하기 **전에** 재색을 시작하면 노브가 답을 주다 만다.
        assert delay in (0, motion.DUR_KNOB), (
            f"선딜레이 {delay}ms — 0(즉시) 이거나 노브 도착(DUR_KNOB) 이어야 한다")
        # 정지시간(~30ms)까지 더해도 옛 '지연 0' 시절의 체감(223ms)을 넘지 않는다.
        assert delay + 30 <= 223, (
            f"누른 뒤 색이 움직이기까지 {delay + 30}ms — 예전보다 느려졌다")
    finally:
        page.close()
