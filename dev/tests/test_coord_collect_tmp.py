"""[좌표수집-임시] 좌표 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것은 둘이다: ① 지울 곳이 ``coord_collect_tmp.py`` docstring 의 목록 그대로라는 것
(표시 없는 곳에 수집 코드가 새지 않게), ② 버튼이 로딩바 규칙대로 도는 것.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aoi_verification" / "app"
MARK = "[좌표수집-임시]"


def _marked_files() -> dict[str, list[str]]:
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    return found


def test_collect_code_lives_only_in_the_listed_places():
    found = _marked_files()
    assert set(found) == {"ui/coord_collect_tmp.py", "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 2                     # 붙이는 줄 · 보이기/숨기기 줄
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2                            # 시작 · 끝


def test_nothing_outside_the_marks_mentions_the_button():
    """표시된 줄·블록을 지우면 앱 어디에도 수집 이름이 남지 않는다."""
    for path in APP.rglob("*.py"):
        if path.name == "coord_collect_tmp.py":
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[좌표수집-임시\] 시작.*?# ── \[좌표수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"coord_collect|COORD_COLLECT", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(COORD_COLLECT_\w+)", (APP / "ui" / "coord_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(COORD_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def test_tool_reports_progress_and_can_be_stopped():
    spec = importlib.util.spec_from_file_location("coord_lab", ROOT / "dev" / "coord_lab.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert {"progress", "stop"} <= set(inspect.signature(tool.run_nas).parameters)


def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import coord_collect_tmp as mod

    class _Loading(QObject):
        cancel_requested = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.calls = []

        def show_overlay(self, message, **kw):
            self.calls.append(("show", kw.get("cancelable")))

        def set_progress(self, done, total, message):
            self.calls.append(("progress", done, total))

        def hide_overlay(self, then=None):
            self.calls.append(("hide",))

    class _Tool:
        found = None                                     # 이미 모은 zip 이 있는 폴더(없으면 None)

        @classmethod
        def find_collected(cls, desktop):
            return cls.found

        @staticmethod
        def slim_zips(folder, progress=None):
            progress(1, 2, "줄여 묶는 중")
            return [folder / "좌표실험_t_줄임.zip"], 450 * 2 ** 20, 20 * 2 ** 20

        argv = None

        @classmethod
        def run_nas(cls, argv, out, progress=None, stop=None):
            cls.argv = list(argv)
            progress(1, 4, "계산 2/4")
            return tmp_path / "좌표실험", 4, 1

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)                 # 실제 창처럼 로딩바가 생기기 전에 붙인다(그때 죽은 적이 있다)
    win._loading = _Loading()

    win._coord_collect_btn.click()
    deadline = time.time() + 5
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "4" in said[0]
    assert "--focus" in _Tool.argv and "--shift-only" not in _Tool.argv   # 집중 분석(2026-11-14)
    assert win._loading.calls[:2] == [("show", True), ("progress", 0, 0)]   # busy 로 시작
    assert ("progress", 1, 4) in win._loading.calls and win._loading.calls[-1] == ("hide",)

    # 이미 모은 zip 이 여러 개면 다시 수집하지 않고 줄여 묶는다(zip 25개를 올릴 수 없었다 — 사용자 2026-11-12)
    _Tool.found, win._loading.calls = tmp_path, []
    del said[:]
    win._coord_collect_btn.click()
    deadline = time.time() + 5
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "450" in said[0] and "좌표실험_t_줄임.zip" in said[0]
    assert ("progress", 1, 2) in win._loading.calls and ("progress", 1, 4) not in win._loading.calls
    win.deleteLater()


def test_attach_never_touches_the_loading_overlay():
    """창은 로딩바를 만들기 전에 버튼을 붙인다 — 붙일 때 ``_loading`` 을 만지면 앱이 시작에서 죽는다(실측)."""
    import ast
    tree = ast.parse((APP / "ui" / "coord_collect_tmp.py").read_text(encoding="utf-8"))
    early = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ("__init__", "attach")]
    assert len(early) == 2
    for fn in early:
        assert not [n for n in ast.walk(fn) if isinstance(n, ast.Attribute) and n.attr == "_loading"], fn.name
    src = (APP / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert src.index("coord_collect_tmp.attach(self)") < src.index("self._loading = LoadingOverlay(self)")


def test_broken_button_does_not_stop_the_app(styled_qapp, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QMainWindow

    from aoi_verification.app.ui import coord_collect_tmp as mod

    def boom(window):
        raise RuntimeError("x")

    monkeypatch.setattr(mod, "_Collector", boom)
    win = QMainWindow()
    mod.attach(win)                                   # 예외가 새지 않는다
    win._coord_collect_btn.setVisible(True)           # 개발자 모드 전환 줄도 그대로 돈다
    win.deleteLater()


def test_second_stop_releases_the_screen_without_waiting_for_the_worker(styled_qapp, monkeypatch):
    """[중지] 한 번은 저장하고 끝내라는 뜻, 두 번은 화면을 바로 풀라는 뜻.
    실측: 중지 뒤 5분 넘게 '진행 중' 이었다 — NAS 호출에 걸린 작업을 화면이 끝없이 기다리면 안 된다."""
    pytest.importorskip("PyQt6.QtWidgets")
    import threading

    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import coord_collect_tmp as mod

    class _Loading(QObject):
        cancel_requested = pyqtSignal()
        shown = True

        def show_overlay(self, message, **kw):
            self.shown = True

        def set_progress(self, done, total, message):
            self.message = message

        def hide_overlay(self, then=None):
            self.shown = False

    gate = threading.Event()

    class _Stuck:
        find_collected = staticmethod(lambda desktop: None)

        @staticmethod
        def run_nas(argv, out, progress=None, stop=None):
            gate.wait(5)                                  # NAS 호출 하나에 걸려 있다
            return None, 0, 0

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Stuck)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()
    win._coord_collect_btn.click()
    win._loading.cancel_requested.emit()                  # [중지] 한 번 — 저장하는 중
    assert win._loading.shown and not said and win._loading.message == mod.i18n.KO.COORD_COLLECT_STOPPING
    assert win._coord_collector._stop.is_set()
    win._loading.cancel_requested.emit()                  # 한 번 더 — 화면을 바로 푼다
    assert not win._loading.shown and said == [mod.i18n.KO.COORD_COLLECT_STOPPED]
    win._coord_collect_btn.click()                        # 작업이 아직 살아 있으면 새로 시작하지 않는다
    assert said[-1] == mod.i18n.KO.COORD_COLLECT_WINDING_DOWN
    gate.set()
    win._coord_collector._thread.join(5)
    styled_qapp.processEvents()
    assert len(said) == 2                                 # 뒤늦은 결과는 버린다
    win.deleteLater()
