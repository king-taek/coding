"""OS 파일 브라우저(폴더 고르기 · 파일 열기 · 파일 저장)를 **UI 스레드를 세우지 않고** 연다.

왜 필요한가.  '폴더를 고르면 몇 초 멈춘다' 는 신고를 네 번 고쳤다(die 안내 스캔·폴더
확인을 워커로, 표본을 슬롯 하나로, 무거운 import 를 로딩창에서 끝내기).  그래도 남았고,
텍스트로 경로를 치면 멈추지 않는데 **[폴더 선택…] 으로 고르면** 멈춘다는 것이 결정적
단서였다 — 두 경로는 ``setText`` 이후가 완전히 같다.  차이는 파일 브라우저 자체다.

``QFileDialog.getExistingDirectory`` · ``getOpenFileName`` · ``getSaveFileName`` 은
``exec()`` 다.  Windows 플러그인(qtbase ``qwindowsdialoghelpers.cpp``)에서 ``exec()`` 는
**호출한 스레드(UI)** 에서 ``IFileDialog::Show`` 를 돌린다 — Qt 문서도 "On Windows the
dialog spins a blocking modal event loop that does not dispatch any QTimers" 라고 적는다.
그래서 창이 열려 있는 동안 Explorer 가 **우리 프로세스의 UI 스레드 안에서** 하는 일(셸
확장 DLL 로딩, 네트워크 폴더 열거, 아이콘·속성 조회, 닫을 때의 COM 정리와 네트워크 핸들
반납)이 전부 UI 스레드 몫이 되고, 그 사이 멈춘 타이머·큐에 쌓인 워커 결과가 **닫는 순간
한꺼번에** 터진다.  취소해도 멈추는 신고가 그 증거였다(취소 경로에는 우리 코드가 없다).

같은 플러그인에서 ``show()`` 는 다르다 — 모달이면 idle 타이머로
``QWindowsDialogThread`` 를 띄워 **네이티브 창을 별도 스레드에서** 돌리고, UI 이벤트
루프는 그대로 돈다.  ``QDialog.open()`` 이 그 경로다.  이 모듈은 ``open()`` +
``fileSelected``/``filesSelected`` 시그널로 고르기를 **비동기**로 바꾼다 — 그동안
디바운스·워커·로딩바가 평소처럼 돈다.  보이는 창은 전과 같은 OS 파일 브라우저다.

세 가지:
- :class:`FolderPicker` — 폴더 하나.  ``picked(str)``.
- :class:`FilePicker` — 열 파일 하나 또는 여러 개.  ``picked(list)``(절대 경로).
- :class:`SavePicker` — 저장할 파일 이름.  ``picked(str)``.
셋 다 ``closed(chosen, open_seconds, since_nav)`` 를 닫힐 때마다(취소 포함) 낸다.

**용도(purpose)** 를 주면 그 용도로 마지막에 고른 곳의 **폴더**를 기억했다가(``ui_prefs``
의 ``last_dirs``) 다음에 시작 위치를 따로 주지 않았을 때 거기서 연다 — 폴더를 고를 때도
고른 폴더의 **상위**를 기억한다(다음 LOT 은 보통 그 옆에 있다).  존재 확인은 하지 않는다:
끊긴 네트워크 경로의 ``is_dir()`` 은 UI 스레드에서 수십 초 걸릴 수 있다.

진단(`app.log`).  모든 고르기 창이 열 때·닫을 때(고름/취소) 한 줄씩 남기고, 열려 있는
동안과 닫힌 뒤 6초 동안 UI 정지를 잰다(`stall_watch`).  닫힌 뒤에는 주 창이 입력을 받을
수 있게 되기까지를 0.25초마다 본다 — '버튼이 안 눌린다' 가 UI 스레드 정지인지, 입력을
막는 무언가(모달·비활성 창)인지 가른다.  '열기' 줄만 있고 '닫힘' 줄이 없으면 Qt 가 창이
닫힌 것을 끝내 듣지 못한 것이다(보이지 않는 모달이 남는다).

불변식:
- ``QFileDialog.get*``/``exec()`` 로 되돌리지 마라 — 회귀 가드
  ``dev/tests/test_folder_picker.py`` 가 소스를 훑는다.
- 다이얼로그 객체는 **고르기 객체당 하나를 돌려쓴다**.  ``IFileDialog::Show`` 는 한 번만
  되지만 Qt 가 다음 ``show()`` 에서 네이티브 창을 새로 만든다(``ensureNativeDialog``).
  끝날 때마다 지우면 헬퍼 소멸자가 보조 스레드를 최대 0.5초 기다린다 — 그 대기가 곧
  UI 정지라 피한다.  수명은 부모가 쥔다.
- 열려 있는데 다시 누르면 새로 열지 않고 그 창을 앞으로 가져온다.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget

from ..utils import prefs as _prefs
from .deferred import call_later

__all__ = ["FolderPicker", "FilePicker", "SavePicker", "last_dir"]

_LOG = logging.getLogger(__name__)

# 열려 있는 동안의 정지 감시 상한 — 사용자가 폴더를 찾는 시간은 길 수 있다(실측 최대
# 99초).  닫히면 6초짜리로 다시 건다.
_OPEN_WATCH_S = 600.0
_PROBE_EVERY_MS = 250
_PROBE_MAX = 40                     # 10초


def last_dir(purpose: str) -> str:
    """``purpose`` 로 마지막에 고른 곳의 폴더 — 없으면 빈 문자열."""
    if not purpose:
        return ""
    got = _prefs.load().last_dirs.get(purpose, "")
    return got if isinstance(got, str) else ""


def _remember(purpose: str, folder: str) -> None:
    if not purpose or not folder:
        return
    cur = _prefs.load().last_dirs
    if cur.get(purpose) == folder:
        return
    cur = dict(cur)
    cur[purpose] = folder
    _prefs.patch(last_dirs=cur)


class _Picker(QObject):
    """세 고르기의 공통 — 창 돌려쓰기 · 창 안 순서 기록 · 로그 · 정지 감시.

    ``closed(chosen, open_seconds, since_nav)`` 의 ``since_nav`` 는 **사용자가 창
    안에서 마지막으로 폴더를 누른 뒤 창이 닫히기까지** 걸린 초다(안 눌렀으면 -1) —
    '열려 있던 시간' 에는 사용자가 폴더를 찾은 시간이 섞여 있어, 멈춤이 창 **안쪽**
    (Windows 가 닫는 동안)에 있는지 보려면 이 꼬리 구간을 따로 재야 한다."""

    closed = pyqtSignal(bool, float, float)

    def __init__(self, parent: QWidget, *, purpose: str = "",
                 on_stall: Optional[Callable[[float, str], None]] = None,
                 on_stack: Optional[Callable[[float, str], None]] = None) -> None:
        super().__init__(parent)
        #: 마지막 위치를 기억하는 키이자 로그의 이름표.  열기 전에 바꿔도 된다
        #: (설정 화면은 기준/검증 칸마다 바꾼다).
        self.purpose = purpose
        # 정지 로그를 호출부가 직접 쓰고 싶을 때(그 화면이 돌리는 워커를 덧붙이려고).
        self._on_stall = on_stall
        self._on_stack = on_stack
        self._dialog: Optional[QFileDialog] = None
        self._opened_at = 0.0
        self._last_nav: Optional[float] = None
        self._chosen = False
        self._closed_once = True
        # 창 안에서 일어난 일의 순서 — (열린 뒤 초, 종류, 경로 꼬리).
        self._events: list[tuple[float, str, str]] = []

    # -- 하위 클래스가 정한다 -----------------------------------------
    def _setup(self, dlg: QFileDialog) -> None:
        raise NotImplementedError

    # -- 공통 --------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._dialog is not None and self._dialog.isVisible()

    def _tag(self) -> str:
        return self.purpose or type(self).__name__

    def _show(self, caption: str, start: str,
              prepare: Optional[Callable[[QFileDialog], None]] = None) -> bool:
        """창을 띄운다.  이미 열려 있으면 앞으로만 가져오고 False."""
        if self.is_open:
            self._dialog.raise_()
            self._dialog.activateWindow()
            _LOG.info("파일 창[%s]: 이미 열려 있어 앞으로 가져왔다", self._tag())
            return False
        dlg = self._dialog
        if dlg is None:
            dlg = QFileDialog(self.parent(), caption)
            self._setup(dlg)
            dlg.fileSelected.connect(self._on_file_selected)
            dlg.filesSelected.connect(self._on_files_selected)
            dlg.directoryEntered.connect(self._on_dir)
            dlg.currentChanged.connect(self._on_current)
            dlg.finished.connect(self._on_finished)
            self._dialog = dlg
        else:
            dlg.setWindowTitle(caption)
        self._chosen = False
        self._closed_once = False
        self._last_nav = None
        self._events = []
        self._opened_at = time.perf_counter()
        if prepare is not None:
            prepare(dlg)
        if start:
            # 네이티브 창은 여기서 셸 항목을 만든다(UI 스레드) — 끊긴 네트워크 경로면
            # 느릴 수 있어 걸린 시간을 순서에 남긴다.
            if self._start_is_dir():
                dlg.setDirectory(start)
            else:                        # '폴더/기본 이름' — 정적 get* 가 하던 대로 나눈다
                folder, name = os.path.split(start)
                if folder:
                    dlg.setDirectory(folder)
                dlg.selectFile(name)
            self._note("start", start)
        _LOG.info("파일 창[%s] 열기 — 시작 위치 %s", self._tag(), start or "(지정 없음)")
        self._watch(_OPEN_WATCH_S)
        dlg.open()                       # exec() 가 아니다 — 모듈 docstring
        self._note("open-returned", "")
        return True

    def _start_is_dir(self) -> bool:
        return True

    def _resolve_start(self, start: str) -> str:
        """시작 위치를 안 주면 그 용도로 마지막에 고른 폴더."""
        return start or last_dir(self.purpose)

    def _note(self, kind: str, path: str) -> None:
        self._events.append((time.perf_counter() - self._opened_at, kind, path))

    def timeline(self) -> str:
        """마지막 창에서 일어난 일을 한 줄로 — ``+1.23s 종류 …경로꼬리`` 의 나열."""
        return " | ".join(
            f"+{t:.2f}s {kind}" + (f" …{path[-40:]}" if path else "")
            for t, kind, path in self._events)

    def _on_file_selected(self, path: str) -> None:
        self._note("selected", path)

    def _on_files_selected(self, paths: list) -> None:
        pass

    def _picked_folder(self, path: str) -> None:
        """고른 곳의 폴더를 기억한다 — 다음에 그 용도의 창이 거기서 열린다."""
        try:
            _remember(self.purpose, os.path.dirname(os.path.normpath(path)))
        except Exception:
            _LOG.warning("파일 창[%s]: 마지막 위치를 저장하지 못했다", self._tag(),
                         exc_info=True)

    def _on_dir(self, path: str) -> None:
        self._last_nav = time.perf_counter()
        self._note("enter-dir", path)

    def _on_current(self, path: str) -> None:
        self._last_nav = time.perf_counter()
        self._note("click", path)

    def _on_finished(self, result: int) -> None:
        if self._closed_once:
            return
        self._closed_once = True
        now = time.perf_counter()
        self._note("finished", str(result))
        open_s = now - self._opened_at
        since_nav = -1.0 if self._last_nav is None else now - self._last_nav
        _LOG.info("파일 창[%s] 닫힘(%s) — %.2f초 열려 있었다 · 마지막 클릭 뒤 닫히기까지 %s"
                  " / 창 안 순서: %s", self._tag(), "고름" if self._chosen else "취소",
                  open_s, "모름" if since_nav < 0 else f"{since_nav:.2f}초",
                  self.timeline() or "없음")
        self._watch(6.0)
        self._probe_input(now, 0)
        self.closed.emit(self._chosen, open_s, since_nav)

    # -- 진단 --------------------------------------------------------
    def _watch(self, seconds: float) -> None:
        from . import stall_watch
        stall_watch.watch(self, self._on_stall or self._log_stall, seconds=seconds,
                          on_stack=self._on_stack or self._log_stack)

    def _log_stall(self, seconds: float, jobs: str) -> None:
        _LOG.warning("파일 창[%s]%s UI 가 %.2f초 멈췄다 — 그때 돌던 작업: %s",
                     self._tag(), "이 열려 있는 동안" if not self._closed_once
                     else "을 닫은 직후", seconds, jobs or "없음")

    def _log_stack(self, stale_s: float, stack: str) -> None:
        _LOG.warning("UI 스레드가 %.1f초째 멈춰 있다(파일 창[%s] %s) — 그 순간 하던 일:\n%s",
                     stale_s, self._tag(), "닫힘" if self._closed_once else "열림",
                     stack or "(스택을 못 떴다)")

    def _probe_input(self, closed_at: float, n: int) -> None:
        """창이 닫힌 뒤 주 창이 입력을 받을 수 있게 되기까지 — 0.25초마다 상태를 남긴다.

        UI 정지 로그 없이 화면이 입력을 못 받는 구간이 있다면 여기서 보인다(모달 위젯이
        남아 있거나, 주 창이 비활성이거나).  상태가 '정상' 이 되면 멈춘다."""
        parent = self.parent()
        if not isinstance(parent, QWidget):
            return
        win = parent.window()
        modal = QApplication.activeModalWidget()
        active = QApplication.activeWindow()
        ok = win.isEnabled() and modal is None and active is win
        if ok or n >= _PROBE_MAX:
            _LOG.info("파일 창[%s] 닫힌 뒤 %.2f초: 주 창 입력 %s", self._tag(),
                      time.perf_counter() - closed_at,
                      "가능" if ok else "여전히 불가(10초 감시 종료)")
            return
        if n == 0 or n % 4 == 0:
            _LOG.info("파일 창[%s] 닫힌 뒤 %.2f초: 주 창 입력 불가 — enabled=%s 모달=%s 활성창=%s",
                      self._tag(), time.perf_counter() - closed_at, win.isEnabled(),
                      type(modal).__name__ if modal else None,
                      type(active).__name__ if active else None)
        call_later(self, _PROBE_EVERY_MS, lambda: self._probe_input(closed_at, n + 1))


class FolderPicker(_Picker):
    """폴더 하나를 고르는 비동기 OS 브라우저.  ``picked(path)`` 는 고른 뒤에만 온다."""

    picked = pyqtSignal(str)

    def open(self, caption: str, start: str = "") -> None:
        """브라우저를 열고 **곧바로 돌아온다**.  결과는 시그널로."""
        self._show(caption, self._resolve_start(start))

    def _setup(self, dlg: QFileDialog) -> None:
        # `getExistingDirectory` 와 같은 모양: 폴더만, 열기.
        dlg.setFileMode(QFileDialog.FileMode.Directory)
        dlg.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)

    def _on_file_selected(self, path: str) -> None:
        super()._on_file_selected(path)
        if not path:
            return
        self._chosen = True
        self._picked_folder(path)
        self.picked.emit(path)


class FilePicker(_Picker):
    """열 파일을 고르는 비동기 OS 브라우저 — ``getOpenFileName`` · ``getOpenFileNames`` 대신.

    ``picked(list)`` 는 고른 뒤에만, 절대 경로 목록으로 온다(하나만 고르는 창도 목록)."""

    picked = pyqtSignal(list)

    def open(self, caption: str, start: str = "", name_filter: str = "",
             multi: bool = False) -> None:
        """브라우저를 열고 **곧바로 돌아온다**.  ``start`` 는 폴더."""
        def prepare(dlg: QFileDialog) -> None:
            dlg.setFileMode(QFileDialog.FileMode.ExistingFiles if multi
                            else QFileDialog.FileMode.ExistingFile)
            dlg.setNameFilter(name_filter)
        self._show(caption, self._resolve_start(start), prepare)

    def _setup(self, dlg: QFileDialog) -> None:
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)

    def _on_files_selected(self, paths: list) -> None:
        got = [os.path.abspath(p) for p in paths if p]
        self._note("selected", got[0] if got else "")
        if not got:
            return
        self._chosen = True
        self._picked_folder(got[0])
        self.picked.emit(got)

    def _on_file_selected(self, path: str) -> None:
        pass                            # `filesSelected` 하나로 받는다(하나만 골라도 온다)


class SavePicker(_Picker):
    """저장할 파일 이름을 받는 비동기 OS 브라우저 — ``getSaveFileName`` 대신.

    ``start`` 가 폴더 없는 파일 이름이면 그 용도로 마지막에 저장한 폴더에 붙인다.
    덮어쓰기 확인은 OS 창이 한다(``getSaveFileName`` 과 같다)."""

    picked = pyqtSignal(str)

    def open(self, caption: str, start: str = "", name_filter: str = "",
             default_suffix: str = "") -> None:
        def prepare(dlg: QFileDialog) -> None:
            dlg.setNameFilter(name_filter)
            dlg.setDefaultSuffix(default_suffix)
        if start and not os.path.dirname(start):
            base = last_dir(self.purpose)
            start = os.path.join(base, start) if base else start
        self._show(caption, start or last_dir(self.purpose), prepare)

    def _start_is_dir(self) -> bool:
        return False                    # 시작 값은 '폴더/기본 이름'

    def _setup(self, dlg: QFileDialog) -> None:
        dlg.setFileMode(QFileDialog.FileMode.AnyFile)
        dlg.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)

    def _on_file_selected(self, path: str) -> None:
        super()._on_file_selected(path)
        if not path:
            return
        self._chosen = True
        self._picked_folder(path)
        self.picked.emit(path)
