"""되돌리기 키는 앱 어디서나 **Z** 다 (현택님 결정 2026-11-24).

예전엔 화면마다 달랐다 — 선별·Overkill 고르기는 Z, 매칭·GL 은 Ctrl+Z 만, Overkill
튜닝·Zone 은 키가 없었다.  이제 모든 되돌리기가 Z 를 받고(Ctrl+Z 도 그대로), 버튼
라벨도 같은 꼴 "되돌리기 (Z)" 로 적는다.  글자 입력칸에서 'z' 를 치는 것은 되돌리기가
아니다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                         # noqa: E402
from PyQt6.QtGui import QShortcut                                   # noqa: E402
from PyQt6.QtTest import QTest                                      # noqa: E402
from PyQt6.QtWidgets import (QApplication, QDialog, QLineEdit,      # noqa: E402
                             QPushButton, QVBoxLayout, QWidget)

from aoi_verification.app import i18n                               # noqa: E402
from aoi_verification.app.ui.undo_keys import bind_undo             # noqa: E402


def _pump(n: int = 4) -> None:
    for _ in range(n):
        QApplication.processEvents()


def _keys(w: QWidget) -> set[str]:
    return {s.key().toString() for s in w.findChildren(QShortcut)}


def test_every_undo_label_says_z():
    """버튼 라벨이 화면마다 달랐다(되돌리기(Z) · 되돌리기(Ctrl+Z) · 되돌리기)."""
    want = "되돌리기 (Z)"
    assert i18n.KO.BTN_UNDO == want
    for name in ("OVERKILL_UNDO", "OVERKILL_PICK_BTN_UNDO", "GL_UNDO"):
        assert getattr(i18n.KO, name) == want, name
    for gone in ("BTN_MATCH_UNDO", "MATCH_UNDO_TOOLTIP"):
        assert not hasattr(i18n.KO, gone), f"{gone} — Ctrl+Z 만 적힌 옛 라벨이 살아났다"


@pytest.fixture
def page(styled_qapp):
    win = QWidget()
    win.setWindowModality(Qt.WindowModality.ApplicationModal)   # 다른 테스트가 남긴 모달에 안 막히게
    win.resize(400, 200)
    lay = QVBoxLayout(win)
    edit = QLineEdit(win)
    btn = QPushButton("x", win)
    lay.addWidget(edit)
    lay.addWidget(btn)
    fired: list[int] = []
    bind_undo(win, lambda: fired.append(1))
    win.show()
    win.activateWindow()
    _pump()
    yield win, edit, btn, fired
    win.hide()
    win.deleteLater()
    _pump()


def test_z_and_ctrl_z_both_undo(page):
    win, _edit, btn, fired = page
    btn.setFocus()
    _pump()
    QTest.keyClick(btn, Qt.Key.Key_Z)
    QTest.keyClick(btn, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    _pump()
    assert fired == [1, 1]


def test_typing_z_in_a_text_field_is_not_undo(page):
    """값을 치다가 'z' 한 글자에 결정이 되돌려지면 안 된다 — 글자는 칸으로 간다."""
    win, edit, _btn, fired = page
    edit.setFocus()
    _pump()
    QTest.keyClick(edit, Qt.Key.Key_Z)
    _pump()
    assert fired == []
    assert edit.text() == "z"


def test_undo_key_lives_only_while_its_owner_is_visible(page):
    """알림 줄의 [되돌리기] 처럼 '되돌릴 것이 있을 때만' 보이는 위젯에 걸면 그때만 산다."""
    win, _edit, btn, _fired = page
    hits: list[int] = []
    bind_undo(btn, lambda: hits.append(1))
    btn.hide()
    _pump()
    QTest.keyClick(win, Qt.Key.Key_Z)
    _pump()
    assert hits == []


def test_match_page_takes_z_too(styled_qapp):
    from aoi_verification.app.ui.pages.match_page import MatchPage
    p = MatchPage()
    try:
        assert {"Z", "Ctrl+Z"} <= _keys(p)
        assert p.undo_btn.text() == "되돌리기 (Z)"
    finally:
        p.deleteLater()


def test_select_page_keeps_z_and_ctrl_z(styled_qapp):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    p = SelectPage()
    try:
        assert {"Z", "Ctrl+Z"} <= _keys(p)
        assert p.btn_undo.text() == "되돌리기 (Z)"
    finally:
        p.deleteLater()


@pytest.mark.parametrize("modname, cls", [
    ("threshold_preview_dialog", "ThresholdPreviewDialog"),
    ("zone_origin_dialog", "ZoneOriginDialog"),
])
def test_folder_removal_undo_takes_z(styled_qapp, modname, cls):
    """Overkill 튜닝·Zone 화면의 '폴더 뺐다 → [되돌리기]' 도 Z 를 받는다.

    단축키는 [되돌리기] 버튼에 걸려 있어 알림이 떠 있을 때만 산다."""
    import importlib
    mod = importlib.import_module(f"aoi_verification.app.ui.widgets.{modname}")
    d = getattr(mod, cls)()
    try:
        assert {"Z", "Ctrl+Z"} <= _keys(d.undo_btn)
        assert d.undo_btn.text() == "되돌리기 (Z)"
    finally:
        d.deleteLater()
        _pump()


def test_gl_canvas_undoes_on_plain_z(styled_qapp):
    np = pytest.importorskip("numpy")
    from aoi_verification.app.gl import loader
    from aoi_verification.app.gl import roi as R
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    d = GlViewerDialog(None)
    d.resize(1100, 760)
    d.show()
    _pump()
    data = np.zeros((150, 200), dtype=np.uint8)
    d.show_view(loader.ViewLoad(loader.GlImage(data, 8, False, "x.tif", [])))
    _pump()
    try:
        c = d.canvas
        c.rois.add(R.Roi(0, R.RECT, "", "", x=50, y=40, w=30, h=20))
        QTest.keyClick(c, Qt.Key.Key_Z)
        assert c.rois.rois == []
        assert d.undo_btn.toolTip() == "되돌리기 (Z)"
    finally:
        d.close()
        d.deleteLater()
        _pump()


def test_two_live_z_keys_cancel_each_other_so_pick_sheet_clears_the_notice(styled_qapp):
    """Qt 의 성질을 못 박는다 — 뒤 화면과 시트가 **둘 다** Z 를 가지면 어느 쪽도 돌지 않는다
    (모호한 단축키).  그래서 Overkill 튜닝은 [고르기] 시트를 열 때 '폴더 뺐다 [되돌리기]'
    알림을 걷는다(`open_pick_dialog`, 짝 테스트는 test_threshold_preview_dialog)."""
    from aoi_verification.app.ui.widgets import sheet_host
    from aoi_verification.app.ui.deferred import call_later
    win = QWidget()
    win.setWindowModality(Qt.WindowModality.ApplicationModal)
    win.resize(900, 700)
    h = sheet_host.SheetHost(win)
    win._sheets = h
    page_w = QWidget(win)
    page_w.setGeometry(0, 0, 900, 700)
    behind: list[int] = []
    behind_keys = bind_undo(page_w, lambda: behind.append(1))
    win.show()
    _pump(8)
    seen: dict = {}

    def press_in(dlg, mine):
        def run():
            QTest.keyClick(dlg, Qt.Key.Key_Z)
            _pump()
            seen.setdefault("runs", []).append(list(mine))
            dlg.accept()
        return run

    try:
        for alive in (True, False):
            for sc in behind_keys:
                sc.setEnabled(alive)
            dlg = QDialog(win)
            mine: list[int] = []
            bind_undo(dlg, lambda: mine.append(1))
            call_later(dlg, 30, press_in(dlg, mine))
            sheet_host.run(dlg, full_bleed=True)
        assert seen["runs"] == [[], [1]]       # 뒤의 Z 가 살아 있으면 시트의 Z 도 죽는다
        assert behind == []
    finally:
        h.close_all()
        win.hide()
        win.deleteLater()
        _pump()
