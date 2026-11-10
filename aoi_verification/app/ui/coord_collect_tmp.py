"""[좌표수집-임시] 좌표 자료 수집 버튼 — **수집·분석이 끝나면 지우는 코드**다.

개발자 모드(Ctrl+Shift+D)일 때만 상태바에 버튼 하나가 보인다.  누르면 ``dev/coord_lab.py`` 의
NAS 순회(``run_nas``)를 백그라운드에서 돌리고 로딩바로 진행을 보인다.  결과는 바탕화면에
``좌표실험_<시각>.*`` 로 남는다.  읽기 전용이고 앱의 좌표 계산·매칭에는 손대지 않는다.

지우는 법(전부 ``[좌표수집-임시]`` 로 검색된다 — ``test_coord_collect_tmp.py`` 가 이 목록을 지킨다):

1. 이 파일 · ``dev/tests/test_coord_collect_tmp.py``
2. ``ui/main_window.py`` 의 표시된 두 줄
3. ``i18n/ko.py`` 의 표시된 블록(시작 ~ 끝)
4. 도구도 필요 없으면 ``dev/coord_lab.py`` · ``dev/coord_lab_nas.py`` · ``dev/tests/test_coord_lab.py``
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
_MINUTES, _PER_LOT, _LOTS = 30, 3, 5


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
        tool = _load_tool()
        if tool is None:
            sheets.warn(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_NO_TOOL)
            return
        yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        if sheets.ask(self._win, i18n.KO.COORD_COLLECT_TITLE, i18n.KO.COORD_COLLECT_ASK,
                      labels={yes: i18n.KO.COORD_COLLECT_START}) != yes:
            return
        self._running = True
        self._stop.clear()
        self._win._loading.cancel_requested.connect(self._on_cancel)
        self._win._loading.show_overlay(i18n.KO.COORD_COLLECT_RUNNING, cancelable=True)
        self._win._loading.set_progress(0, 0, i18n.KO.COORD_COLLECT_RUNNING)   # busy → 0 에서 안 멈춤
        out = Path.home() / "Desktop"
        out = out if out.is_dir() else Path.home()

        def _work() -> None:
            try:
                base, wafers, odd = tool.run_nas(
                    ["--minutes", str(_MINUTES), "--per-lot", str(_PER_LOT), "--lots", str(_LOTS)],
                    out, progress=lambda d, t, m: self.progress.emit(int(d), int(t), str(m)),
                    stop=self._stop.is_set)
                self.finished.emit(int(wafers), int(odd), str(base), "")
            except Exception as e:                                  # noqa: BLE001
                _LOG.exception("좌표 자료 수집 실패")
                self.finished.emit(0, 0, "", f"{type(e).__name__}: {e}")

        threading.Thread(target=_work, name="coord-collect", daemon=True).start()

    def _on_cancel(self) -> None:
        if self._running:
            self._stop.set()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if self._running:
            self._win._loading.set_progress(done, total, message or i18n.KO.COORD_COLLECT_RUNNING)

    def _on_finished(self, wafers: int, odd: int, path: str, error: str) -> None:
        self._running = False
        self._win._loading.cancel_requested.disconnect(self._on_cancel)
        self._win._loading.hide_overlay()
        if error:
            sheets.warn(self._win, i18n.KO.COORD_COLLECT_TITLE,
                        i18n.KO.COORD_COLLECT_FAIL_FMT.format(error=error))
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
