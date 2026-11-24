"""팝업 버튼 순서·Enter·Esc (현택님 2026-11-24 결정).

- 메시지 시트(ask · info · choose): Windows 순서 [확인][취소] — 확인이 먼저, 취소·닫기가
  맨 오른쪽(P1·P2 B안).
- 대화상자(저장 이름 · Notch · 슬롯 선택 · 다중 선택 · 슬롯 짝): 오른쪽 아래 [취소][확인]
  (P3–P6 A안).  두 순서가 반대인 것은 일부러 고른 것이다.
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


# ── 메시지 시트: Windows 순서 ────────────────────────────────────────────────
@pytest.mark.parametrize("buttons,default,want", [
    (_SB.Yes | _SB.No, _SB.No, [_SB.Yes, _SB.No]),
    (_SB.Yes | _SB.No, _SB.Yes, [_SB.Yes, _SB.No]),
    (_SB.Ok | _SB.Cancel, _SB.Cancel, [_SB.Ok, _SB.Cancel]),
    (_SB.Yes | _SB.No | _SB.Cancel, _SB.Cancel, [_SB.Yes, _SB.No, _SB.Cancel]),
])
def test_message_sheet_puts_confirm_first(styled_qapp, buttons, default, want):
    s = _shown(styled_qapp, sh._MessageSheet(None, "t", "b", buttons, default, "question"))
    try:
        order = _left_to_right(s._buttons.values())
        by_btn = {b: sb for sb, b in s._buttons.items()}
        assert [by_btn[b] for b in order] == want
        assert all(b.height() == _H for b in s._buttons.values())
    finally:
        s.hide()
        s.deleteLater()


def test_save_done_choice_is_open_file_first_close_last(styled_qapp):
    """저장 완료(P2): [파일 열기][폴더 열기][닫기] — 파일 열기만 파랑."""
    K = i18n.KO
    d = _shown(styled_qapp, sh._ChoiceSheet(
        K.SAVE_SUCCESS_TITLE, "C:/x.xlsx",
        [("open_file", K.BTN_OPEN_SAVED_FILE, "primary"),
         ("open_dir", K.BTN_OPEN_SAVED_FOLDER, "ghost"),
         ("close", K.MSG_BTN_CLOSE, "ghost")], default="open_file"))
    try:
        keys = {b: k for k, b in d._buttons.items()}
        assert [keys[b] for b in _left_to_right(d._buttons.values())] == \
            ["open_file", "open_dir", "close"]
        assert all(b.height() == _H for b in d._buttons.values())
    finally:
        d.hide()
        d.deleteLater()


def test_choice_cancel_keys_go_right_even_if_listed_first(styled_qapp):
    """선택지를 어떤 순서로 넘기든 주 액션이 먼저, '다음에'·None 이 맨 오른쪽."""
    d = _shown(styled_qapp, sh._ChoiceSheet(
        "t", "b", [("never", "안 함", "danger"), ("later", "다음에", "ghost"),
                   ("install", "설치", "primary")]))
    try:
        keys = {b: k for k, b in d._buttons.items()}
        assert [keys[b] for b in _left_to_right(d._buttons.values())] == \
            ["install", "never", "later"]
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
