"""앱 **내부** 창(시트) — 별도 OS 창으로 뜨던 팝업이 프로그램 안에서 뜬다.

사용자 요청: "켜지는 모든 팝업은 새로운 창이 아니라 프로그램 내에서 켜지는 창으로".

여기서 고정하는 계약(어긋나면 앱이 굳거나 팝업이 사라진다):

- ``run()`` 은 ``exec()`` 과 **같은 동기 의미**를 준다 — accept/reject/Esc/닫기 어느
  경로에서도 정확한 코드로 돌아오고 **중첩 이벤트 루프가 남지 않는다**(남으면 그 자리에서
  앱이 굳는다).
- ``ask()`` 의 반환값은 ``QMessageBox.StandardButton`` — 호출부 39곳의
  ``if r == StandardButton.Yes:`` 를 그대로 살리기 위한 것이다.
- 시트는 **쌓인다**(다이얼로그 안에서 다이얼로그를 여는 곳이 5곳 있다).
- 호스트가 없으면 **네이티브로 폴백**한다 — 구조 변경이 '아무것도 안 뜨는' 경로를
  만들면 안 된다.
- 창 크기를 따라간다(리사이즈).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QTimer                                  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QDialog, QLabel,       # noqa: E402
                             QMessageBox, QWidget)

from aoi_verification.app.ui import theme                         # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host            # noqa: E402

_SB = QMessageBox.StandardButton
_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(qapp):
    """호스트를 가진 '창' — 실제 메인 창과 같은 규약(`_sheets` 속성)."""
    win = QWidget()
    win.resize(900, 700)
    h = sheet_host.SheetHost(win)
    win._sheets = h
    win.show()
    for _ in range(8):
        qapp.processEvents()
    yield win, h
    h.close_all()
    win.hide()
    qapp.processEvents()
    win.deleteLater()
    qapp.processEvents()


def _later(ms: int, fn) -> None:
    """중첩 루프 **안에서** 실행할 일을 예약한다(루프가 돌아야 발화한다)."""
    QTimer.singleShot(ms, fn)


# ── run(): 동기 의미와 결과 코드 ──────────────────────────────────────────
def test_run_returns_accepted_and_leaves_no_loop(qapp, host):
    win, h = host
    dlg = QDialog(win)
    _later(30, dlg.accept)
    code = sheet_host.run(dlg)
    assert code == QDialog.DialogCode.Accepted
    assert h._stack == [], "시트 스택이 비지 않았다 — 중첩 루프가 남으면 앱이 굳는다"
    assert h.isHidden(), "시트가 모두 닫혔는데 호스트가 화면에 남았다"


def test_run_returns_rejected(qapp, host):
    win, h = host
    dlg = QDialog(win)
    _later(30, dlg.reject)
    assert sheet_host.run(dlg) == QDialog.DialogCode.Rejected
    assert h._stack == []


def test_run_returns_when_the_sheet_just_hides(qapp, host):
    """accept/reject 없이 **숨기만** 해도 루프가 끝나야 한다.

    안 끝나면 호출부가 그 줄에서 영원히 멈춘다 — 사용자에겐 '프로그램이 멈췄다'다."""
    win, h = host
    dlg = QDialog(win)
    _later(30, dlg.hide)
    sheet_host.run(dlg)
    assert h._stack == []


def test_sheet_is_inside_the_window_not_a_separate_one(qapp, host):
    """★ 시트는 **창 안**에 있어야 한다 — 부모가 호스트이고 창 플래그가 Widget 이다."""
    win, h = host
    dlg = QDialog(win)
    seen = {}

    def check():
        seen["parent_is_host"] = dlg.parentWidget() is h
        seen["inside"] = (h.rect().contains(dlg.geometry())
                          if not dlg.geometry().isEmpty() else False)
        seen["scrim"] = not h._scrim.isHidden()
        dlg.accept()

    _later(40, check)
    sheet_host.run(dlg)
    assert seen["parent_is_host"], "시트가 별도 창으로 떴다(부모가 호스트가 아니다)"
    assert seen["inside"], "시트가 창 밖으로 나갔다"
    assert seen["scrim"], "뒤를 덮는 스크림이 없다"


def test_full_bleed_sheet_fills_the_window(qapp, host):
    """뷰어는 창 내부를 거의 꽉 채운다(사용자 결정: 뷰어도 앱 안 전체 영역 시트)."""
    win, h = host
    dlg = QDialog(win)
    got = {}

    def check():
        got["w"] = dlg.width()
        got["h"] = dlg.height()
        dlg.accept()

    _later(40, check)
    sheet_host.run(dlg, full_bleed=True)
    assert got["w"] >= win.width() - 40, f"뷰어 시트 폭 {got['w']} — 너무 좁다"
    assert got["h"] >= win.height() - 40


def test_sheets_stack_and_only_the_top_takes_keys(qapp, host):
    """다이얼로그 안에서 다이얼로그를 여는 5곳을 위해 — 쌓이고, 맨 위만 입력을 받는다."""
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    win, h = host
    outer = QDialog(win)
    inner = QDialog(win)
    depth = {}

    def open_inner():
        depth["outer_open"] = len(h._stack)

        def finish_inner():
            depth["both_open"] = len(h._stack)
            depth["top_is_inner"] = h._stack[-1]["widget"] is inner
            # 뒤(바깥 시트)로 가는 키는 버려진다 — Tab 이 새 나가지 않게.
            ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                           Qt.KeyboardModifier.NoModifier)
            depth["blocked"] = h.eventFilter(outer, ev) is True
            depth["allowed"] = h.eventFilter(inner, ev) is False
            inner.accept()

        _later(30, finish_inner)
        sheet_host.run(inner)
        depth["after_inner"] = len(h._stack)
        outer.accept()

    _later(30, open_inner)
    sheet_host.run(outer)
    assert depth["outer_open"] == 1
    assert depth["both_open"] == 2, "시트가 쌓이지 않았다"
    assert depth["top_is_inner"] is True
    assert depth["blocked"] is True, "뒤 시트로 가는 키가 통과했다"
    assert depth["allowed"] is True, "맨 위 시트의 키를 막았다"
    assert depth["after_inner"] == 1, "안쪽 시트가 닫혀도 스택이 줄지 않았다"
    assert h._stack == []


def test_sheet_follows_the_window_resize(qapp, host):
    win, h = host
    dlg = QDialog(win)
    got = {}

    def check():
        win.resize(600, 500)
        for _ in range(6):
            qapp.processEvents()
        got["host"] = (h.width(), h.height())
        got["inside"] = h.rect().contains(dlg.geometry())
        dlg.accept()

    _later(40, check)
    sheet_host.run(dlg, full_bleed=True)
    assert got["host"] == (600, 500), f"호스트가 창을 따라오지 않았다: {got['host']}"
    assert got["inside"], "리사이즈 후 시트가 창 밖으로 나갔다"


# ── 메시지 시트 — QMessageBox 대체 ────────────────────────────────────────
def _answer(qapp, h, standard_button, delay=30):
    """열린 메시지 시트에서 지정 표준 버튼을 누른다."""
    def click():
        sheet = h._stack[-1]["widget"]
        sheet._buttons[standard_button].click()
    _later(delay, click)


def test_ask_returns_standard_buttons(qapp, host):
    """★ 반환값은 ``QMessageBox.StandardButton`` — 호출부 39곳을 그대로 살리기 위해."""
    win, h = host
    _answer(qapp, h, _SB.Yes)
    assert sheet_host.ask(win, "질문", "계속할까요?") == _SB.Yes
    _answer(qapp, h, _SB.No)
    assert sheet_host.ask(win, "질문", "계속할까요?") == _SB.No


def test_info_shows_only_ok(qapp, host):
    win, h = host
    got = {}

    def check():
        sheet = h._stack[-1]["widget"]
        got["buttons"] = set(sheet._buttons)
        sheet._buttons[_SB.Ok].click()

    _later(30, check)
    assert sheet_host.info(win, "알림", "끝났습니다") == _SB.Ok
    assert got["buttons"] == {_SB.Ok}


def test_escape_answers_like_a_message_box(qapp, host):
    """Esc = 취소/아니오(있으면) — QMessageBox 와 같은 어휘."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    win, h = host

    def esc():
        QTest.keyClick(h._stack[-1]["widget"], Qt.Key.Key_Escape)

    _later(30, esc)
    assert sheet_host.ask(win, "질문", "지울까요?",
                          _SB.Yes | _SB.Cancel) == _SB.Cancel


def test_message_sheet_has_its_own_surface(qapp, host):
    """스크림이 옅어도 읽히도록 시트는 자기 면을 갖는다(QSS role)."""
    win, h = host
    got = {}

    def check():
        sheet = h._stack[-1]["widget"]
        got["role"] = sheet.property("role")
        sheet._buttons[_SB.Ok].click()

    _later(30, check)
    sheet_host.info(win, "알림", "본문")
    assert got["role"] == "sheet"
    assert 'QDialog[role="sheet"]' in _QSS
    assert "$" not in theme.render_qss(_QSS)      # 토큰 오타 조기 노출


def test_long_text_does_not_widen_the_sheet_past_the_window(qapp, host):
    """긴 본문이 시트를 창 밖으로 밀지 않는다(800×600 에서도 가로 스크롤 0)."""
    win, h = host
    win.resize(800, 600)
    for _ in range(6):
        qapp.processEvents()
    got = {}

    def check():
        sheet = h._stack[-1]["widget"]
        got["w"] = sheet.width()
        got["inside"] = h.rect().contains(sheet.geometry())
        sheet._buttons[_SB.Ok].click()

    _later(40, check)
    sheet_host.info(win, "알림", "아주 긴 문장입니다. " * 40)
    assert got["inside"], f"긴 본문이 시트를 창 밖으로 밀었다(폭 {got['w']})"


# ── 폴백 — 호스트가 없어도 앱은 계속 동작해야 한다 ─────────────────────────
def test_falls_back_to_native_when_there_is_no_host(qapp, monkeypatch):
    """★ 호스트가 없으면 네이티브로 폴백한다 — 초기화 중 팝업·단위 테스트·창 밖 호출.

    폴백이 없으면 '팝업이 아예 뜨지 않는' 경로가 생긴다.  구조 변경이 기능을 없애는 것이
    가장 나쁜 결과다."""
    calls: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: (calls.append(a), _SB.Yes)[1])
    orphan = QLabel("호스트 없음")            # 창에 붙지 않은 위젯
    assert sheet_host.host_for(orphan) is None
    assert sheet_host.ask(orphan, "질문", "본문") == _SB.Yes
    assert calls, "네이티브 폴백이 불리지 않았다"


def test_host_for_finds_the_host_through_parents(qapp, host):
    """시트 안의 위젯에서도 호스트를 찾아야 한다(중첩 팝업 5곳)."""
    win, h = host
    child = QWidget(win)
    grand = QLabel("깊은 자식", child)
    assert sheet_host.host_for(grand) is h


# ── 전역 이벤트 필터는 열려 있을 때만 ───────────────────────────────────────
def test_app_filter_is_installed_only_while_a_sheet_is_open(qapp, host):
    """전역 필터는 마우스 이동까지 전부 받는다 — 평소엔 걸어 두지 않는다."""
    win, h = host
    assert h._app_filter_on is False
    dlg = QDialog(win)
    got = {}

    def check():
        got["on"] = h._app_filter_on
        dlg.accept()

    _later(30, check)
    sheet_host.run(dlg)
    assert got["on"] is True, "시트가 열렸는데 전역 필터가 없다(키가 새 나간다)"
    assert h._app_filter_on is False, "시트를 닫았는데 전역 필터가 남았다"
