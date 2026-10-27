"""OS 폴더 브라우저를 **UI 스레드를 세우지 않고** 연다.

왜 필요한가.  '폴더를 고르면 몇 초 멈춘다' 는 신고를 네 번 고쳤다(die 안내 스캔·폴더
확인을 워커로, 표본을 슬롯 하나로, 무거운 import 를 로딩창에서 끝내기).  그래도 남았고,
텍스트로 경로를 치면 멈추지 않는데 **[폴더 선택…] 으로 고르면** 멈춘다는 것이 결정적
단서였다 — 두 경로는 ``setText`` 이후가 완전히 같다.  차이는 파일 브라우저 자체다.

``QFileDialog.getExistingDirectory`` 는 ``exec()`` 다.  Windows 플러그인
(qtbase ``qwindowsdialoghelpers.cpp``)에서 ``exec()`` 는 **호출한 스레드(UI)** 에서
``IFileDialog::Show`` 를 돌린다 — Qt 문서도 "On Windows the dialog spins a blocking
modal event loop that does not dispatch any QTimers" 라고 적는다.  그래서 창이 열려
있는 동안 Explorer 가 **우리 프로세스의 UI 스레드 안에서** 하는 일(셸 확장 DLL 로딩,
네트워크 폴더 열거, 아이콘·속성 조회, 닫을 때의 COM 정리와 네트워크 핸들 반납)이 전부
UI 스레드 몫이 되고, 그 사이 멈춘 타이머·큐에 쌓인 워커 결과가 **닫는 순간 한꺼번에**
터진다.  취소해도 멈추는 신고가 그 증거였다(취소 경로에는 우리 코드가 없다).

같은 플러그인에서 ``show()`` 는 다르다 — 모달이면 idle 타이머로
``QWindowsDialogThread`` 를 띄워 **네이티브 창을 별도 스레드에서** 돌리고, UI 이벤트
루프는 그대로 돈다.  ``QDialog.open()`` 이 그 경로다.  이 모듈은 ``open()`` +
``fileSelected`` 시그널로 폴더 선택을 **비동기**로 바꾼다 — 그동안 디바운스·워커·
로딩바가 평소처럼 돈다.  보이는 창은 전과 같은 OS 파일 브라우저다.

불변식:
- ``getExistingDirectory``/``exec()`` 로 되돌리지 마라 — 회귀 가드
  ``dev/tests/test_folder_picker.py`` 가 소스를 훑는다.
- 다이얼로그 객체는 **부모당 하나를 돌려쓴다**.  ``IFileDialog::Show`` 는 한 번만
  되지만 Qt 가 다음 ``show()`` 에서 네이티브 창을 새로 만든다(``ensureNativeDialog``).
  끝날 때마다 지우면 헬퍼 소멸자가 보조 스레드를 최대 0.5초 기다린다 — 그 대기가 곧
  UI 정지라 피한다.  수명은 부모가 쥔다.
- 열려 있는데 다시 누르면 새로 열지 않고 그 창을 앞으로 가져온다.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QWidget

__all__ = ["FolderPicker"]

_LOG = logging.getLogger(__name__)


class FolderPicker(QObject):
    """폴더 하나를 고르는 비동기 OS 브라우저.

    ``picked(path)`` 는 고른 뒤에만, ``closed(chosen, open_seconds, since_nav)`` 는
    닫힐 때마다(취소 포함) 온다.  ``since_nav`` 는 **사용자가 창 안에서 마지막으로
    폴더를 누른 뒤 창이 닫히기까지** 걸린 초다(안 눌렀으면 -1) — '열려 있던 시간' 에는
    사용자가 폴더를 찾은 시간이 섞여 있어, 멈춤이 창 **안쪽**(Windows 가 닫는 동안)에
    있는지 보려면 이 꼬리 구간을 따로 재야 한다(실측: 닫힌 뒤의 UI 정지는 0건이었는데
    신고는 남았다).  네이티브 창의 폴더 이동은 ``directoryEntered``/``currentChanged``
    로 들어온다."""

    picked = pyqtSignal(str)
    closed = pyqtSignal(bool, float, float)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._dialog: Optional[QFileDialog] = None
        self._opened_at = 0.0
        self._last_nav: Optional[float] = None
        self._chosen = False
        # 창 안에서 일어난 일의 순서 — (열린 뒤 초, 종류, 경로 꼬리).  닫힐 때 호출부가
        # `timeline()` 으로 한 줄에 남긴다: 어느 폴더를 눌렀을 때 시간이 갔는지.
        self._events: list[tuple[float, str, str]] = []

    @property
    def is_open(self) -> bool:
        return self._dialog is not None and self._dialog.isVisible()

    def open(self, caption: str, start: str = "") -> None:
        """브라우저를 열고 **곧바로 돌아온다**.  결과는 시그널로."""
        if self.is_open:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        dlg = self._ensure_dialog(caption)
        if start:
            dlg.setDirectory(start)
        self._chosen = False
        self._last_nav = None
        self._events = []
        self._opened_at = time.perf_counter()
        dlg.open()                       # exec() 가 아니다 — 모듈 docstring
        self._note("open-returned", "")

    def _ensure_dialog(self, caption: str) -> QFileDialog:
        dlg = self._dialog
        if dlg is None:
            dlg = QFileDialog(self.parent(), caption)
            # `getExistingDirectory` 와 같은 모양: 폴더만, 열기.
            dlg.setFileMode(QFileDialog.FileMode.Directory)
            dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
            dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
            dlg.fileSelected.connect(self._on_selected)
            dlg.directoryEntered.connect(self._on_dir)
            dlg.currentChanged.connect(self._on_current)
            dlg.finished.connect(self._on_finished)
            self._dialog = dlg
        else:
            dlg.setWindowTitle(caption)
        return dlg

    def _note(self, kind: str, path: str) -> None:
        self._events.append((time.perf_counter() - self._opened_at, kind, path))

    def timeline(self) -> str:
        """마지막 창에서 일어난 일을 한 줄로 — ``+1.23s 종류 …경로꼬리`` 의 나열."""
        return " | ".join(
            f"+{t:.2f}s {kind}" + (f" …{path[-40:]}" if path else "")
            for t, kind, path in self._events)

    def _on_selected(self, path: str) -> None:
        self._note("selected", path)
        if not path:
            return
        self._chosen = True
        self.picked.emit(path)

    def _on_dir(self, path: str) -> None:
        self._last_nav = time.perf_counter()
        self._note("enter-dir", path)

    def _on_current(self, path: str) -> None:
        self._last_nav = time.perf_counter()
        self._note("click", path)

    def _on_finished(self, result: int) -> None:
        now = time.perf_counter()
        self._note("finished", str(result))
        since_nav = -1.0 if self._last_nav is None else now - self._last_nav
        self.closed.emit(self._chosen, now - self._opened_at, since_nav)
