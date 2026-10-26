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
4. UI 소스 어디에도 정적 ``getExistingDirectory``(= exec 폴더 선택)가 되돌아오지
   않는다(설정 화면 · Wafer map).  사진 열기·엑셀 저장의 ``getOpenFileName`` ·
   ``getSaveFileName`` 은 아직 exec 다 — 신고가 오면 같은 방식으로 옮긴다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                      # noqa: E402
from PyQt6.QtWidgets import QDialog, QFileDialog, QWidget        # noqa: E402

from aoi_verification.app.ui.folder_picker import FolderPicker   # noqa: E402

_UI = Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"


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


def test_no_blocking_static_folder_dialog_in_ui_sources():
    """정적 `getExistingDirectory`(= exec) 로 되돌리지 않는다 — 설정 화면 · Wafer map."""
    pat = re.compile(r"QFileDialog\.getExistingDirectory\s*\(")
    hits = [str(p.relative_to(_UI)) for p in _UI.rglob("*.py")
            if pat.search(p.read_text(encoding="utf-8"))]
    assert hits == [], f"UI 스레드를 세우는 폴더 선택이 돌아왔다: {hits}"
