"""시트(앱 내 팝업) 등장 모션 — 아래에서 올라오며 나타난다.

사용자 요청: "일부 슬롯만 고르는 버튼 눌렀을 때 나오는 팝업도 모션 처리."

그 팝업(`SlotSelectDialog`)은 `sheets.run` 을 타므로 **모든 시트가 함께 고쳐진다**.
이전에는 불투명도만 0→1 이었다 — '나타났다'는 사실만 전할 뿐 **어디서 왔는지**가
없어 툭 튀어나온 것처럼 읽혔다.  위치와 불투명도를 같은 시간·같은 곡선으로 함께
움직여야 한 덩어리로 떠오른다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer, QPropertyAnimation      # noqa: E402
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget        # noqa: E402

from aoi_verification.app.ui import motion                      # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sh     # noqa: E402


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)


@pytest.fixture
def host(styled_qapp):
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


def _sheet_widget() -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.addWidget(QLabel("내용", w))
    w.setMinimumSize(320, 200)
    return w


def _spin(qapp, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


def test_enter_animates_both_opacity_and_position(styled_qapp, host,
                                                  motion_on):
    """★ 두 축이 **함께** 움직인다 — 하나만 있으면 툭 튀어나온 것처럼 보인다."""
    w = _sheet_widget()
    host._cover_parent()
    host._place(w, False)
    w.setParent(host)
    w.show()
    end = w.pos()

    host._enter(w)
    try:
        anims = w.findChildren(QPropertyAnimation)
        props = {bytes(a.propertyName()).decode() for a in anims}
        assert props == {"opacity", "pos"}, f"움직이는 축: {props}"
        # 시작 위치는 도착점보다 **아래**다.
        assert w.pos().y() > end.y(), "아래에서 올라오지 않는다"
        assert w.pos().y() - end.y() == sh.SheetHost.ENTER_RISE_PX
        assert w.pos().x() == end.x(), "옆으로도 움직인다 — 시트는 세로로만 뜬다"
    finally:
        _spin(styled_qapp, motion.DUR_SHEET + 150)
        w.deleteLater()


def test_enter_lands_exactly_where_place_put_it(styled_qapp, host, motion_on):
    """끝나면 `_place` 가 정한 자리에 정확히 안착한다(1px 어긋나면 다음 리사이즈에 튄다)."""
    w = _sheet_widget()
    host._cover_parent()
    host._place(w, False)
    w.setParent(host)
    w.show()
    end = w.pos()

    host._enter(w)
    _spin(styled_qapp, motion.DUR_SHEET + 200)
    try:
        assert w.pos() == end
        assert w.graphicsEffect() is None, \
            "불투명도 효과가 남았다 — 이후 렌더가 계속 오프스크린 버퍼를 탄다"
    finally:
        w.deleteLater()


def test_both_axes_share_one_duration_and_curve(styled_qapp, host, motion_on):
    """두 축의 시간·곡선이 다르면 한 덩어리로 안 읽힌다."""
    w = _sheet_widget()
    host._cover_parent()
    host._place(w, False)
    w.setParent(host)
    w.show()
    host._enter(w)
    try:
        anims = w.findChildren(QPropertyAnimation)
        assert {a.duration() for a in anims} == {motion.DUR_SHEET}
        for a in anims:
            assert a.easingCurve() == motion.EASE_PRIMARY
    finally:
        _spin(styled_qapp, motion.DUR_SHEET + 150)
        w.deleteLater()


def test_motion_disabled_places_the_sheet_immediately(styled_qapp, host,
                                                      monkeypatch):
    """모션이 꺼진 경로(헤드리스)에서는 위치를 건드리지 않는다 — 캡처가 흔들리지 않게."""
    monkeypatch.setattr(motion, "enabled", lambda: False)
    w = _sheet_widget()
    host._cover_parent()
    host._place(w, False)
    w.setParent(host)
    w.show()
    end = w.pos()
    host._enter(w)
    try:
        assert w.pos() == end
        assert not w.findChildren(QPropertyAnimation)
    finally:
        w.deleteLater()


def test_slot_select_dialog_goes_through_the_sheet_host():
    """'일부 슬롯만' 팝업이 이 경로를 타는지 — 안 타면 위 모션이 걸리지 않는다."""
    from aoi_verification.app.ui.pages import setup_page

    src = inspect.getsource(setup_page.SetupPage._open_slot_select)
    assert "sheets.run(dlg)" in src
    assert ".exec()" not in src, "OS 창으로 띄우면 시트 모션이 걸리지 않는다"


def test_run_calls_enter():
    """`run` 이 등장 모션을 부르는지 — 호출이 빠지면 조용히 옛 동작으로 돌아간다."""
    src = inspect.getsource(sh.SheetHost.run)
    assert "self._enter(root)" in src
