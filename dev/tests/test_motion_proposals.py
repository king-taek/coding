"""모션 개선안(01~17안) 적용 — 각 모션이 **켜졌을 때** 실제로 그 일을 하고, 끝나면
흔적(이펙트·임시 위젯·묶인 높이)을 남기지 않는지 확인한다.

헤드리스에서는 ``motion.enabled()`` 가 False 라 모든 모션이 즉시 끝나는 경로를 탄다.
여기서는 ``motion_on`` 으로 켠 채 실제 애니메이션을 돌린다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer, QPoint, QPointF, QRect   # noqa: E402
from PyQt6.QtGui import QColor, QPixmap                          # noqa: E402
from PyQt6.QtWidgets import (QLabel, QStackedWidget, QVBoxLayout,  # noqa: E402
                             QWidget)

from aoi_verification.app.ui import motion                       # noqa: E402


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)


def _spin(qapp, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


def _pix(w: int, h: int, color: str = "#336699") -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor(color))
    return pm


# ── 01안: 나가는 화면이 먼저 비켜 준다 ──────────────────────────────────
def test_page_exit_effect_is_put_on_and_removed(styled_qapp, motion_on):
    stack = QStackedWidget()
    a, b = QLabel("A"), QLabel("B")
    stack.addWidget(a)
    stack.addWidget(b)
    stack.resize(400, 300)
    stack.show()
    committed = []

    def _commit():
        committed.append(True)
        stack.setCurrentWidget(b)

    motion.transition_in(stack, _pix(400, 300), forward=True, on_commit=_commit)
    eff = a.graphicsEffect()
    assert eff is not None and eff.objectName() == "_pageExitEffect"
    _spin(styled_qapp, motion.DUR_PAGE_EXIT + motion.DUR_PAGE_ENTER + 150)
    assert committed == [True]
    assert a.graphicsEffect() is None, "나가는 화면이 투명한 채로 남았다"
    assert stack.findChild(QLabel, "_pageFadeOverlay") is None
    stack.deleteLater()


def test_exit_effect_only_on_pages_without_their_own_effect(styled_qapp, motion_on):
    """위젯당 이펙트는 하나 — 이미 이펙트를 가진 화면은 건드리지 않는다."""
    from PyQt6.QtWidgets import QGraphicsOpacityEffect
    stack = QStackedWidget()
    a = QLabel("A")
    own = QGraphicsOpacityEffect(a)
    a.setGraphicsEffect(own)
    stack.addWidget(a)
    stack.resize(200, 100)
    stack.show()
    motion.transition_in(stack, _pix(200, 100), forward=False)
    assert a.graphicsEffect() is own
    _spin(styled_qapp, motion.DUR_PAGE_EXIT + motion.DUR_PAGE_ENTER + 150)
    stack.deleteLater()


# ── 02·03안: 레일 — 선이 다 찬 뒤 새 단계가 켜지고 고리가 퍼진다 ──────────
def test_rail_lights_new_step_only_after_fill(styled_qapp, motion_on):
    from aoi_verification.app.ui.widgets.journey_rail import JourneyRail
    rail = JourneyRail()
    rail.resize(900, rail.RAIL_H)
    rail.show()
    rail.set_current(1, animate=True)
    assert rail._arriving is True, "선이 차는 동안엔 새 단계를 아직 '예정' 으로"
    _spin(styled_qapp, motion.DUR_RAIL_LEAD + 40)
    assert rail._arriving is False
    assert rail._arrive_anim is not None
    _spin(styled_qapp, motion.DUR_RAIL_ARRIVE + 60)
    assert rail._arrive == pytest.approx(0.0, abs=1e-3)
    # 뒤로 가면 차오를 것도, 퍼질 것도 없다.
    rail.set_current(0, animate=True)
    assert rail._arriving is False and rail._fill == 1.0
    rail.deleteLater()


def test_rail_handoff_is_before_rail_finishes():
    assert 0 < motion.DUR_RAIL_HANDOFF < motion.DUR_RAIL_LEAD


# ── 04·05·11안: 시트가 누른 자리에서 열리고, 닫힐 때 작아지며 빠진다 ───────
@pytest.fixture
def host(styled_qapp):
    from aoi_verification.app.ui.widgets import sheet_host as sh
    parent = QWidget()
    parent.resize(800, 600)
    parent.show()
    for _ in range(8):
        styled_qapp.processEvents()
    h = sh.SheetHost(parent)
    yield h
    parent.close()
    parent.deleteLater()
    styled_qapp.processEvents()


def test_sheet_origin_rect_property_wins(styled_qapp, host, motion_on):
    w = QWidget()
    g = host.parentWidget().mapToGlobal(QPoint(100, 120))
    w.setProperty(host.ORIGIN_RECT_PROP, QRect(g, QRect(0, 0, 80, 60).size()))
    kind, rect = host._origin_of(w)
    assert kind == "rect"
    assert rect.topLeft() == QPoint(100, 120) and rect.size().width() == 80


def test_sheet_grows_from_origin_and_cleans_up(styled_qapp, host, motion_on):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addWidget(QLabel("내용", w))
    w.setMinimumSize(320, 200)
    host._cover_parent()
    host._place(w, False)
    w.setParent(host)
    w.show()
    host._enter(w, ("point", QPoint(40, 40)))
    assert w.graphicsEffect() is not None
    _spin(styled_qapp, max(motion.DUR_SHEET_OPEN, motion.DUR_MORPH) + 150)
    assert w.graphicsEffect() is None, "열린 뒤에도 이펙트가 남았다"
    w.deleteLater()


def test_shrink_out_runs_to_done(styled_qapp, motion_on):
    w = QLabel("고스트")
    w.resize(200, 100)
    w.show()
    done = []
    motion.shrink_out(w, on_done=lambda: done.append(1))
    _spin(styled_qapp, motion.DUR_SHEET_CLOSE + 120)
    assert done == [1]
    w.deleteLater()


# ── 08·09안: 높이를 몰고 끝나면 레이아웃에 돌려준다 ─────────────────────
def test_animate_height_releases_constraints(styled_qapp, motion_on):
    w = QWidget()
    w.setMinimumHeight(10)
    w.show()
    motion.animate_height(w, 200, 60, duration=80)
    assert w.maximumHeight() == 200 and w.minimumHeight() == 200
    _spin(styled_qapp, 180)
    assert w.minimumHeight() == 10, "원래 최소 높이로 돌아오지 않았다"
    assert w.maximumHeight() == 16777215
    w.deleteLater()


# ── 12안: 휠 확대는 커서 아래 사진 점을 고정한다 ─────────────────────────
def test_zoom_keeps_point_under_cursor(styled_qapp):
    from aoi_verification.app.ui.widgets.side_by_side_viewer import _Pane
    pane = _Pane("t")
    pane.resize(500, 400)
    pane.show()
    styled_qapp.processEvents()
    pane.set_pixmap(_pix(1000, 800))
    anchor = QPoint(pane._img.width() // 4, pane._img.height() // 3)
    before = pane._zoom_plan(1.0001, anchor)[3]
    pane.zoom_by(1.5, anchor)
    after = pane._zoom_plan(1.0001, anchor)[3]
    assert pane._zoom == pytest.approx(1.5, rel=1e-3)
    assert after[0] == pytest.approx(before[0], abs=1.0)
    assert after[1] == pytest.approx(before[1], abs=1.0)
    pane.deleteLater()


def test_animated_zoom_lands_on_goal(styled_qapp, motion_on):
    from aoi_verification.app.ui.widgets.side_by_side_viewer import _Pane
    pane = _Pane("t")
    pane.resize(500, 400)
    pane.show()
    styled_qapp.processEvents()
    pane.set_pixmap(_pix(1000, 800))
    anchor = QPoint(100, 100)
    pane._animate_zoom(1.1, anchor)
    pane._animate_zoom(1.1, anchor)          # 연속 — 목표에서 다음 칸
    goal = pane._zoom_goal
    assert goal[0] == pytest.approx(1.21, rel=1e-3)
    _spin(styled_qapp, motion.DUR_ZOOM_STEP + 80)
    assert (pane._zoom, pane._off_x, pane._off_y) == goal
    pane.deleteLater()


# ── 14안: 누름 이펙트는 떼면 걷힌다 ───────────────────────────────────
def test_press_effect_removed_after_release(styled_qapp, motion_on):
    from PyQt6.QtCore import QEvent, QPointF as P, Qt
    from PyQt6.QtGui import QMouseEvent
    from aoi_verification.app.ui.widgets.neon_button import NeonButton
    btn = NeonButton("확인")
    btn.resize(120, 40)
    btn.show()
    pos = P(60, 20)
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(kind, pos, btn.mapToGlobal(pos),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        styled_qapp.sendEvent(btn, ev)
        if kind == QEvent.Type.MouseButtonPress:
            assert isinstance(btn.graphicsEffect(), motion.PressEffect)
    _spin(styled_qapp, motion.DUR_PRESS_IN + motion.DUR_PRESS_OUT + 120)
    assert btn.graphicsEffect() is None, "버튼마다 이펙트가 남으면 그리기가 비싸진다"
    btn.deleteLater()


# ── 06안: 반짝임 테두리는 스스로 사라진다 ────────────────────────────────
def test_flash_outline_deletes_itself(styled_qapp, motion_on):
    target = QWidget()
    target.resize(200, 100)
    target.show()
    motion.flash_outline(target, "#00ff88", duration=80)
    motion.flash_outline(target, "#00ff88", duration=80)   # 연타 — 앞 것은 치운다
    styled_qapp.processEvents()
    assert len(target.findChildren(motion._FlashFrame)) == 1
    _spin(styled_qapp, 200)
    assert target.findChildren(motion._FlashFrame) == []
    target.deleteLater()


def test_select_stamp_paints_border():
    from aoi_verification.app.ui.pages.select_page import SelectPage
    pm = _pix(120, 90, "#000000")
    SelectPage._stamp(pm, True)
    img = pm.toImage()
    assert img.pixelColor(1, 45) != QColor("#000000"), "테두리가 안 찍혔다"


# ── 15안: 스위치 노브가 움직이는 동안 출발·도착을 안다 ────────────────────
def test_switch_knob_records_from_to(styled_qapp, motion_on):
    from aoi_verification.app.ui.widgets.switch_row import ToggleSwitch
    sw = ToggleSwitch(False)
    sw.show()
    sw.set_on(True)
    assert (sw._knob_from, sw._knob_to) == (0.0, 1.0)
    _spin(styled_qapp, motion.DUR_KNOB + 80)
    assert sw._pos == pytest.approx(1.0)
    sw.deleteLater()


# ── 16안: 슬롯 타일 체크 배지는 선택 상태를 따른다 ───────────────────────
def test_slot_tile_badge_follows_check(styled_qapp, motion_on):
    from aoi_verification.app.ui.widgets.slot_select_dialog import _SlotTile
    tile = _SlotTile("S01")
    tile.resize(160, 60)
    tile.show()
    assert tile._badge._t == 0.0
    tile.setChecked(True)
    _spin(styled_qapp, motion.DUR_CHECK_POP + 80)
    assert tile._badge._t == pytest.approx(1.0)
    tile.setChecked(False)
    _spin(styled_qapp, motion.DUR_CHECK_OUT + 80)
    assert tile._badge._t == pytest.approx(0.0)
    tile.deleteLater()


# ── 17안: 토스트 체크 — 자리는 한 번만 비우고, 지우면 숨는다 ──────────────
def test_toast_tick_margin_added_once_and_hidden_after(styled_qapp, motion_on):
    lab = QLabel()
    lab.resize(200, 24)
    lab.show()
    left0 = lab.contentsMargins().left()
    motion.flash_text(lab, "복사됨", hold_ms=40)
    motion.flash_text(lab, "복사됨", hold_ms=40)
    assert lab.contentsMargins().left() == left0 + motion._ToastTick.W + 2
    tick = lab._motionToastTick
    assert tick.isVisible()
    _spin(styled_qapp, 40 + motion.DUR_TOAST_OUT + 150)
    assert lab.text() == ""
    assert not tick.isVisible()
    lab.deleteLater()


# ── 10안: 검토 썸네일 중간 캐시는 UI 스레드 밖에서 만든다 ──────────────────
def test_thumb_loader_delivers_off_thread(styled_qapp, monkeypatch, tmp_path):
    import threading
    from aoi_verification.app.ui.pages import match_review_page as mrp
    from aoi_verification.app.utils import image_io
    seen = {}

    def _fake_mid(path, *a, **k):
        seen["thread"] = threading.current_thread()
        return path

    monkeypatch.setattr(image_io, "get_mid_path", _fake_mid)

    class _Thumb(QWidget):
        def __init__(self):
            super().__init__()
            self._path = str(tmp_path / "a.jpg")
            self.loaded = 0

        def _load(self, *a, **k):
            self.loaded += 1

    calls = []
    monkeypatch.setattr(image_io, "get_mid_path",
                        lambda p, *a, **k: (calls.append(1), _fake_mid(p)))
    loader = mrp._ThumbLoader()
    t1, t2 = _Thumb(), _Thumb()
    loader.request(t1)
    loader.request(t2)                        # 같은 사진 — 파일은 한 번만 만든다
    _spin(styled_qapp, 300)
    assert seen["thread"] is not threading.main_thread()
    assert len(calls) == 1
    assert (t1.loaded, t2.loaded) == (1, 1)
    t1.deleteLater()
    t2.deleteLater()


# ── 공통: 이펙트가 실제로 그림을 바꾼다 ─────────────────────────────────
def test_offset_fade_really_changes_pixels(styled_qapp):
    """★ 위젯 소스의 ``drawSource`` 는 painter 의 불투명도·변환을 무시한다 — 예전 몸통이
    그것을 썼을 때 흐림·밀림·커짐이 **하나도 화면에 나오지 않았다**(실측 녹화)."""
    win = QWidget()
    win.resize(200, 150)
    win.setStyleSheet("background:#dddddd")
    d = QWidget(win)
    d.setStyleSheet("background:#cc3333")
    d.setGeometry(0, 0, 200, 150)
    win.show()
    eff = motion._OffsetFade(d)
    eff.configure(offset_from=(0, 0), offset_to=(0, 0), opacity_from=1.0,
                  opacity_to=1.0, scale_from=0.3, scale_to=0.3,
                  origin=QPointF(0, 0))
    d.setGraphicsEffect(eff)
    eff.progress = 0.5
    styled_qapp.processEvents()
    img = win.grab().toImage()
    assert img.pixelColor(10, 10).name() == "#cc3333"        # 줄어든 자리 안
    assert img.pixelColor(150, 120).name() != "#cc3333", "배율이 그림에 안 들어갔다"
    win.deleteLater()
