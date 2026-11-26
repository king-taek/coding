"""[폴더 선택…] 은 UI 스레드를 세우지 않는다 — `ui/folder_picker.py` 회귀 가드.

배경: '폴더를 고르면 몇 초 멈춘다' 를 네 번 고쳤는데도 남았고, 텍스트로 치면 안 멈추고
**폴더 버튼으로 고르면** 멈춘다는 신고가 결정적이었다.  두 경로는 ``setText`` 이후가
같다 — 차이는 ``QFileDialog.getExistingDirectory`` 가 ``exec()`` 라 Windows 에서
Explorer 창을 UI 스레드 안에서 돌린다는 것(qtbase ``qwindowsdialoghelpers.cpp``:
``exec()`` 는 호출 스레드에서 ``Show``, ``show()`` 는 ``QWindowsDialogThread``).
그래서 ``open()`` 으로 바꿨다.  여기서 못 박는 것:
1. ``open()`` 을 부르면 **곧바로 돌아온다**(막는 모달 루프가 아니다) — 창은 열린 채.
2. 고르면 ``picked`` 가, 닫히면 ``closed`` 가 온다(취소도).
3. 열려 있는데 다시 눌러도 창을 하나 더 만들지 않는다.
4. 앱 소스 어디에도 정적 ``QFileDialog.get*``(= exec 고르기 창)가 되돌아오지 않고,
   ``QFileDialog`` 를 직접 만드는 곳은 이 모듈뿐이다(2026-11-24 — 사진 열기 · 엑셀
   저장 · GL 열기/비교/저장까지 옮겼다: 현택님이 '탐색기를 닫자 앱이 멈춤' 을 또 겪음).
5. 파일 열기(:class:`FilePicker`) · 저장(:class:`SavePicker`)도 같은 규칙 — 곧바로
   돌아오고, 고르면 ``picked``, 닫히면(취소 포함) ``closed``.
6. 취소하면 아무것도 남지 않는다(모달 · 비활성 창 · 시트 · 로딩 덮개).
7. 용도마다 마지막에 고른 폴더를 기억해 다음 창이 거기서 열린다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                      # noqa: E402
from PyQt6.QtWidgets import QDialog, QFileDialog, QWidget        # noqa: E402

from aoi_verification.app.ui import folder_picker as fp          # noqa: E402
from aoi_verification.app.ui.folder_picker import (              # noqa: E402
    FilePicker, FolderPicker, SavePicker)

_APP = Path(__file__).resolve().parents[2] / "aoi_verification" / "app"
_UI = _APP / "ui"


@pytest.fixture
def host(qapp):
    w = QWidget()
    yield w
    w.deleteLater()


def test_open_returns_immediately_and_dialog_is_window_modal(host, qapp):
    picker = FolderPicker(host)
    picker.open("폴더")                          # exec() 였다면 여기서 멈춘다
    qapp.processEvents()
    assert picker.is_open
    dlg = picker._dialog
    assert isinstance(dlg, QFileDialog)
    # `QDialog.open()` 의 표식 — Windows 플러그인은 이 모달 show() 를 보조 스레드로 돌린다.
    assert dlg.windowModality() == Qt.WindowModality.WindowModal
    assert dlg.fileMode() == QFileDialog.FileMode.Directory
    assert dlg.testOption(QFileDialog.Option.ShowDirsOnly)
    dlg.reject()


def test_picked_and_closed_signals(host, qapp, tmp_path):
    picker = FolderPicker(host)
    got: list = []
    closed: list = []
    picker.picked.connect(got.append)
    picker.closed.connect(lambda chosen, s, nav: closed.append((chosen, s, nav)))

    picker.open("폴더")
    # 사용자가 폴더를 고른 것 — 네이티브 창이 하는 일을 흉내낸다(`accept()` 는
    # 비네이티브 창이 현재 폴더로 `fileSelected` 를 한 번 더 내서 쓰지 않는다).
    picker._dialog.directoryEntered.emit(str(tmp_path))   # 창 안에서 폴더를 누름
    picker._dialog.fileSelected.emit(str(tmp_path))
    picker._dialog.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    assert got == [str(tmp_path)]
    assert closed and closed[0][0] is True and closed[0][1] >= 0.0
    assert 0.0 <= closed[0][2] <= closed[0][1]      # 마지막 클릭 뒤 꼬리 ≤ 열린 시간

    picker.open("폴더")                               # 취소 — picked 없이 closed 만
    picker._dialog.reject()
    qapp.processEvents()
    assert got == [str(tmp_path)]
    assert len(closed) == 2 and closed[1][0] is False
    assert closed[1][2] == -1.0                       # 폴더를 누른 적 없음 → 모름


def test_reopen_while_open_reuses_the_same_dialog(host, qapp):
    picker = FolderPicker(host)
    picker.open("폴더")
    first = picker._dialog
    picker.open("폴더")
    assert picker._dialog is first and picker.is_open
    first.reject()


def test_no_blocking_static_file_dialog_in_app_sources():
    """정적 ``QFileDialog.get*``(= exec) 로 되돌리지 않는다 — 폴더 · 사진 열기 · 저장 전부."""
    pat = re.compile(r"\bget(ExistingDirectory|OpenFileNames?|SaveFileName|"
                     r"ExistingDirectoryUrl|OpenFileUrls?|SaveFileUrl)\s*\(")
    hits = [str(p.relative_to(_APP)) for p in _APP.rglob("*.py")
            if pat.search(p.read_text(encoding="utf-8"))]
    assert hits == [], f"UI 스레드를 세우는 고르기 창이 돌아왔다: {hits}"


def test_file_dialogs_are_built_only_by_the_picker_module():
    """``QFileDialog(`` 를 직접 만드는 곳은 `ui/folder_picker.py` 하나다 — 로그·기억·돌려쓰기를 함께 받게."""
    pat = re.compile(r"\bQFileDialog\s*\(")
    hits = [str(p.relative_to(_APP)) for p in _APP.rglob("*.py")
            if p.name != "folder_picker.py" and pat.search(p.read_text(encoding="utf-8"))]
    assert hits == [], f"고르기 창을 직접 만든다 — folder_picker 를 써라: {hits}"


def test_picker_module_never_calls_exec():
    src = (_UI / "folder_picker.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert not re.search(r"\.exec\s*\(", code)


def test_timeline_records_clicks_in_order(host, qapp, tmp_path):
    """창 안에서 일어난 일이 순서대로 한 줄에 남는다 — 어느 폴더에서 시간이 갔는지."""
    picker = FolderPicker(host)
    picker.open("폴더")
    picker._dialog.directoryEntered.emit(str(tmp_path))
    picker._dialog.currentChanged.emit(str(tmp_path / "A"))
    picker._dialog.fileSelected.emit(str(tmp_path / "A"))
    picker._dialog.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    line = picker.timeline()
    kinds = [tok.split()[1] for tok in line.split(" | ")]
    assert kinds == ["open-returned", "enter-dir", "click", "selected", "finished"], line
    assert "/A" in line


# ---------------------------------------------------------------------------
# 파일 열기 · 저장 (2026-11-24)
def test_file_picker_single_and_multi(host, qapp, tmp_path):
    picker = FilePicker(host)
    got: list = []
    closed: list = []
    picker.picked.connect(got.append)
    picker.closed.connect(lambda chosen, s, nav: closed.append(chosen))

    picker.open("사진", str(tmp_path), "Images (*.png)")          # exec() 였다면 여기서 멈춘다
    qapp.processEvents()
    dlg = picker._dialog
    assert picker.is_open and dlg.windowModality() == Qt.WindowModality.WindowModal
    assert dlg.fileMode() == QFileDialog.FileMode.ExistingFile
    assert dlg.acceptMode() == QFileDialog.AcceptMode.AcceptOpen
    a = tmp_path / "a.png"
    dlg.filesSelected.emit([str(a)])
    dlg.fileSelected.emit(str(a))                    # 둘 다 와도 picked 는 한 번
    dlg.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    assert got == [[str(a)]] and closed == [True]

    picker.open("사진", str(tmp_path), "Images (*.png)", multi=True)
    assert picker._dialog is dlg                     # 돌려쓴다
    assert dlg.fileMode() == QFileDialog.FileMode.ExistingFiles
    dlg.filesSelected.emit([str(a), str(tmp_path / "b.png")])
    dlg.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    assert got[-1] == [str(a), str(tmp_path / "b.png")]

    picker.open("사진")                               # 취소 — picked 없이 closed 만
    dlg.reject()
    qapp.processEvents()
    assert len(got) == 2 and closed[-1] is False


def test_file_picker_reopen_while_open_reuses(host, qapp):
    picker = FilePicker(host)
    picker.open("사진")
    first = picker._dialog
    picker.open("사진")
    assert picker._dialog is first and picker.is_open
    first.reject()


def test_save_picker_signals_and_start_name(host, qapp, tmp_path):
    picker = SavePicker(host, purpose="t_save")
    got: list = []
    picker.picked.connect(got.append)
    picker.open("저장", str(tmp_path / "out.xlsx"), "Excel (*.xlsx)", "xlsx")
    dlg = picker._dialog
    assert dlg.acceptMode() == QFileDialog.AcceptMode.AcceptSave
    assert dlg.defaultSuffix() == "xlsx"
    assert dlg.directory().absolutePath() == str(tmp_path)
    dlg.fileSelected.emit(str(tmp_path / "out.xlsx"))
    dlg.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    assert got == [str(tmp_path / "out.xlsx")]

    # 이름만 주면 마지막으로 저장한 폴더에 붙는다
    other = tmp_path / "elsewhere"
    other.mkdir()
    picker.open("저장", "next.xlsx", "Excel (*.xlsx)", "xlsx")
    assert dlg.directory().absolutePath() == str(tmp_path)
    dlg.reject()
    qapp.processEvents()
    assert got == [str(tmp_path / "out.xlsx")]


def test_last_folder_is_remembered_per_purpose(host, qapp, tmp_path):
    """고른 곳의 폴더를 용도마다 기억하고, 시작 위치를 안 주면 다음 창이 거기서 열린다."""
    lot = tmp_path / "NAS" / "LOT1"
    lot.mkdir(parents=True)
    other = tmp_path / "x"
    other.mkdir()
    a = FolderPicker(host, purpose="t_ref")
    a.open("폴더")
    a._dialog.fileSelected.emit(str(lot))
    a._dialog.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    assert fp.last_dir("t_ref") == str(lot.parent)         # 폴더는 그 상위 — 다음 LOT 이 옆에 있다
    assert fp.last_dir("t_val") == ""                       # 다른 용도는 섞이지 않는다

    a.open("폴더")
    assert a._dialog.directory().absolutePath() == str(lot.parent)
    a._dialog.reject()
    a.open("폴더", str(other))                              # 준 시작 위치가 이긴다
    assert a._dialog.directory().absolutePath() == str(other)
    a._dialog.reject()
    qapp.processEvents()

    f = FilePicker(host, purpose="t_img")
    f.open("사진")
    f._dialog.filesSelected.emit([str(other / "p.png")])
    f._dialog.done(QDialog.DialogCode.Accepted)
    qapp.processEvents()
    assert fp.last_dir("t_img") == str(other)
    # 취소는 기억을 바꾸지 않는다
    f.open("사진", str(lot))
    f._dialog.reject()
    qapp.processEvents()
    assert fp.last_dir("t_img") == str(other)


def test_cancel_logs_open_and_close(host, qapp, caplog):
    """다음 app.log 가 어느 창에서 멈췄는지 말해 주도록 — 열기 · 닫힘(취소) · 입력 상태."""
    import logging
    picker = FilePicker(host, purpose="t_log")
    with caplog.at_level(logging.INFO, logger=fp.__name__):
        picker.open("사진")
        picker._dialog.reject()
        qapp.processEvents()
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "파일 창[t_log] 열기" in msg, msg
    assert "파일 창[t_log] 닫힘(취소)" in msg, msg
    assert "창 안 순서: +" in msg and "finished" in msg, msg
    assert "주 창 입력" in msg, msg


@pytest.mark.parametrize("make", [
    lambda h: (FolderPicker(h), lambda p: p.open("폴더")),
    lambda h: (FilePicker(h), lambda p: p.open("사진", multi=True)),
    lambda h: (SavePicker(h), lambda p: p.open("저장", "a.xlsx", "Excel (*.xlsx)", "xlsx")),
])
def test_cancel_inside_a_sheet_leaves_nothing_blocking(qapp, make):
    """시트(앱 안 창) 안에서 연 고르기 창을 취소해도 입력을 막는 것이 남지 않는다.

    '취소했더니 버튼이 안 눌린다' 의 두 가설 중 '입력을 막는 잔재' 쪽을 못 박는다 —
    보이지 않는 모달 · 비활성 창 · 닫히지 않은 시트 · 켜진 채 남은 전역 키 필터."""
    from PyQt6.QtCore import QTimer
    from aoi_verification.app.ui.widgets import sheet_host

    win = QWidget()
    win.resize(800, 600)
    h = sheet_host.SheetHost(win)
    win._sheets = h
    win.show()
    qapp.processEvents()
    page = QDialog(win)
    page.setWindowTitle("시트")
    seen: dict = {}
    picker, opener = make(page)
    picker._dialog = None
    for_dialog = []

    def step_open():
        opener(picker)
        for_dialog.append(picker._dialog)
        picker._dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        seen["modal_while_open"] = qapp.activeModalWidget() is picker._dialog
        QTimer.singleShot(50, step_cancel)

    def step_cancel():
        picker._dialog.reject()
        QTimer.singleShot(50, step_check)

    def step_check():
        seen["modal"] = qapp.activeModalWidget()
        seen["enabled"] = win.isEnabled() and page.isEnabled()
        seen["stack"] = len(h._stack)
        page.reject()

    QTimer.singleShot(30, step_open)
    sheet_host.run(page)
    qapp.processEvents()
    assert seen["modal_while_open"]
    assert seen["modal"] is None, f"취소 뒤 모달이 남았다: {seen['modal']}"
    assert seen["enabled"] and seen["stack"] == 1
    assert h._stack == [] and not h._app_filter_on        # 시트도 깨끗이 닫힌다
    assert not for_dialog[0].isVisible()
    win.hide()
    win.deleteLater()
