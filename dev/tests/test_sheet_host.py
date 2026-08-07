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
def test_titled_dialog_gets_a_title_bar_and_a_close_button(qapp, host):
    """★ 창 안으로 들어오면 **OS 타이틀바가 없다** — 제목과 닫기를 시트가 대신해야 한다.

    그러지 않으면 '무슨 팝업인지 모르고, 닫을 수도 없는' 시트가 생긴다(자체 닫기 버튼이
    없는 뷰어에서 특히 치명적이다).  창 제목이 있으면 제목줄을 자동으로 씌운다."""
    from aoi_verification.app.ui.widgets.sheet_host import _SheetFrame
    win, h = host
    dlg = QDialog(win)
    dlg.setWindowTitle("슬롯 짝짓기")
    got = {}

    def check():
        entry = h._stack[-1]
        frame = entry["frame"]
        got["framed"] = isinstance(frame, _SheetFrame)
        got["titles"] = [lb.text() for lb in frame.findChildren(QLabel)
                         if lb.property("role") == "sheetTitle"]
        got["hosted"] = dlg.parentWidget() is frame
        # ✕(닫기) 버튼을 누르면 시트가 닫혀야 한다.
        btns = [b for b in frame.findChildren(sheet_host.NeonButton)]
        got["has_close"] = bool(btns)
        btns[0].click()

    _later(40, check)
    sheet_host.run(dlg)
    assert got["framed"], "제목줄이 씌워지지 않았다"
    assert got["titles"] == ["슬롯 짝짓기"], f"제목이 없다: {got['titles']}"
    assert got["hosted"], "다이얼로그가 제목줄 안으로 들어가지 않았다"
    assert got["has_close"], "닫기 버튼이 없다 — 닫을 방법이 사라졌다"
    assert h._stack == [], "닫기 버튼을 눌렀는데 시트가 남았다"


def test_message_sheet_is_not_double_titled(qapp, host):
    """메시지/선택 시트는 스스로 제목을 그린다 — 제목줄을 겹쳐 씌우지 않는다."""
    win, h = host
    got = {}

    def check():
        got["frame"] = h._stack[-1]["frame"]
        h._stack[-1]["widget"]._buttons[_SB.Ok].click()

    _later(30, check)
    sheet_host.info(win, "알림", "본문")
    assert got["frame"] is None, "메시지 시트에 제목줄이 한 겹 더 씌워졌다"


def test_no_popup_escapes_to_a_separate_window(qapp):
    """★ 앱 코드에 **네이티브 팝업 호출이 남아 있지 않아야** 한다.

    사용자 요청은 "켜지는 모든 팝업"이다 — 한 곳만 남아도 그 경로에서 창이 다시 뜬다.
    금지: ``QMessageBox.information/warning/critical/question/about`` 정적 호출과
    ``.exec()``.  둘 다 ``sheet_host`` 를 통과해야 한다(그 안의 폴백 구현은 예외).

    ※ ``QFileDialog`` 는 검사하지 않는다 — OS 파일 브라우저는 앱 팝업이 아니고, 앱 안에
    다시 만들면 네트워크 경로·경로 직접 입력·최근 위치를 잃는다(사용자 결정)."""
    import io
    import tokenize
    ui_dir = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui")
    bad_msg = ("QMessageBox.information", "QMessageBox.warning",
               "QMessageBox.critical", "QMessageBox.question",
               "QMessageBox.about")
    offenders: list[str] = []
    for path in sorted(ui_dir.rglob("*.py")):
        if path.name == "sheet_host.py":
            continue                       # 폴백 구현체 — 유일하게 허용된 곳
        src = path.read_text(encoding="utf-8")
        # 주석·문자열(설명)은 위반이 아니다 — 코드만 본다.
        try:
            code = " ".join(
                t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.COMMENT, tokenize.STRING))
        except Exception:
            code = src
        rel = path.relative_to(ui_dir.parents[2])
        for pat in bad_msg:
            if pat.replace(".", " . ") in code or pat in code:
                offenders.append(f"{rel}: {pat}")
        if ". exec ( )" in code or ".exec()" in code:
            offenders.append(f"{rel}: .exec()")
    assert not offenders, (
        "별도 OS 창으로 뜨는 팝업이 남았다 — sheet_host 를 쓰라: " + ", ".join(offenders))


def test_dialogs_do_not_claim_window_powers(qapp):
    """★ 시트로 뜨는 다이얼로그는 **창 제어를 요구하지 않는다.**

    자식 위젯에는 타이틀바가 없어 최소화/최대화 힌트가 아무 일도 하지 않고, F11 은
    '전체화면'을 약속하면서 실제로는 아무 변화도 만들지 못한다(실측: 자식에
    `showFullScreen()` 을 걸면 `isFullScreen()` 만 True 가 되고 기하는 그대로다).
    지키지 못할 약속을 하는 단축키는 없는 것이 낫다.

    전체화면은 **메인 창**이 담당한다 — 뷰어가 창 안 시트가 된 뒤로는 그것이 '사진을
    화면 가득 보는' 유일한 경로이므로, 사라지지 않았는지 함께 고정한다."""
    import io
    import tokenize
    ui_dir = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui")
    offenders: list[str] = []
    for path in sorted(ui_dir.rglob("*.py")):
        if path.name in ("window_controls.py", "main_window.py"):
            continue                       # 헬퍼 자신과 **메인 창**은 허용
        src = path.read_text(encoding="utf-8")
        try:
            code = " ".join(
                t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type not in (tokenize.COMMENT, tokenize.STRING))
        except Exception:
            code = src
        for pat in ("enable_window_controls", "add_fullscreen_shortcut",
                    "showMaximized", "showFullScreen"):
            if pat in code:
                offenders.append(f"{path.relative_to(ui_dir.parents[2])}: {pat}")
    assert not offenders, (
        "시트로 뜨는 위젯이 창 제어를 부른다 — 자식 위젯에는 효과가 없다: "
        + ", ".join(offenders))

    # 메인 창에는 F11 이 살아 있어야 한다(옛 뷰어별 F11 의 대체).
    from PyQt6.QtGui import QKeySequence, QShortcut
    from aoi_verification.app.ui import main_window as mw
    src = (ui_dir / "main_window.py").read_text(encoding="utf-8")
    assert "add_fullscreen_shortcut(self)" in src, \
        "메인 창의 F11 전체화면이 사라졌다 — 사진을 화면 가득 보는 경로가 없어진다"
    assert QKeySequence("F11") and QShortcut and mw is not None


def test_no_sheet_caps_itself_to_the_primary_screen(qapp):
    """★ 시트로 뜨는 위젯은 **주 모니터 크기로 자기 최대크기를 걸지 않는다.**

    실제로 났던 버그다: 좌우 비교 뷰어가 `setMaximumSize(availableGeometry().size())`
    를 '화면 초과 성장 차단' 이라며 걸어 뒀는데, 시트 배치(`_place`)가 창 크기 -8px
    를 주면 그 상한에 잘려 **창보다 작은 팝업**이 됐다.  다중 모니터에서 주 모니터가
    더 작거나, 큰 모니터에 창을 최대화하면 그대로 재현된다.

    화면 초과를 막는 일은 `_place` 가 이미 한다 — 배치 주체는 하나여야 한다."""
    import io
    import re
    import tokenize
    ui_dir = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui")
    offenders: list[str] = []
    for path in sorted(ui_dir.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        try:
            code = "\n".join(
                t.string if t.type != tokenize.STRING else '""'
                for t in tokenize.generate_tokens(io.StringIO(src).readline)
                if t.type != tokenize.COMMENT)
        except Exception:
            code = src
        # setMaximumSize(...) 인자에 화면 기하가 흘러드는 형태만 잡는다.
        for m in re.finditer(r"setMaximumSize\s*\(([^)]*)\)", code):
            arg = m.group(1)
            if "availableGeometry" in arg or re.search(r"\bg\s*\.\s*size\b", arg):
                offenders.append(f"{path.relative_to(ui_dir.parents[2])}: {m.group(0)}")
    assert not offenders, (
        "시트가 주 모니터 크기로 자기 최대크기를 걸었다 — 창이 그보다 크면 팝업이 "
        "창보다 작게 뜬다.  배치는 sheet_host._place 에 맡겨라: " + ", ".join(offenders))


def test_f11_still_reaches_the_window_while_a_sheet_is_open(qapp):
    """★ 시트가 열린 동안에도 F11 이 **메인 창**에 닿아야 한다.

    뷰어가 창 안 시트가 된 뒤로 '사진을 화면 가득 보는' 유일한 경로가 이것이다.  시트가
    열린 동안 키를 가로채는 필터가 이 경로를 막으면 검사 면적을 넓히는 수단이 사라진다.
    (시트는 메인 창의 자손이므로 `WidgetWithChildrenShortcut` 로 닿는다.)"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from aoi_verification.app.ui.widgets.window_controls import (
        add_fullscreen_shortcut)
    win = QWidget()
    win.resize(900, 700)
    h = sheet_host.SheetHost(win)
    win._sheets = h
    add_fullscreen_shortcut(win)           # 메인 창과 같은 배선
    win.show()
    for _ in range(8):
        qapp.processEvents()
    dlg = QDialog(win)
    got = {}

    def check():
        QTest.keyClick(dlg, Qt.Key.Key_F11)
        for _ in range(10):
            qapp.processEvents()
        got["full"] = win.isFullScreen()
        got["sheet_alive"] = h._stack != []
        dlg.accept()

    _later(40, check)
    sheet_host.run(dlg)
    try:
        assert got["full"] is True, "시트가 열린 동안 F11 이 창에 닿지 않았다"
        assert got["sheet_alive"] is True, "F11 이 시트를 닫아 버렸다"
    finally:
        win.showNormal()
        h.close_all()
        win.hide()
        qapp.processEvents()
        win.deleteLater()
        qapp.processEvents()


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


# ── 단축키(QShortcut)도 막힌다 ────────────────────────────────────────────
def test_shortcut_events_do_not_leak_behind_an_open_sheet(qapp, host):
    """시트가 열려 있으면 **뒤 화면의 단축키가 발화하지 않는다.**

    ★ 이건 실제로 결과를 틀어지게 했다.  ``QShortcut`` 은 ``KeyPress`` 나
      ``ShortcutOverride`` 를 잡아먹어도 **그대로 발화한다** — 셋 중 ``Shortcut`` 만
      실제로 막는다.  게다가 ``Shortcut`` 이벤트의 수신자는 위젯이 아니라 **QShortcut
      객체**라, 예전 필터의 ``isinstance(obj, QWidget)`` 조건이 그것만 쏙 빠뜨렸다.

      그래서 Stage 1 에서 [선택 모드] 를 열어 둔 채 방향키를 누르면 — 대화상자는
      사진 격자라 방향키가 자연스러운 조작이다 — **대화상자 뒤에서 사진이 하나씩
      '검증'/'제외' 로 결정됐다**(실측: 남은 4장 → 1장).  사용자는 뒤 화면이 바뀌는
      것을 볼 수 없으므로 틀어진 줄도 모른다.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtTest import QTest
    win, h = host

    # ★ 단축키는 **페이지**에 단다 — 앱과 같은 배선이다(`select_page` 는
    #   `QShortcut(..., self)`).  창에 직접 달면 '창 자신의 단축키'(F11) 예외에
    #   걸려 이 테스트가 엉뚱한 이유로 통과/실패한다.
    page = QWidget(win)
    page.setGeometry(0, 0, 900, 700)
    page.show()
    qapp.processEvents()
    fired: list[str] = []
    QShortcut(QKeySequence("Right"), page, activated=lambda: fired.append("→"))
    QShortcut(QKeySequence("Z"), page, activated=lambda: fired.append("Z"))

    # 대조군 — 시트가 없으면 단축키는 정상 동작해야 한다(안 그러면 이 테스트는
    # '배선이 안 된' 상태에서도 통과한다).
    QTest.keyClick(win, Qt.Key.Key_Right)
    qapp.processEvents()
    assert fired == ["→"], "시트가 없는데도 단축키가 죽어 있다"

    dlg = QDialog(win)
    dlg.setWindowTitle("선택 모드")
    seen: dict = {}

    def inside():
        fired.clear()
        QTest.keyClick(win, Qt.Key.Key_Right)
        QTest.keyClick(win, Qt.Key.Key_Z)
        qapp.processEvents()
        seen["fired"] = list(fired)
        dlg.accept()

    _later(30, inside)
    sheet_host.run(dlg, full_bleed=True)
    assert seen["fired"] == [], "시트 뒤로 단축키가 샜다"

    # 닫힌 뒤에는 다시 살아난다 — 잠금이 새어 나가면 앱이 키를 영영 못 받는다.
    fired.clear()
    QTest.keyClick(win, Qt.Key.Key_Right)
    qapp.processEvents()
    assert fired == ["→"], "시트를 닫았는데 단축키가 죽은 채 남았다"


def test_the_open_sheet_keeps_its_own_shortcuts(qapp, host):
    """막는 것은 **뒤 화면**이지 시트 자신이 아니다.

    좌우 비교 뷰어는 ``Esc``·``←``·``→`` 로 조작한다 — 함께 막아 버리면 그 화면이
    통째로 못 쓰게 된다.  ``Shortcut`` 이벤트의 소유 위젯을 ``parent()`` 로 풀어
    시트 안쪽인지 판정하는 이유가 이것이다."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtTest import QTest
    win, h = host

    dlg = QDialog(win)
    dlg.setWindowTitle("좌우 비교")
    mine: list[str] = []
    QShortcut(QKeySequence(Qt.Key.Key_Left), dlg, activated=lambda: mine.append("←"))
    seen: dict = {}

    def inside():
        QTest.keyClick(win, Qt.Key.Key_Left)
        qapp.processEvents()
        seen["mine"] = list(mine)
        dlg.accept()

    _later(30, inside)
    sheet_host.run(dlg, full_bleed=True)
    assert seen["mine"] == ["←"], "시트 자신의 단축키까지 막혔다"
