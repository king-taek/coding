"""팝업 손질(현택님 2026-11-24) — 키·포커스·버튼·제목.

- 매치 없음 검토: Esc·제목줄 [닫기] 도 '선택한 대로 매치할까요?' 를 먼저 묻는다.
  버튼 이름은 크게 보기와 같고, [검토 종료]·'?' 도움말은 없다.
- Notch 방향: [취소]·Esc·✕ = 매칭을 시작하지 않고 설정 화면으로(예전엔 닫기 = 확인).
- 저장 결과 시트 제목은 앱 이름이 아니라 '저장 완료' / '저장 실패'.
- 예/아니오 시트: Enter 는 아무것도 누르지 않는다(Esc = 아니오 는 그대로).
- 시트가 열린 동안 Tab 은 시트 안에서만 돌고, 닫히면 연 자리로 포커스가 돌아간다.
- 저장 이름 창: 열리자마자 이름 칸에 포커스 — 바로 타이핑하면 이름이 바뀐다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt, QTimer                                # noqa: E402
from PyQt6.QtTest import QTest                                     # noqa: E402
from PyQt6.QtWidgets import (QApplication, QDialog, QLabel,         # noqa: E402
                             QMessageBox, QPushButton, QVBoxLayout, QWidget)

from aoi_verification.app import i18n                              # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host             # noqa: E402

_SB = QMessageBox.StandardButton
_UI = Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"


@pytest.fixture
def host(styled_qapp):
    """시트 호스트를 가진 '창' + 뒤에 깔린 버튼 둘(왼쪽 레일·시트를 연 버튼 흉내).

    ★ 앱 모달로 띄운다 — 앞선 테스트가 남긴 모달이 이 창의 키를 막지 않게
    (`test_sheet_host.host` 와 같은 이유)."""
    app = styled_qapp
    win = QWidget()
    win.setWindowModality(Qt.WindowModality.ApplicationModal)
    win.resize(900, 700)
    lay = QVBoxLayout(win)
    rail = QPushButton("rail", win)
    opener = QPushButton("opener", win)
    lay.addWidget(rail)
    lay.addWidget(opener)
    h = sheet_host.SheetHost(win)
    win._sheets = h
    win.show()
    win.activateWindow()
    for _ in range(8):
        app.processEvents()
    yield win, h, rail, opener
    h.close_all()
    win.hide()
    app.processEvents()
    win.deleteLater()
    app.processEvents()


def _later(ms: int, fn) -> None:
    QTimer.singleShot(ms, fn)        # 테스트 안에서만 — 중첩 루프가 돌 때 발화한다


def _focus():
    return QApplication.focusWidget()


# ── 매치 없음 사진 검토 ────────────────────────────────────────────────────
def _unmatched_dialog():
    from aoi_verification.app.models.result import MissEntry
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import (
        UnmatchedReviewDialog)
    entry = MissEntry(slot="S1", path=Path("/tmp/S1_r.jpg"), side="ref")
    return UnmatchedReviewDialog([entry], {("S1", "ref"): []}, parent=None)


def test_unmatched_escape_asks_before_closing(styled_qapp, monkeypatch):
    """미확정 선택이 남았으면 Esc 도 [닫기]·✕ 와 같은 질문을 거친다."""
    from aoi_verification.app.ui.widgets import unmatched_review_dialog as urd
    asked = []

    def fake_ask(_parent, title, text, *a, **k):
        asked.append((title, text))
        return _SB.No

    monkeypatch.setattr(urd.sheets, "ask", fake_ask)
    dlg = _unmatched_dialog()
    try:
        dlg._pending[0] = object()                 # 파란 테두리로 고른 후보 하나
        QTest.keyClick(dlg, Qt.Key.Key_Escape)
        assert asked == [(i18n.KO.UNMATCHED_SHEET_TITLE,
                          i18n.KO.UNMATCHED_CONFIRM_ON_CLOSE)]
        assert dlg.result() == QDialog.DialogCode.Rejected
    finally:
        dlg.deleteLater()


def test_unmatched_escape_without_pick_does_not_ask(styled_qapp, monkeypatch):
    from aoi_verification.app.ui.widgets import unmatched_review_dialog as urd
    asked = []
    monkeypatch.setattr(urd.sheets, "ask", lambda *a, **k: asked.append(a) or _SB.No)
    dlg = _unmatched_dialog()
    try:
        dlg.reject()
        assert asked == []
    finally:
        dlg.deleteLater()


def test_unmatched_buttons_match_expand_view(styled_qapp):
    """이름은 크게 보기와 같고, [검토 종료]·'?' 도움말·사용법 문단은 없다."""
    dlg = _unmatched_dialog()
    try:
        assert dlg.btn_prev.text() == i18n.KO.BTN_EXPAND_PREV == "◀ 이전"
        assert dlg.btn_skip.text() == i18n.KO.BTN_EXPAND_NEXT == "다음 ▶"
        assert dlg.btn_confirm.text() == i18n.KO.BTN_CONFIRM_AS_MATCH == "이 후보로 매치"
        assert not hasattr(dlg, "btn_close")
        assert not hasattr(dlg, "_help_btn")
        assert not hasattr(i18n.KO, "UNMATCHED_REVIEW_HINT")
        texts = [b.text() for b in dlg.findChildren(QPushButton)]
        assert "?" not in texts and "검토 종료" not in texts
    finally:
        dlg.deleteLater()


def test_unmatched_close_prompt_is_short():
    assert i18n.KO.UNMATCHED_CONFIRM_ON_CLOSE == "선택한 대로 매치할까요?"


def test_title_bar_close_goes_through_reject(host):
    """제목줄 ✕ 는 다이얼로그의 ``reject`` 를 거친다 — 그래야 매치 없음 검토가 묻는다.

    예전엔 ``close()`` 였고 호스트 필터가 그 Close 를 가로채 ``reject``·``closeEvent``
    를 모두 건너뛰었다."""
    win, h, _rail, _opener = host
    seen = []

    class D(QDialog):
        def reject(self):
            seen.append("reject")
            super().reject()

    dlg = D(win)
    dlg.setWindowTitle("검토")
    _later(40, lambda: h._stack[-1]["frame"].close_requested.emit())
    assert sheet_host.run(dlg) == QDialog.DialogCode.Rejected
    assert seen == ["reject"]
    assert h._stack == []


# ── Notch 방향 ─────────────────────────────────────────────────────────────
def test_notch_dialog_has_no_body_title_and_cancel_ok(styled_qapp):
    from aoi_verification.app.ui.widgets.notch_dialog import NotchDialog
    dlg = NotchDialog("A", "B", "down", "right")
    try:
        titles = [lb for lb in dlg.findChildren(QLabel)
                  if lb.text() == i18n.KO.NOTCH_TITLE]
        assert titles == [], "본문에 제목을 또 크게 쓴다(제목줄과 중복)"
        assert dlg.cancel_btn.text() == i18n.KO.BTN_CANCEL
        assert dlg.cancel_btn.property("role") == "ghost"
        assert dlg.ok_btn.property("role") == "primary"
        dlg.resize(560, 300)
        dlg.show()
        styled_qapp.processEvents()
        assert dlg.cancel_btn.x() < dlg.ok_btn.x(), "[취소] 는 [확인] 왼쪽"
    finally:
        dlg.hide()
        dlg.deleteLater()


@pytest.mark.parametrize("how", ["cancel", "esc"])
def test_notch_dialog_cancel_and_escape_reject(styled_qapp, how):
    from aoi_verification.app.ui.widgets.notch_dialog import NotchDialog
    dlg = NotchDialog("A", "B", "down", "right")
    try:
        dlg.show()
        if how == "cancel":
            dlg.cancel_btn.click()
        else:
            QTest.keyClick(dlg, Qt.Key.Key_Escape)
        assert dlg.result() == QDialog.DialogCode.Rejected
    finally:
        dlg.deleteLater()


def test_main_window_notch_cancel_returns_to_setup(styled_qapp, monkeypatch, tmp_path):
    """창을 취소하면 `_after_notch` 로 가지 않고 매칭 시작을 접는다."""
    from types import SimpleNamespace
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.widgets import notch_dialog
    from aoi_verification.app.workers.notch_auto import AutoNotch

    class FakeDlg:
        def __init__(self, *a, **k):
            pass
        dirs = property(lambda self: ("down", "right"))

    monkeypatch.setattr(notch_dialog, "NotchDialog", FakeDlg)
    code = {"v": QDialog.DialogCode.Rejected}
    monkeypatch.setattr(mw.sheets, "run", lambda dlg, **k: code["v"])
    calls = []
    worker = SimpleNamespace(wait=lambda: None)
    me = SimpleNamespace(
        _input=SimpleNamespace(ref_machine="A", val_machine="B",
                               ref_root=tmp_path / "r", val_root=tmp_path / "v"),
        _scan=object(), _calib_next=([], {}, False), _notch_dirs=(),
        _notch_worker=worker, _notch_guess=("down", "down", None),
        _loading=SimpleNamespace(hide_overlay=lambda: None),
        _notch_needs_confirm=mw.MainWindow._notch_needs_confirm,
        _after_notch=lambda: calls.append("after"),
        _on_match_cancelled=lambda: calls.append("cancelled"),
        sender=lambda: worker)
    me._ask_notch = lambda *a, **k: mw.MainWindow._ask_notch(me, *a, **k)
    res = AutoNotch("right", 30, 2, 40)

    mw.MainWindow._on_notch_auto_done(me, res)
    assert calls == ["cancelled"] and me._calib_next is None

    calls.clear()
    me._calib_next, me._notch_worker = ([], {}, False), worker
    code["v"] = QDialog.DialogCode.Accepted
    mw.MainWindow._on_notch_auto_done(me, res)
    assert calls == ["after"]


# ── 저장 결과 시트 제목 ────────────────────────────────────────────────────
def test_save_result_sheets_are_titled_not_app_name():
    """저장 완료·실패 시트는 앱 이름 대신 '저장 완료' / '저장 실패'."""
    assert i18n.KO.SAVE_SUCCESS_TITLE == "저장 완료"
    assert i18n.KO.SAVE_FAIL_TITLE == "저장 실패"
    files = [_UI / "main_window.py", _UI / "pages" / "result_page.py",
             _UI / "widgets" / "zone_origin_dialog.py"]
    for f in files:
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"sheets\.(?:choose|info|warn)\(\s*self,\s*(\S+?),\s*"
                             r"(?:i18n\.KO|_KO)\.(SAVE_SUCCESS_FMT|SAVE_FAIL_FMT|body)",
                             src):
            want = "SAVE_SUCCESS_TITLE" if "SUCCESS" in m.group(2) else "SAVE_FAIL_TITLE"
            assert m.group(1).endswith(want), f"{f.name}: {m.group(0)}"


# ── 예/아니오 시트: Enter 는 누르지 않는다 ──────────────────────────────────
@pytest.mark.parametrize("default", [_SB.Yes, _SB.No])
def test_enter_presses_nothing_on_yes_no_sheet(host, default):
    win, h, _rail, _opener = host
    got = {}

    def press_enter():
        sheet = h._stack[-1]["widget"]
        for target in {sheet, _focus()}:
            if target is not None:
                QTest.keyClick(target, Qt.Key.Key_Return)
                QTest.keyClick(target, Qt.Key.Key_Enter)
        got["still_open"] = bool(h._stack) and h._stack[-1]["widget"] is sheet
        got["defaults"] = [b.isDefault() or b.autoDefault()
                           for b in sheet._buttons.values()]
        QTest.keyClick(sheet, Qt.Key.Key_Escape)   # Esc = 아니오 는 그대로

    _later(40, press_enter)
    assert sheet_host.ask(win, "질문", "계속할까요?", _SB.Yes | _SB.No, default) == _SB.No
    assert got["still_open"], "Enter 가 버튼을 눌러 시트가 닫혔다"
    assert got["defaults"] == [False, False]


def test_single_button_info_sheet_is_unchanged(styled_qapp):
    """버튼 하나뿐인 안내는 손대지 않는다."""
    s = sheet_host._MessageSheet(None, "알림", "끝났습니다", _SB.Ok, _SB.NoButton, "info")
    try:
        assert s._enter_off is False
    finally:
        s.deleteLater()


# ── Tab 은 시트 밖으로 새지 않고, 닫히면 연 자리로 ──────────────────────────
def _two_button_dialog(win):
    dlg = QDialog(win)
    dlg.setWindowTitle("시트")
    lay = QVBoxLayout(dlg)
    a, b = QPushButton("a", dlg), QPushButton("b", dlg)
    lay.addWidget(a)
    lay.addWidget(b)
    return dlg


@pytest.mark.parametrize("key", [Qt.Key.Key_Tab, Qt.Key.Key_Backtab])
def test_tab_stays_inside_the_sheet(host, key):
    win, h, rail, opener = host
    dlg = _two_button_dialog(win)
    seen = []

    def tab_around():
        top = h._stack[-1]["root"]
        for _ in range(7):
            QTest.keyClick(_focus(), key)
            w = _focus()
            seen.append(w)
            assert w is not None and sheet_host.SheetHost._is_within(w, top), \
                f"Tab 이 시트 밖으로 새 나갔다: {w}"
        dlg.reject()

    _later(40, tab_around)
    sheet_host.run(dlg)
    assert len(seen) == 7
    assert rail not in seen and opener not in seen
    assert len({id(w) for w in seen}) >= 3, "시트 안의 버튼·✕ 를 돌지 않았다"


def test_tab_from_behind_pulls_focus_back_into_the_sheet(host):
    win, h, rail, _opener = host
    dlg = _two_button_dialog(win)
    got = {}

    def go():
        rail.setFocus()                    # 마우스 등으로 포커스가 뒤에 가 있었다
        QTest.keyClick(rail, Qt.Key.Key_Tab)
        got["in"] = sheet_host.SheetHost._is_within(_focus(), h)
        dlg.reject()

    _later(40, go)
    sheet_host.run(dlg)
    assert got["in"]


def test_focus_returns_to_the_opener_after_close(host):
    win, h, rail, opener = host
    opener.setFocus()
    QApplication.processEvents()
    assert _focus() is opener
    dlg = _two_button_dialog(win)
    _later(40, dlg.reject)
    sheet_host.run(dlg)
    assert _focus() is opener, f"닫힌 뒤 포커스가 {_focus()} 로 갔다(연 버튼이 아님)"


def test_focus_returns_after_fullscreen_viewer(host, tmp_path):
    """크게 보기(전체화면 뷰어)도 같은 길 — Esc 로 닫으면 연 자리로."""
    from aoi_verification.app.ui.widgets.zoom_window import FullscreenViewer
    from PyQt6.QtGui import QImage
    win, h, rail, opener = host
    opener.setFocus()
    QApplication.processEvents()
    img = QImage(64, 48, QImage.Format.Format_RGB32)
    img.fill(0)
    viewer = FullscreenViewer(tmp_path / "x.jpg", win, image=img)
    seen = []

    def go():
        for _ in range(5):
            QTest.keyClick(_focus(), Qt.Key.Key_Tab)
            seen.append(_focus())
        viewer.close()

    _later(40, go)
    sheet_host.run(viewer, full_bleed=True)
    assert rail not in seen and opener not in seen, "Tab 이 뒤의 레일로 새 나갔다"
    assert _focus() is opener


def test_nested_sheet_returns_focus_inside_the_outer_sheet(host):
    win, h, _rail, _opener = host
    outer = _two_button_dialog(win)
    b_outer = outer.findChildren(QPushButton)[-1]
    got = {}

    def open_inner():
        b_outer.setFocus()
        inner = _two_button_dialog(win)
        _later(30, inner.reject)
        sheet_host.run(inner)
        got["back"] = _focus() is b_outer
        outer.reject()

    _later(40, open_inner)
    sheet_host.run(outer)
    assert got["back"], "안쪽 시트가 닫힌 뒤 바깥 시트의 그 버튼으로 돌아가지 않았다"


# ── 저장 이름 창 ───────────────────────────────────────────────────────────
def test_save_name_edit_has_focus_and_typing_replaces(host, tmp_path):
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog
    win, h, _rail, _opener = host
    dlg = SaveNameDialog("AOI검증결과_x.xlsx", tmp_path, win)
    got = {}

    def check():
        got["focus"] = _focus() is dlg.edit
        got["selected"] = dlg.edit.selectedText()
        QTest.keyClicks(_focus(), "NEW")   # keyClicks 는 ASCII 만
        got["text"] = dlg.edit.text()
        dlg.reject()

    _later(40, check)
    sheet_host.run(dlg)
    assert got["focus"], "열렸는데 이름 칸에 포커스가 없다(한 번 더 눌러야 한다)"
    assert got["selected"] == "AOI검증결과_x"
    assert got["text"] == "NEW.xlsx"


def test_save_name_has_no_body_title_or_explanations(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.save_name_dialog import SaveNameDialog
    dlg = SaveNameDialog("a.xlsx", tmp_path, None,
                         fields={"machine": "17", "material": "M"})
    try:
        texts = [lb.text() for lb in dlg.findChildren(QLabel) if lb.text()]
        assert i18n.KO.SAVE_NAME_TITLE not in texts
        muted = [lb.text() for lb in dlg.findChildren(QLabel)
                 if lb.property("role") == "muted" and lb.text()]
        assert muted == [i18n.KO.SAVE_NAME_FOLDER_FMT.format(path=str(tmp_path))]
    finally:
        dlg.deleteLater()
