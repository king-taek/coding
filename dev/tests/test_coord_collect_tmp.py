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
        @staticmethod
        def run_nas(argv, out, progress=None, stop=None):
            progress(1, 4, "계산 2/4")
            return tmp_path / "좌표실험", 4, 1

    win = QMainWindow()
    win._loading = _Loading()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)

    win._coord_collect_btn.click()
    deadline = time.time() + 5
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "4" in said[0]
    assert win._loading.calls[:2] == [("show", True), ("progress", 0, 0)]   # busy 로 시작
    assert ("progress", 1, 4) in win._loading.calls and win._loading.calls[-1] == ("hide",)
    win.deleteLater()
