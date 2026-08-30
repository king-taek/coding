"""구조 개편 — 여정 레일 · 헤더 진행 · 로딩 작업 큐 · 다크 팔레트 ①.

디자인 결정(2026-08-30)의 회귀 가드다.  네 가지가 각각 실제 사고/지적에서 나왔다:

1안-A **여정 레일** — 화면마다 뜻이 다르던 [← 설정으로] 를 없애고, 창 상단의 상시
  진행 지도가 복귀를 통일해서 맡는다.  '모르고 되돌아가 결정을 폐기' 가 이 앱의
  가장 비싼 실수라, 레일이 눌리는 곳은 **실제로 복귀 경로가 있는 단계뿐**이어야
  하고(죽은 클릭 금지) 폐기 확인은 각 화면에 남아야 한다(규칙의 단일 출처).
2안-B **헤더 흡수** — 진행 수치가 액션 버튼 뒤 오른쪽 끝의 작은 보조 텍스트였다.
  표제 바로 옆으로 올려 다섯 화면이 같은 자리를 쓴다.  하단 상태바는 현행 유지.
11안-B **작업 큐** — 차단은 유지하되, 단계가 넘어가며 진행바가 0 으로 스냅해도
  '몇 개 남았나' 가 사라지지 않게 지나온 단계의 수치를 얼려 둔다.
24안 **팔레트 ①** — 다크의 면·선을 한 단 밝혀 층이 보이게 한다.  면을 밝히면 그
  위 잉크의 대비 여유가 줄어드므로 잉크도 함께 올라간다(게이트는 a11y 쪽이 잰다).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

from aoi_verification.app import i18n
from aoi_verification.app.ui import theme

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                      # noqa: E402
from PyQt6.QtGui import QMouseEvent                              # noqa: E402
from PyQt6.QtWidgets import QWidget                              # noqa: E402

from aoi_verification.app.ui import main_window as mw            # noqa: E402
from aoi_verification.app.ui.widgets.journey_rail import (       # noqa: E402
    JourneyRail)
from aoi_verification.app.ui.widgets.loading_overlay import (    # noqa: E402
    LoadingOverlay)


# ═══ 24안 — 팔레트 ① '덜 어두운 흑연' ═══════════════════════════════════════
def test_dark_surfaces_are_layered_enough_to_see():
    """면 셋(bg/panel/elev)이 눈으로 구분돼야 카드·행·시트의 층이 읽힌다.

    ★ 팔레트 ① 이 바꾼 것은 면 사이의 **간격**이 아니라 면이 앉은 **높이**다
    (옛/새 모두 단 차이는 평균 9 안팎).  어두울수록 같은 밝기 차이가 덜 보이므로
    (Weber), 바닥을 25 → 38 로 들어 올리는 것만으로 같은 단이 눈에 잡힌다.
    그래서 이 테스트는 '간격이 넓어졌는가' 가 아니라 **바닥이 다시 내려가지
    않는가** 를 지킨다 — 그게 되돌리면 안 되는 결정이다."""
    dark = theme.PALETTES["dark"]

    def mean(hexv: str) -> float:
        h = hexv.lstrip("#")
        return sum(int(h[i:i + 2], 16) for i in (0, 2, 4)) / 3

    bg, panel, elev = mean(dark["bg"]), mean(dark["panel"]), mean(dark["elev"])
    assert bg < panel < elev, "다크의 면 위계가 무너졌다"
    assert bg >= 34, f"다크 바탕이 다시 내려앉았다 (평균 {bg:.1f} — 팔레트 ① 은 38)"
    assert panel - bg >= 8, f"panel 이 bg 위로 안 뜬다 (차이 {panel - bg:.1f})"
    assert elev - panel >= 8, f"elev 가 panel 위로 안 뜬다 (차이 {elev - panel:.1f})"
    # 그래도 '어두운 화면' 이어야 한다 — a11y 게이트(mean<90)보다 보수적으로 둔다.
    assert bg < 60, "다크 바탕이 더 이상 어둡지 않다"


def test_apply_to_app_caches_the_rendered_qss(monkeypatch):
    """색 모드를 오갈 때 style.qss 를 매번 디스크에서 읽고 렌더하지 않는다.

    1,200 줄 QSS 의 '읽기 + Template.substitute' 만으로 실측 55~99 ms 가 메인
    스레드에서 사라졌다 — 다크 전환이 버벅이던 몫이다(24안 '버벅임 제거')."""
    calls: list[int] = []
    real = theme.render_qss
    monkeypatch.setattr(theme, "render_qss",
                        lambda text: (calls.append(1), real(text))[1])

    class _App:
        def setStyleSheet(self, _text):     # noqa: N802
            pass

    app = _App()
    start = theme.COLOR_MODE
    try:
        for mode in ("light", "dark", "light", "dark", "light"):
            theme.set_color_mode(mode)
            theme.apply_to_app(app)
    finally:
        theme.set_color_mode(start)
    assert len(calls) <= 2, (
        f"모드 2개에 렌더 {len(calls)} 회 — 캐시가 동작하지 않는다")


# ═══ 11안-B — 로딩 패널의 작업 큐 ════════════════════════════════════════════
@pytest.fixture
def overlay(styled_qapp):
    host = QWidget()
    host.resize(900, 600)
    ov = LoadingOverlay(host)
    yield ov
    host.deleteLater()
    styled_qapp.processEvents()


def test_work_queue_freezes_the_finished_step_counts(overlay):
    """단계가 넘어가도 지나온 단계의 수치가 남는다.

    ★ 이게 11안-B 의 전부다.  차단 오버레이는 화면을 가리는 대가로 '전체 중
    어디쯤 · 무엇이 끝났나' 를 돌려줘야 한다 — 진행바 하나는 단계가 바뀌며 0 으로
    스냅해 '다 됐다가 다시 0' 으로 읽혔다."""
    steps = i18n.KO.LOAD_JOURNEY_STEPS
    overlay.show_overlay(i18n.KO.LOAD_SCAN, step=(1, 3), steps=steps)
    overlay.set_progress(50, 50, i18n.KO.LOAD_SCAN)
    overlay.set_stage((2, 3), steps)
    overlay.set_progress(298, 480, i18n.KO.LOAD_THUMBNAIL)

    q = overlay._steps
    assert q._index == 1
    assert q._counts[0] == (50, 50), "끝난 단계의 수치가 사라졌다"
    assert q._counts[1] == (298, 480)
    assert 2 not in q._counts, "아직 시작하지 않은 단계에 수치가 생겼다"
    # 세로 목록이라 높이는 줄 수에 비례한다(가로 점 행이면 고정 1줄이었다).
    assert q.height() == q.ROW_H * len(steps)


def test_work_queue_owns_the_numbers_while_it_is_shown(overlay):
    """수치의 단일 출처 — 큐가 떠 있으면 바 아래 모노 라벨은 숨는다.

    둘 다 켜 두면 같은 숫자가 한 패널에 두 번 적힌다(ko.py 의 단일 출처 규칙).
    텍스트는 계속 채워 둔다 — 큐가 없는 호출부(매칭 화면)는 그대로 쓴다."""
    steps = i18n.KO.LOAD_JOURNEY_STEPS
    overlay.show_overlay(i18n.KO.LOAD_SCAN, step=(1, 3), steps=steps)
    overlay.set_progress(3, 10, i18n.KO.LOAD_SCAN)
    assert not overlay._steps.isHidden()
    assert overlay._count_label.isHidden()
    assert overlay._count_label.text() != "", "텍스트까지 비우면 안 된다"

    # 여정을 주지 않은 호출부(매칭 화면)는 예전 그대로 — 라벨이 보인다.
    overlay.show_overlay(i18n.KO.LOAD_SCORING)
    overlay.set_progress(3, 10, i18n.KO.LOAD_SCORING)
    assert overlay._steps.isHidden()
    assert not overlay._count_label.isHidden()


# ═══ 1안-A — 여정 레일 ═══════════════════════════════════════════════════════
@pytest.fixture
def window(styled_qapp, monkeypatch, isolated_cache):
    monkeypatch.setattr(mw.MainWindow, "_check_for_update_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_maybe_offer_openvino", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_warmup_accel_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    theme.set_color_mode("light")
    w = mw.MainWindow()
    w._build_remaining_pages()
    yield w
    w.close()
    w.deleteLater()
    styled_qapp.processEvents()


def test_rail_tracks_every_page_transition(window):
    """`_show_page` 는 모든 전환의 단일 통로다 — 레일은 거기서만 갱신된다."""
    pages = (window._setup_page, window._select_page, window._match_page,
             window._match_review_page, window._result_page)
    for expected, page in enumerate(pages):
        window._show_page(page, animate=False)
        assert window._rail._index == expected, f"{page} 에서 레일이 어긋났다"


def test_rail_only_offers_routes_that_actually_exist(window):
    """눌리는 단계 = 창이 실제로 가진 복귀 경로.

    ★ '완료했으니 전부 갈 수 있다' 로 두면 죽은 클릭이 생기고, 반대로 막으면
    레일이 거짓말을 한다.  설정 화면에서는 갈 곳이 없다(시작 지점)."""
    window._show_page(window._setup_page, animate=False)
    assert window._rail._navigable == frozenset()
    for page, expect in ((window._select_page, {0}),
                         (window._match_page, {0}),
                         (window._match_review_page, {0}),
                         (window._result_page, {3})):
        window._show_page(page, animate=False)
        assert set(window._rail._navigable) == expect, f"{page} 의 복귀 경로"


def test_rail_click_reuses_the_page_own_discard_confirmation(window,
                                                             monkeypatch):
    """레일은 '어디로' 만 말하고, **무엇이 사라지는지**는 화면이 묻는다.

    폐기 규칙이 두 곳에 생기면 그중 하나가 반드시 낡는다 — 그래서 레일 클릭은
    각 화면이 이미 가진 복귀 진입점을 그대로 부른다."""
    seen: list[str] = []
    monkeypatch.setattr(type(window._select_page), "request_back_to_setup",
                        lambda self: seen.append("select"))
    monkeypatch.setattr(type(window._match_page), "request_back_to_setup",
                        lambda self: seen.append("match"))
    window._show_page(window._select_page, animate=False)
    window._rail.step_clicked.emit(0)
    window._show_page(window._match_page, animate=False)
    window._rail.step_clicked.emit(0)
    assert seen == ["select", "match"]


def test_review_to_setup_asks_before_discarding_everything(window, monkeypatch):
    """검토 화면에는 복귀 경로가 없었다 — 레일이 새로 여는 유일한 길이라 여기서 묻는다.

    ★ 기본 버튼은 **아니오**여야 한다.  이 길은 매칭·검토 결과를 통째로 버리고
    처음으로 돌아가는 파괴 흐름이고, 실수로 엔터를 눌러 수 분짜리 작업이 사라지는
    것이 정확히 1안-A 가 막으려는 사고다."""
    asked: list[tuple] = []
    reset: list[int] = []
    from PyQt6.QtWidgets import QMessageBox

    def fake_ask(parent, title, body, buttons, default):
        asked.append((title, default))
        return QMessageBox.StandardButton.No           # 사용자가 취소

    monkeypatch.setattr(mw.sheets, "ask", fake_ask)
    monkeypatch.setattr(type(window), "_new_session",
                        lambda self: reset.append(1))
    window._show_page(window._match_review_page, animate=False)
    window._rail.step_clicked.emit(0)
    assert asked and asked[0][0] == i18n.KO.JOURNEY_BACK_TO_SETUP_TITLE
    assert asked[0][1] == QMessageBox.StandardButton.No, "기본이 '예' 로 되어 있다"
    assert reset == [], "'아니오' 인데 세션을 지웠다"

    monkeypatch.setattr(mw.sheets, "ask",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._rail.step_clicked.emit(0)
    assert reset == [1], "'예' 인데 되돌아가지 않았다"


def test_rail_click_area_covers_the_label_not_just_the_dot(styled_qapp):
    """작은 점만 노리게 하지 않는다 — 표식+이름 전체가 클릭 영역이다."""
    rail = JourneyRail()
    rail.resize(1200, JourneyRail.RAIL_H)
    rail.set_current(3)
    rail.set_navigable({0})
    rail.grab()                                  # paint 가 히트 영역을 만든다
    assert rail._hit, "히트 영역이 비어 있다"
    first = rail._hit[0]
    assert first.width() > JourneyRail.MARK_D + 10, "이름이 클릭 영역 밖이다"
    assert first.height() >= 26, "WCAG 2.5.8 하한(26px)보다 얇다"
    clicked: list[int] = []
    rail.step_clicked.connect(clicked.append)
    pos = first.center()
    rail.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress, pos.toPointF(),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    assert clicked == [0]
    # 눌 수 없는 단계는 신호를 내지 않는다.
    clicked.clear()
    rail.mousePressEvent(QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        rail._hit[4].center().toPointF(),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))
    assert clicked == []
    rail.deleteLater()


def test_rail_criteria_comes_from_the_single_judgement_source(window):
    """판정 기준 문구는 설정 화면의 `judgement_text()` 하나에서 나온다."""
    src = inspect.getsource(mw.MainWindow._refresh_rail_criteria)
    assert "judgement_text" in src
    assert "JUDGE_NAME" not in src, "레일이 문구를 따로 조립하고 있다"
    # 시작 전에는 기준이 확정되지 않았다 — 빈 칸으로 둔다(지어내지 않는다).
    window._input = None
    window._refresh_rail_criteria()
    assert window._rail._criteria == ""


# ═══ 2안-B — 진행 정보를 화면 헤더가 흡수 ════════════════════════════════════
@pytest.mark.parametrize("attr", ["_select_page", "_match_page"])
def test_progress_sits_next_to_the_title_not_after_the_buttons(window, attr):
    """진행 라벨은 표제 옆(= 늘어나는 여백 **앞**)에 있다.

    예전엔 액션 버튼들 뒤, 줄의 오른쪽 끝이었다 — 이 화면의 핵심 상태인데
    위계가 최하위였고 화면마다 자리가 달라 눈의 이동이 학습되지 않았다."""
    page = getattr(window, attr)
    row = page.title.parentWidget().layout()

    def index_of(widget):
        for i in range(row.count()):
            item = row.itemAt(i)
            for j in range(item.layout().count() if item.layout() else 0):
                if item.layout().itemAt(j).widget() is widget:
                    return i
            if item.widget() is widget:
                return i
        return -1

    # title 이 든 QHBoxLayout 을 찾는다(루트 QVBox 의 한 칸).
    top = None
    for i in range(row.count()):
        lay = row.itemAt(i).layout()
        if lay is None:
            continue
        if any(lay.itemAt(j).widget() is page.title for j in range(lay.count())):
            top = lay
            break
    assert top is not None, "표제가 든 상단 줄을 못 찾았다"
    order = [top.itemAt(j) for j in range(top.count())]
    i_title = next(j for j, it in enumerate(order) if it.widget() is page.title)
    i_prog = next(j for j, it in enumerate(order)
                  if it.widget() is page.progress_count)
    i_stretch = next(j for j, it in enumerate(order)
                     if it.widget() is None and it.spacerItem() is not None
                     and it.spacerItem().expandingDirections()
                     & Qt.Orientation.Horizontal)
    assert i_title < i_prog < i_stretch, (
        "진행 수치가 표제 옆이 아니다 (표제 → 진행 → 늘어나는 여백 → 액션)")


