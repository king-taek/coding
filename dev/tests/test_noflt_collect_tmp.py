"""[Surface없음수집-임시] Surface.flt 없는 폴더 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``noflt_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 고른 웨이퍼 폴더의 사진이 아닌 파일을 담고 사진 · 큰 파일 · 압축은 이름만 남기는 것,
③ LOT 폴더는 파일만 담고 셋업 · Job · 옆 LOT 은 담지 않는 것(1차 수집에서 받았다),
④ zip 을 28 MB 조각으로 나누는 것, ⑤ 버튼이 로딩바 규칙대로 도는 것.
"""

from __future__ import annotations

import importlib.util
import os
import re
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aoi_verification" / "app"
MARK = "[Surface없음수집-임시]"
MB = 2 ** 20
JOB = "R_TB500_LIVE_PI4 - Enhanced"


def _tool():
    spec = importlib.util.spec_from_file_location("noflt_collect", ROOT / "dev" / "noflt_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collect_code_lives_only_in_the_listed_places():
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    assert set(found) == {"ui/noflt_collect_tmp.py", "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 2
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2


def test_nothing_outside_the_marks_mentions_the_button():
    for path in APP.rglob("*.py"):
        if path.name == "noflt_collect_tmp.py":
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[Surface없음수집-임시\] 시작.*?# ── \[Surface없음수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"noflt_collect|NOFLT_COLLECT", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(NOFLT_COLLECT_\w+)",
                          (APP / "ui" / "noflt_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(NOFLT_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def _fake_nas(tmp_path: Path):
    """``<share>/4F-AOI-04/Scanresult/<Job>/Setup1/<LOT>/<Wafer>`` — 고른 웨이퍼 하나 + 같은 LOT 의 다른 웨이퍼 + 옆 LOT."""
    sr = tmp_path / "share" / "4F-AOI-04" / "Scanresult"
    setup = sr / JOB / "Setup1"
    lot = setup / "YDD-PIDS3 RE"
    w = lot / "35174142EWE5"
    (w / "Zones").mkdir(parents=True)
    (w / "Defects.csv").write_text("#,Type,Area\n1,Surface-PI,3\n")
    (w / "DieAlignMiss.flt").write_bytes(b"\x01" * 64)
    (w / "1.2.c.3.1.jpeg").write_bytes(b"jpg")
    (w / "WaferAlignData.zip").write_bytes(b"zip")
    (w / "Zones" / "PI.ini").write_text("[General]\nZoneName=PI\nZoneID=1\n")
    (lot / "lot.ini").write_text("lot")
    (lot / "35174143EWB4").mkdir()
    (lot / "35174143EWB4" / "Surface.flt").write_bytes(b"\x02" * 152)
    (setup / "setup.ini").write_text("setup")
    (sr / JOB / "Recipe.ini").write_text("[x]\n")
    (setup / "VHM_PIDS3" / "S0").mkdir(parents=True)
    (setup / "VHM_PIDS3" / "S0" / "Surface.flt").write_bytes(b"\x03" * 152)
    return sr, w


def test_collects_wafer_files_lot_files_and_photo_names(tmp_path):
    tool = _tool()
    sr, _ = _fake_nas(tmp_path)
    lot = rf"I:\4F-AOI-04\Scanresult\{JOB}\Setup1\YDD-PIDS3 RE"
    out = tmp_path / "out"
    out.mkdir()
    folder, found, _files = tool.run(out, targets=[lot + r"\35174142EWE5", lot + r"\none"],
                                     roots={"4F-AOI-04": sr})
    assert found == 1 and folder is not None
    (z,) = sorted(folder.glob("*.zip"))
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        listing = zf.read("파일목록.txt").decode()
        photos = zf.read("사진목록.txt").decode()
        summary = zf.read("요약.txt").decode()
    base = f"4F-AOI-04/{JOB}/Setup1/YDD-PIDS3 RE"
    assert {f"{base}/35174142EWE5/Defects.csv", f"{base}/35174142EWE5/DieAlignMiss.flt",
            f"{base}/35174142EWE5/Zones/PI.ini", f"{base}/lot.ini"} <= names
    assert not any(n.endswith((".jpeg", ".zip")) for n in names)
    assert f"{base}/35174142EWE5/1.2.c.3.1.jpeg" in photos
    # 1차 수집에서 받은 것은 다시 담지 않는다 — 셋업 · Job 파일, 같은 LOT 의 다른 웨이퍼, 옆 LOT.
    assert not any(n.endswith(("setup.ini", "Recipe.ini")) or "35174143EWB4" in n or "VHM_PIDS3" in n
                   for n in names)
    assert "옆폴더목록.txt" not in names
    assert "Defects.csv 1" in summary and "사진 1" in summary and "flt 종류 DieAlignMiss.flt" in summary
    assert "못 찾음" in summary
    assert f"{base}/35174142EWE5/WaferAlignData.zip\t" in listing


def test_nothing_found_leaves_no_folder(tmp_path):
    tool = _tool()
    assert tool.run(tmp_path, targets=[r"I:\4F-AOI-04\Scanresult\x\y"], roots={}) == (None, 0, 0)
    assert list(tmp_path.iterdir()) == []


def test_targets_and_limits():
    tool = _tool()
    assert [t.rsplit("\\", 1)[1] for t in tool.TARGETS] == ["35174142EWE5", "35174140EWD4", "35174664EWD0"]
    assert all(t.rsplit("\\", 2)[1] == "YDD-PIDS3 RE" for t in tool.TARGETS)
    assert all(tool._split(t)[0] == "4F-AOI-04" for t in tool.TARGETS)
    assert tool.PART_MB == 28 and tool.FILE_MB == 30


def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import noflt_collect_tmp as mod

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
        def run(out, progress=None, stop=None):
            progress(0, 0, "폴더 1/3")
            return tmp_path / "Surface없음수집_x", 3, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    win._noflt_collect_btn.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "12" in said[0] and "Surface없음수집_x" in said[0]
    calls = win._loading.calls
    assert calls[0] == ("show", True) and calls[1] == ("progress", 0, 0)   # busy 로 시작
    assert calls[-1] == ("hide",)
