"""[좌표수집-임시] 좌표 자료 수집 버튼 — **수집·분석이 끝나면 지우는 코드**다.

개발자 모드(Ctrl+Shift+D)일 때만 상태바에 버튼 하나가 보인다.  누르면 ``dev/coord_lab.py`` 의
NAS 순회(``run_nas``)를 백그라운드에서 돌리고 로딩바로 진행을 보인다.  2026-11-14 부터는
**번호를 아직 못 정하는 Job 만 집중해서** 모은다(``--focus`` — SNU-EDGE · 번호표 없는 블록 제품,
목록은 ``coord_lab.FOCUS_JOBS``, 사용자 지시).  시프트 규칙은 장비 화면으로 확정돼 ``--shift-only`` 는 쓰지 않는다.  결과는
``바탕화면\\프로그램 개발중\\분석용 자료 수집\\AOI 레시피 검증\\YYYYMMDD\\집중수집.zip`` 으로 남는다(사용자 지시 2026-11-14,
20 MB 를 넘으면 ``집중수집_2.zip`` …).  읽기 전용이고 앱의 좌표 계산·매칭에는 손대지 않는다.

지우는 법(전부 ``[좌표수집-임시]`` 로 검색된다 — ``test_coord_collect_tmp.py`` 가 이 목록을 지킨다):

1. 이 파일 · ``dev/tests/test_coord_collect_tmp.py``
2. ``ui/main_window.py`` 의 표시된 두 줄
3. ``i18n/ko.py`` 의 표시된 블록(시작 ~ 끝)
4. 도구도 필요 없으면 ``dev/coord_lab.py`` · ``dev/coord_lab_nas.py`` · ``dev/coord_lab_seen.txt`` ·
   ``dev/tests/test_coord_lab.py``
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QPushButton

from .. import i18n
from ..utils import paths, prefs
from .widgets import sheet_host as sheets

_LOG = logging.getLogger("aoi.coord_collect")
_TOOL = "dev/coord_lab.py"
# 집중 분석: 정해 둔 Job 몇 개만 돌므로 LOT 마다 웨이퍼를 많이, LOT 도 많이 본다.
_MINUTES, _PER_LOT, _LOTS = 30, 10, 50
# 특별 수집 결과는 늘 이 자리에 '프로젝트명\날짜' 폴더로 남긴다(사용자 지시 2026-11-14).  zip 이름은 짧게.
_SAVE_ROOT = ("Desktop", *i18n.KO.COORD_COLLECT_SAVE_ROOT)
_PROJECT = i18n.KO.COORD_COLLECT_PROJECT
_ZIP_NAME = i18n.KO.COORD_COLLECT_ZIP_NAME


def save_dir(today=None) -> Path:
    r"""``바탕화면\프로그램 개발중\분석용 자료 수집\AOI 레시피 검증\YYYYMMDD`` — 없으면 만든다."""
    from datetime import date
    d = Path.home().joinpath(*_SAVE_ROOT, _PROJECT, f"{today or date.today():%Y%m%d}")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_tool():
    """``dev/coord_lab.py`` 를 모듈로 읽는다.  없으면 None(개발자 모드 설치에만 dev/ 가 있다)."""
    path = paths.resource_path(_TOOL)
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("coord_lab", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Collector(QObject):
    """버튼 하나와 그 작업 — 창에 붙어 창과 함께 죽는다."""

    progress = pyqtSignal(int, int, str)          # 작업 스레드 → 메인 스레드(로딩바)
    finished = pyqtSignal(int, int, str, str)     # 웨이퍼 수, 이상 폴더 수, 결과 경로, 오류

    def __init__(self, window) -> None:
        super().__init__(window)
        self._win = window
        self._running = False
        self._stop = threading.Event()
        self._thread = None
        self.button = QPushButton(i18n.KO.COORD_COLLECT_BUTTON, window.statusBar())
        self.button.setFlat(True)
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button.clicked.connect(self.start)
        self.progress.connect(self._on_progress)
        self.finished.connect(self._on_finished)
        # 로딩바(window._loading)는 여기서 만지지 않는다 — 창이 이 버튼을 붙일 때는 아직 없다.

    def start(self) -> None:
        if self._running:
            return
        if self._thread is not None and self._thread.is_alive():    # 중지한 작업이 아직 NAS 응답을 기다린다
            sheets.info(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_WINDING_DOWN)
            return
        tool = _load_tool()
        if tool is None:
            sheets.warn(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_NO_TOOL)
            return
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        out = Path.home() / "Desktop"
        out = out if out.is_dir() else Path.home()
        found = tool.find_collected(out)         # 이미 모은 zip 이 여러 개면 다시 수집하지 않고 줄여 묶는다
        if found is not None and sheets.ask(
                self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_SLIM_ASK_FMT.format(folder=found),
                labels={yes: i18n.KO.COORD_COLLECT_SLIM_START, no: i18n.KO.COORD_COLLECT_SLIM_SKIP}) == yes:
            self._begin(lambda: self._slim(tool, found))
            return
        if sheets.ask(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_ASK,
                      labels={yes: i18n.KO.COORD_COLLECT_START}) != yes:
            return
        self._begin(lambda: self._collect(tool, save_dir()))

    def _begin(self, job) -> None:
        """로딩바를 띄우고 ``job`` 을 백그라운드에서 돌린다.  ``job`` 은 ``finished`` 에 실을 값을 돌려준다."""
        self._running = True
        self._stop.clear()
        self._win._loading.cancel_requested.connect(self._on_cancel)
        self._win._loading.show_overlay(i18n.KO.COORD_COLLECT_RUNNING, cancelable=True)
        self._win._loading.set_progress(0, 0, i18n.KO.COORD_COLLECT_RUNNING)   # busy → 0 에서 안 멈춤

        def _work() -> None:
            try:
                self.finished.emit(*job(), "")
            except Exception as e:                                  # noqa: BLE001
                _LOG.exception("좌표 자료 수집 실패")
                self.finished.emit(0, 0, "", f"{type(e).__name__}: {e}")

        self._thread = threading.Thread(target=_work, name="coord-collect", daemon=True)
        self._thread.start()

    def _emit_progress(self, done, total, message) -> None:
        self.progress.emit(int(done), int(total), str(message))

    def _collect(self, tool, out):
        base, wafers, odd = tool.run_nas(
            ["--minutes", str(_MINUTES), "--per-lot", str(_PER_LOT), "--lots", str(_LOTS), "--focus",
             "--zip-name", _ZIP_NAME],
            out, progress=self._emit_progress, stop=self._stop.is_set)
        return int(wafers), int(odd), str(base or "")

    def _slim(self, tool, folder):
        """이미 모은 zip 을 줄여 묶는다.  ``finished`` 의 웨이퍼 수 자리에 -1 을 실어 '줄였다' 를 알린다."""
        made, before, after = tool.slim_zips(folder, progress=self._emit_progress)
        return -1, len(made), i18n.KO.COORD_COLLECT_SLIM_DONE_FMT.format(
            before=before / 2 ** 20, after=after / 2 ** 20, n=len(made), names="\n".join(str(p) for p in made))

    def _on_cancel(self) -> None:
        """[중지] 한 번 = 그만 찾고 **지금까지 모은 것을 저장**한다.  한 번 더 = 화면을 바로 푼다
        (NAS 호출 하나에 걸린 작업 스레드를 화면이 끝없이 기다리지 않게)."""
        if not self._running:
            return
        if not self._stop.is_set():
            self._stop.set()
            self._win._loading.set_progress(0, 0, i18n.KO.COORD_COLLECT_STOPPING)
            return
        self._close_overlay()
        sheets.info(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_STOPPED)

    def _close_overlay(self) -> None:
        self._running = False
        self._win._loading.cancel_requested.disconnect(self._on_cancel)
        self._win._loading.hide_overlay()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if self._running:
            if self._stop.is_set():              # 저장 중 — 문구가 '찾는 중' 으로 되돌아가지 않게
                message = i18n.KO.COORD_COLLECT_STOPPING
            self._win._loading.set_progress(done, total, message or i18n.KO.COORD_COLLECT_RUNNING)

    def _on_finished(self, wafers: int, odd: int, path: str, error: str) -> None:
        if not self._running:                    # 중지한 뒤에 도착한 결과 — 이미 화면을 풀었다
            return
        self._close_overlay()
        if error:
            sheets.warn(self._win, i18n.KO.COORD_COLLECT_TITLE,
                        i18n.KO.COORD_COLLECT_FAIL_FMT.format(error=error))
            return
        if wafers < 0:                           # 줄여 묶기가 끝났다 — path 자리에 안내 글이 온다
            sheets.info(self._win, i18n.KO.COORD_COLLECT_TITLE, path)
            return
        if not wafers:
            sheets.info(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_NOTHING)
            return
        sheets.info(self._win, i18n.KO.COORD_COLLECT_TITLE,
                    i18n.KO.COORD_COLLECT_DONE_FMT.format(wafers=wafers, odd=odd, path=path))


def attach(window) -> None:
    """상태바에 버튼을 단다.  ``window._coord_collect_btn`` 으로 보이기/숨기기만 한다."""
    window._coord_collect_btn = QPushButton(window)      # 아래가 실패해도 보이기/숨기기 줄이 죽지 않게
    window._coord_collect_btn.hide()
    try:
        collector = _Collector(window)
        window.statusBar().addPermanentWidget(collector.button)
        collector.button.setVisible(prefs.load().dev_mode)
    except Exception:                                    # noqa: BLE001 — 임시 버튼이 앱 시작을 막으면 안 된다
        _LOG.exception("좌표 자료 수집 버튼을 붙이지 못했습니다")
        return
    window._coord_collect_btn.deleteLater()
    window._coord_collector = collector
    window._coord_collect_btn = collector.button
