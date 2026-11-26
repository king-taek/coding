"""팝업 버튼 순서·Enter·Esc (현택님 2026-11-24 결정).

- 모든 팝업(메시지 시트 ask · info · choose 와 대화상자 저장 이름 · Notch · 슬롯 선택 ·
  다중 선택 · 슬롯 짝): 오른쪽 아래 [취소][확인] — 확인이 맨 오른쪽
  (P3–P6 A안, 2026-11-25 Q1 B안으로 메시지 시트도 같게).
- 버튼 높이 44.  버튼이 둘 이상인 시트는 Enter 가 아무것도 누르지 않고, Esc = 아니오/취소.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt, QTimer                                  # noqa: E402
from PyQt6.QtTest import QTest                                       # noqa: E402
from PyQt6.QtWidgets import (QApplication, QMessageBox, QPushButton,  # noqa: E402
                             QVBoxLayout, QWidget)

from aoi_verification.app import i18n                                # noqa: E402
from aoi_verification.app.ui import theme                            # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sh         # noqa: E402

_SB = QMessageBox.StandardButton
_H = theme.PROFILE.control_h_lg


def _shown(styled_qapp, w):
    w.show()
    for _ in range(3):
        styled_qapp.processEvents()
    return w


def _left_to_right(buttons):
    return sorted(buttons, key=lambda b: b.mapTo(b.window(), b.rect().topLeft()).x())


# ── 메시지 시트: [취소][확인] ────────────────────────────────────────────────
@pytest.mark.parametrize("buttons,default,want", [
    (_SB.Yes | _SB.No, _SB.No, [_SB.No, _SB.Yes]),
    (_SB.Yes | _SB.No, _SB.Yes, [_SB.No, _SB.Yes]),
    (_SB.Ok | _SB.Cancel, _SB.Cancel, [_SB.Cancel, _SB.Ok]),
    (_SB.Yes | _SB.No | _SB.Cancel, _SB.Cancel, [_SB.Cancel, _SB.No, _SB.Yes]),
])
def test_message_sheet_puts_confirm_last(styled_qapp, buttons, default, want):
    s = _shown(styled_qapp, sh._MessageSheet(None, "t", "b", buttons, default, "question"))
    try:
        order = _left_to_right(s._buttons.values())
        by_btn = {b: sb for sb, b in s._buttons.items()}
        assert [by_btn[b] for b in order] == want
        assert all(b.height() == _H for b in s._buttons.values())
    finally:
        s.hide()
        s.deleteLater()


def test_save_done_choice_is_close_first_open_file_last(styled_qapp):
    """저장 완료: [닫기][폴더 열기][파일 열기] — 파일 열기만 파랑."""
    K = i18n.KO
    d = _shown(styled_qapp, sh._ChoiceSheet(
        K.SAVE_SUCCESS_TITLE, "C:/x.xlsx",
        [("open_file", K.BTN_OPEN_SAVED_FILE, "primary"),
         ("open_dir", K.BTN_OPEN_SAVED_FOLDER, "ghost"),
         ("close", K.MSG_BTN_CLOSE, "ghost")], default="open_file"))
    try:
        keys = {b: k for k, b in d._buttons.items()}
        assert [keys[b] for b in _left_to_right(d._buttons.values())] == \
            ["close", "open_dir", "open_file"]
        assert all(b.height() == _H for b in d._buttons.values())
    finally:
        d.hide()
        d.deleteLater()


def test_choice_cancel_keys_go_left_even_if_listed_last(styled_qapp):
    """선택지를 어떤 순서로 넘기든 '다음에'·None 이 먼저, 주 액션이 맨 오른쪽."""
    d = _shown(styled_qapp, sh._ChoiceSheet(
        "t", "b", [("never", "안 함", "danger"), ("later", "다음에", "ghost"),
                   ("install", "설치", "primary")]))
    try:
        keys = {b: k for k, b in d._buttons.items()}
        assert [keys[b] for b in _left_to_right(d._buttons.values())] == \
            ["later", "never", "install"]
    finally:
        d.hide()
        d.deleteLater()


# ── Enter · Esc (호스트 안에서 실제로 누른다) ───────────────────────────────
@pytest.fixture
def host(styled_qapp):
    app = styled_qapp
    win = QWidget()
    win.setWindowModality(Qt.WindowModality.ApplicationModal)
    win.resize(900, 700)
    QVBoxLayout(win).addWidget(QPushButton("opener", win))
    h = sh.SheetHost(win)
    win._sheets = h
    win.show()
    win.activateWindow()
    for _ in range(8):
        app.processEvents()
    yield win, h
    h.close_all()
    win.hide()
    app.processEvents()
    win.deleteLater()
    app.processEvents()


def _press_enter_then_escape(h, got):
    sheet = h._stack[-1]["widget"]
    for target in {sheet, QApplication.focusWidget()}:
        if target is not None:
            QTest.keyClick(target, Qt.Key.Key_Return)
            QTest.keyClick(target, Qt.Key.Key_Enter)
    got["still_open"] = bool(h._stack) and h._stack[-1]["widget"] is sheet
    got["defaults"] = [b.isDefault() or b.autoDefault() for b in sheet._buttons.values()]
    QTest.keyClick(sheet, Qt.Key.Key_Escape)


@pytest.mark.parametrize("tiles", [True, False])
def test_enter_presses_nothing_on_choice_sheet(host, tiles):
    """choose(tiles=True) 는 QDialog autoDefault 때문에 Enter 가 첫 타일을 눌렀다."""
    win, h = host
    got = {}
    QTimer.singleShot(40, lambda: _press_enter_then_escape(h, got))
    picked = sh.choose(win, "t", "b",
                       [("ref", "기준", "option"), ("val", "검증", "option"),
                        ("both", "둘 다", "option"), (None, "KLA 아님", "option")]
                       if tiles else
                       [("open_file", "파일 열기", "primary"), ("close", "닫기", "ghost")],
                       default=None if tiles else "open_file", tiles=tiles)
    assert got["still_open"], "Enter 가 선택지를 눌러 시트가 닫혔다"
    assert not any(got["defaults"])
    assert picked is None, "Esc 는 아무것도 고르지 않는다"


@pytest.mark.parametrize("buttons,want", [
    (_SB.Yes | _SB.No, _SB.No),
    (_SB.Ok | _SB.Cancel, _SB.Cancel),
])
def test_escape_means_no_or_cancel(host, buttons, want):
    win, h = host
    got = {}
    QTimer.singleShot(40, lambda: _press_enter_then_escape(h, got))
    assert sh.ask(win, "t", "b", buttons, want) == want
    assert got["still_open"]


# ── 제목줄 닫기: 글자 대신 ✕ 그림, 작은 팝업은 없음 ───────────────────────────
def _frame_close(styled_qapp, dlg, *, full_bleed=False):
    """``dlg`` 를 시트로 띄워 제목줄 닫기 버튼과 그 보임 여부를 돌려준다."""
    from PyQt6.QtWidgets import QDialog
    win = QWidget()
    win.resize(1000, 800)
    h = sh.SheetHost(win)
    win._sheets = h
    win.show()
    got = {}

    def look():
        frame = h._stack[-1]["frame"]
        btn = frame._close_btn
        got.update(visible=btn.isVisible(), text=btn.text(),
                   name=btn.accessibleName(), tip=btn.toolTip(),
                   icon=isinstance(btn, sh._CloseIcon))
        dlg.reject()

    QTimer.singleShot(40, look)
    assert isinstance(dlg, QDialog)
    h.run(dlg, full_bleed=full_bleed)
    h.close_all()
    win.hide()
    win.deleteLater()
    styled_qapp.processEvents()
    return got


def test_title_bar_close_is_an_icon_named_close(styled_qapp):
    from PyQt6.QtWidgets import QDialog
    dlg = QDialog()
    dlg.setWindowTitle("큰 창")
    got = _frame_close(styled_qapp, dlg)
    assert got["icon"] and got["visible"]
    assert got["text"] == "", "글자 '닫기' 가 남아 있다"
    assert got["name"] == got["tip"] == i18n.KO.MSG_BTN_CLOSE


def test_close_icon_is_painted_not_a_font_glyph(styled_qapp):
    """✕ 글자는 앱 글꼴에 없다 — 선으로 그려 PC 마다 같은 모양이다."""
    btn = sh._CloseIcon(None)
    try:
        btn.resize(btn.SIZE, btn.SIZE)
        img = btn.grab().toImage()
        c = img.pixelColor(btn.SIZE // 2, btn.SIZE // 2)
        bg = img.pixelColor(4, btn.SIZE // 2)
        assert c != bg, "가운데에 ✕ 가 그려지지 않았다"
    finally:
        btn.deleteLater()


@pytest.mark.parametrize("modname,factory", [
    ("save_name_dialog", lambda m, tmp: m.SaveNameDialog("a.xlsx", tmp)),
    ("notch_dialog", lambda m, tmp: m.NotchDialog("A", "B", "down", "down")),
    ("slot_select_dialog", lambda m, tmp: m.SlotSelectDialog(["S1", "S2"])),
])
def test_small_popups_drop_the_title_bar_close(styled_qapp, tmp_path, modname, factory):
    import importlib
    mod = importlib.import_module(f"aoi_verification.app.ui.widgets.{modname}")
    dlg = factory(mod, tmp_path)
    got = _frame_close(styled_qapp, dlg)
    assert got["visible"] is False, f"{modname}: 작은 팝업에 제목줄 ✕ 가 남아 있다"


# ── 대화상자: 오른쪽 아래 [취소][확인], 높이 44 ─────────────────────────────
def _cancel_ok(dlg, cancel_text, ok_role="primary"):
    from aoi_verification.app.ui.widgets.neon_button import NeonButton
    btns = [b for b in dlg.findChildren(NeonButton) if b.isVisible()]
    cancel = [b for b in btns if b.text() == cancel_text]
    ok = [b for b in btns if b.property("role") == ok_role]
    assert len(cancel) == 1 and ok, [b.text() for b in btns]

    def _pos(b):
        p = b.mapTo(dlg, b.rect().topLeft())
        return (p.y(), p.x())
    return cancel[0], max(ok, key=_pos)       # 맨 아래 줄의 주 버튼


@pytest.mark.parametrize("make,cancel_text", [
    (lambda tmp: __import__("aoi_verification.app.ui.widgets.save_name_dialog",
                            fromlist=["x"]).SaveNameDialog("a.xlsx", tmp),
     i18n.KO.SAVE_NAME_CANCEL),
    (lambda tmp: __import__("aoi_verification.app.ui.widgets.notch_dialog",
                            fromlist=["x"]).NotchDialog("A", "B", "down", "down"),
     i18n.KO.NOTCH_CANCEL),
    (lambda tmp: __import__("aoi_verification.app.ui.widgets.slot_select_dialog",
                            fromlist=["x"]).SlotSelectDialog(["S1", "S2"]),
     i18n.KO.BTN_CANCEL),
    (lambda tmp: __import__("aoi_verification.app.ui.widgets.slot_mapping_dialog",
                            fromlist=["x"]).SlotMappingDialog(["A"], ["B"]),
     i18n.KO.BTN_CANCEL),
    (lambda tmp: __import__("aoi_verification.app.ui.widgets.bulk_select_dialog",
                            fromlist=["x"]).BulkSelectDialog(
        "t", {}, actions=[("x", "X", "primary")]),
     i18n.KO.BTN_CANCEL),
])
def test_dialog_puts_cancel_left_of_ok_bottom_right(styled_qapp, tmp_path, make,
                                                    cancel_text):
    dlg = make(tmp_path)
    try:
        dlg.resize(900, 600)
        _shown(styled_qapp, dlg)
        cancel, ok = _cancel_ok(dlg, cancel_text)
        cx = cancel.mapTo(dlg, cancel.rect().topLeft()).x()
        ox = ok.mapTo(dlg, ok.rect().topLeft()).x()
        assert cx < ox, "[취소] 는 [확인] 왼쪽"
        assert ok.mapTo(dlg, ok.rect().topRight()).x() > dlg.width() * 0.7, \
            "[확인] 이 오른쪽 아래가 아니다"
        assert cancel.property("role") == "ghost"
        assert cancel.height() == ok.height() == _H
    finally:
        dlg.hide()
        dlg.deleteLater()


# ── 키 이름은 'Esc' 로 적는다(D2h A안) ─────────────────────────────────────
def test_ui_strings_write_esc_not_ESC():
    import re
    bad = []
    for name in dir(i18n.KO):
        val = getattr(i18n.KO, name)
        vals = val if isinstance(val, (list, tuple)) else [val]
        for v in vals:
            if isinstance(v, str) and re.search(r"\bESC\b", v):
                bad.append(name)
    assert not bad, f"'ESC' 대신 'Esc': {bad}"
    assert "(Esc)" in i18n.KO.WAFER_MAP_FULLSCREEN_EXIT


# ── 저장: 같은 이름이면 한 번 더 눌러야 덮어쓴다 (현택님 2026-11-25 Q3 A안) ─────────
def test_save_name_asks_before_overwriting(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog
    (tmp_path / "a.xlsx").write_bytes(b"x")
    d = SaveNameDialog("a.xlsx", tmp_path)
    try:
        d._on_ok()
        assert d.chosen is None, "같은 이름인데 묻지 않고 저장했다"
        assert d.btn_ok.text() == i18n.KO.SAVE_NAME_OVERWRITE
        d._on_ok()
        assert d.chosen == "a.xlsx"
        d2 = SaveNameDialog("a.xlsx", tmp_path)
        d2._on_ok()
        d2.edit.setText("b.xlsx")         # 이름을 고치면 확인을 다시 받는다
        assert d2.btn_ok.text() == i18n.KO.SAVE_NAME_OK
        d2._on_ok()
        assert d2.chosen == "b.xlsx"
        d2.deleteLater()
    finally:
        d.deleteLater()


def test_default_save_folders_are_the_results_folder():
    """GL · Overkill · 결과(경로 없음) 저장의 기본 폴더는 결과 폴더 — 마지막 폴더·바탕화면 아님."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
    gl = (root / "widgets" / "gl_viewer_dialog.py").read_text(encoding="utf-8")
    thr = (root / "widgets" / "threshold_preview_dialog.py").read_text(encoding="utf-8")
    res = (root / "pages" / "result_page.py").read_text(encoding="utf-8")
    assert 'last_dir("gl_save")' not in gl
    assert '"Desktop"' not in thr and 'last_dir("threshold_export")' not in thr
    assert "paths.results_dir() / filename" in res
