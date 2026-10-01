"""창을 최소화했다가 복원해도 열려 있던 시트(앱 내부 팝업)가 **그대로** 남는다.

사용자 신고: 프로그램을 최소화했다 다시 열면 팝업이 사라져 있었다.  원인은
``SheetHost.eventFilter`` 가 시트의 ``Hide`` 를 원인을 따지지 않고 '닫힘' 으로 받은 것 —
Qt 는 창을 최소화하면 자식 전체에 **자발적** Hide 를 보내고(``hideChildren(true)``),
부모가 숨으면 비자발적 Hide 를 보낸다.  둘 다 시트 자신이 숨겨진 것(``isHidden()``)이
아니다.

★ 여기의 최소화는 offscreen 플랫폼의 ``showMinimized()`` 다.  Qt 가 보내는
``WindowStateChange`` → 자식 Hide 경로는 같지만, Windows 의 실제 작업표시줄 최소화·복원을
대신하지는 못한다 — 그것은 수동 검증으로 남긴다.

모든 테스트는 중첩 루프가 남아도 스위트가 멈추지 않게 ``_guard`` 로 유한 시간에 끊는다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, Qt, QTimer                       # noqa: E402
from PyQt6.QtGui import QKeyEvent                                 # noqa: E402
from PyQt6.QtWidgets import (QApplication, QDialog, QLabel,       # noqa: E402
                             QLineEdit, QMessageBox, QVBoxLayout,
                             QWidget)

from aoi_verification.app.ui.widgets import sheet_host            # noqa: E402

_SB = QMessageBox.StandardButton
_GUARD_MS = 4000
# 예약 콜백 안의 예외 — Qt 슬롯에서 새면 PyQt 가 프로세스를 abort 하므로 여기 모은다.
_ERRORS: list = []


@pytest.fixture
def host(qapp):
    win = QWidget()
    win.resize(900, 700)
    h = sheet_host.SheetHost(win)
    win._sheets = h
    win.show()
    for _ in range(8):
        qapp.processEvents()
    _ERRORS.clear()
    yield win, h
    h.close_all()
    win.hide()
    qapp.processEvents()
    win.deleteLater()
    qapp.processEvents()
    assert not _ERRORS, f"예약 콜백에서 예외: {_ERRORS!r}"


def _later(ms: int, fn) -> None:
    def _safe():
        try:
            fn()
        except Exception as exc:            # noqa: BLE001 — 테스트가 판정한다
            _ERRORS.append(exc)
    QTimer.singleShot(ms, _safe)


def _guard(h, timed_out: list) -> None:
    """실패해도 중첩 루프가 영원히 남지 않게 — 유한 시간 뒤 모두 닫는다."""
    def _bail():
        if h._stack:
            timed_out.append(True)
            h.close_all()
    QTimer.singleShot(_GUARD_MS, _bail)


def _minimize_restore(win) -> None:
    win.showMinimized()
    QApplication.processEvents()
    win.showNormal()
    QApplication.processEvents()


def _titled_dialog(parent) -> tuple[QDialog, QLineEdit]:
    dlg = QDialog(parent)
    dlg.setWindowTitle("슬롯 선택")
    edit = QLineEdit(dlg)
    QVBoxLayout(dlg).addWidget(edit)
    return dlg, edit


# ── 1) 일반 다이얼로그(제목줄 있는 시트) ─────────────────────────────────
def test_titled_dialog_survives_minimize_and_restore(qapp, host):
    win, h = host
    dlg, edit = _titled_dialog(win)
    finished: list = []
    dlg.finished.connect(finished.append)
    seen: dict = {}
    timed_out: list = []

    def step_min():
        edit.setText("입력 중")
        win.showMinimized()
        qapp.processEvents()
        seen["while_min"] = (len(h._stack), list(finished))

    def step_restore():
        win.showNormal()
        qapp.processEvents()
        seen["after"] = (len(h._stack), h._stack[-1]["widget"] if h._stack else None,
                         dlg.isVisible(), edit.text(), list(finished))
        dlg.accept()

    _later(30, step_min)
    _later(90, step_restore)
    _guard(h, timed_out)
    code = sheet_host.run(dlg)

    assert not timed_out
    assert seen["while_min"] == (1, []), "최소화했더니 시트가 닫혔다(또는 finished 발행)"
    n, top, visible, text, fin = seen["after"]
    assert n == 1 and top is dlg, "복원 뒤 같은 시트 객체가 남아 있어야 한다"
    assert visible, "복원했는데 시트가 다시 보이지 않는다"
    assert text == "입력 중", "입력 상태가 유지돼야 한다"
    assert fin == [], "최소화가 결과(finished)를 만들면 안 된다"
    assert code == QDialog.DialogCode.Accepted
    assert finished == [QDialog.DialogCode.Accepted.value], "finished 는 정확히 한 번"
    assert h._stack == []


# ── 2) 메시지 시트 ─────────────────────────────────────────────────────────
def test_message_sheet_survives_minimize_and_keeps_its_answer(qapp, host):
    win, h = host
    seen: dict = {}
    timed_out: list = []

    def step():
        top = h._stack[-1]["widget"] if h._stack else None
        _minimize_restore(win)
        seen["same"] = bool(h._stack) and h._stack[-1]["widget"] is top
        seen["visible"] = top is not None and top.isVisible()
        if h._stack:
            h._stack[-1]["widget"]._answer_with(_SB.No)

    _later(40, step)
    _guard(h, timed_out)
    r = sheet_host.ask(win, "확인", "계속할까요?", _SB.Yes | _SB.No, _SB.Yes)
    assert not timed_out
    assert seen == {"same": True, "visible": True}
    assert r == _SB.No, "복원 뒤 사용자가 고른 답이 그대로 돌아와야 한다"


def test_escape_after_restore_still_cancels(qapp, host):
    win, h = host
    timed_out: list = []

    def step():
        _minimize_restore(win)
        top = h._stack[-1]["widget"]
        QApplication.sendEvent(top, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                              Qt.KeyboardModifier.NoModifier))

    _later(40, step)
    _guard(h, timed_out)
    r = sheet_host.ask(win, "확인", "계속할까요?", _SB.Yes | _SB.No, _SB.Yes)
    assert not timed_out
    assert r == _SB.No


def test_close_button_after_restore_rejects(qapp, host):
    win, h = host
    dlg, _ = _titled_dialog(win)
    timed_out: list = []

    def step():
        _minimize_restore(win)
        h._stack[-1]["frame"].close_requested.emit()

    _later(40, step)
    _guard(h, timed_out)
    assert sheet_host.run(dlg) == QDialog.DialogCode.Rejected
    assert not timed_out
    assert h._stack == []


# ── 3) 중첩 시트 ───────────────────────────────────────────────────────────
def test_nested_sheets_keep_their_order_across_minimize(qapp, host):
    win, h = host
    outer, _ = _titled_dialog(win)
    inner, _ = _titled_dialog(outer)
    seen: dict = {}
    timed_out: list = []

    def open_inner():
        _later(40, minimize_then_close_inner)
        seen["inner_code"] = sheet_host.run(inner)
        seen["after_inner"] = [e["widget"] for e in h._stack]
        outer.accept()

    def minimize_then_close_inner():
        _minimize_restore(win)
        seen["order"] = [e["widget"] for e in h._stack]
        inner.accept()

    _later(30, open_inner)
    _guard(h, timed_out)
    code = sheet_host.run(outer)
    assert not timed_out
    assert seen["order"] == [outer, inner], "최소화 뒤 스택 순서가 바뀌었다"
    assert seen["inner_code"] == QDialog.DialogCode.Accepted
    assert seen["after_inner"] == [outer], "안쪽을 닫으면 바깥 시트로 돌아와야 한다"
    assert code == QDialog.DialogCode.Accepted
    assert h._stack == []


# ── 4) 반복 ────────────────────────────────────────────────────────────────
def test_repeated_minimize_restore_does_not_duplicate(qapp, host):
    win, h = host
    dlg, _ = _titled_dialog(win)
    counts: list = []
    timed_out: list = []

    def step():
        for _ in range(10):
            _minimize_restore(win)
            counts.append((len(h._stack), h._app_filter_on,
                           sum(1 for c in h.children()
                               if isinstance(c, sheet_host._SheetFrame))))
        dlg.reject()

    _later(30, step)
    _guard(h, timed_out)
    assert sheet_host.run(dlg) == QDialog.DialogCode.Rejected
    assert not timed_out
    assert counts == [(1, True, 1)] * 10
    assert h._stack == [] and not h._app_filter_on


# ── 5) 숨김의 두 종류 ─────────────────────────────────────────────────────
def test_parent_hide_is_not_a_close(qapp, host):
    """창 자체가 숨었다 다시 보이는 것(비자발적 Hide)도 시트 종료가 아니다."""
    win, h = host
    dlg, _ = _titled_dialog(win)
    seen: dict = {}
    timed_out: list = []

    def step():
        win.hide()
        qapp.processEvents()
        win.show()
        qapp.processEvents()
        seen["n"] = len(h._stack)
        seen["visible"] = dlg.isVisible()
        dlg.accept()

    _later(30, step)
    _guard(h, timed_out)
    sheet_host.run(dlg)
    assert not timed_out
    assert seen == {"n": 1, "visible": True}


def test_the_sheet_hiding_itself_still_ends_the_loop(qapp, host):
    """시트 **자신의** ``hide()`` 는 여전히 닫기다(`test_run_returns_when_the_sheet_just_hides`)."""
    win, h = host
    dlg, _ = _titled_dialog(win)
    timed_out: list = []

    def step():
        _minimize_restore(win)
        dlg.hide()

    _later(30, step)
    _guard(h, timed_out)
    sheet_host.run(dlg)
    assert not timed_out, "명시적 hide 로 루프가 끝나지 않았다"
    assert h._stack == []


# ── 6) 최소화 중 끝난 작업 ────────────────────────────────────────────────
def test_work_finishing_while_minimized_shows_after_restore(qapp, host):
    """최소화된 동안 시트 안의 작업이 끝나 결과를 갱신해도 취소되지 않는다."""
    win, h = host
    dlg = QDialog(win)
    dlg.setWindowTitle("작업")
    label = QLabel("진행 중", dlg)
    QVBoxLayout(dlg).addWidget(label)
    seen: dict = {}
    timed_out: list = []

    def minimize():
        win.showMinimized()
        qapp.processEvents()

    def work_done():
        label.setText("완료")

    def restore():
        win.showNormal()
        qapp.processEvents()
        seen["n"] = len(h._stack)
        seen["text"] = label.text() if label.isVisible() else None
        dlg.accept()

    _later(30, minimize)
    _later(70, work_done)
    _later(120, restore)
    _guard(h, timed_out)
    sheet_host.run(dlg)
    assert not timed_out
    assert seen == {"n": 1, "text": "완료"}


# ── 7) 실제 앱 종료 ────────────────────────────────────────────────────────
def test_closing_the_main_window_ends_open_sheet_loops(qapp, monkeypatch):
    """창이 숨어도 시트가 닫히지 않으므로, 앱 종료가 명시적으로 루프를 끝내야 한다."""
    from aoi_verification.app.ui import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    _ERRORS.clear()
    win = mw.MainWindow()
    win.show()
    qapp.processEvents()
    h = win._sheets
    outer, _ = _titled_dialog(win)
    inner, _ = _titled_dialog(outer)
    seen: dict = {}
    timed_out: list = []

    def open_inner():
        _later(40, close_window)
        sheet_host.run(inner)
        seen["inner_returned"] = True

    def close_window():
        seen["depth"] = len(h._stack)
        win.close()

    _later(30, open_inner)
    _guard(h, timed_out)
    try:
        sheet_host.run(outer)
    finally:
        h.close_all()
        win.close()
    assert not _ERRORS, f"예약 콜백에서 예외: {_ERRORS!r}"
    assert not timed_out, "창을 닫았는데 시트 루프가 남았다"
    assert seen["depth"] == 2
    assert seen.get("inner_returned")
    assert h._stack == []
