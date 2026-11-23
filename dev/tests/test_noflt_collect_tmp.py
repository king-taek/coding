"""[Surface없음수집-임시] Surface.flt 없는 폴더 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``noflt_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 고른 LOT 폴더 아래 웨이퍼의 사진이 아닌 파일을 담고 사진 · 큰 파일 · 압축은 이름만 남기는 것,
③ 부모 폴더(Job 까지)는 파일만 담는 것, ④ 옆 LOT 은 목록만 남기고 Surface.flt 가 있는 최근 웨이퍼만 비교용으로 담는 것,
⑤ zip 을 28 MB 조각으로 나누는 것, ⑥ 버튼이 로딩바 규칙대로 도는 것.
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
    """``<share>/4F-AOI-04/Scanresult/<Job>/Setup1/<LOT>/<Wafer>`` — 고른 LOT 하나 + 옆 LOT 둘."""
    sr = tmp_path / "share" / "4F-AOI-04" / "Scanresult"
    setup = sr / JOB / "Setup1"
    w = setup / "YDD-PIDS3 RE" / "W1"
    (w / "Zones").mkdir(parents=True)
    (w / "Defects.csv").write_text("#,Type,Area\n1,Surface-PI,3\n")
    (w / "DieAlignMiss.flt").write_bytes(b"\x01" * 64)
    (w / "1.2.c.3.jpeg").write_bytes(b"jpg")
    (w / "WaferAlignData.zip").write_bytes(b"zip")
    (w / "Zones" / "PI.ini").write_text("[General]\nZoneName=PI\nZoneID=1\n")
    (w.parent / "lot.ini").write_text("lot")
    (setup / "setup.ini").write_text("setup")
    (sr / JOB / "Recipe.ini").write_text("[x]\n")
    (sr / "top.ini").write_text("scanresult level")
    for i, lot in enumerate(("VHM_PIDS3", "SXK-PIDS3", "OLD")):
        s = setup / lot / f"S{i}"
        s.mkdir(parents=True)
        (s / "Surface.flt").write_bytes(bytes([i]) * 152)
        (s / "Surface.flt.md").write_text('<RecordSize Size="152"/>')
        (s / "9.9.c.9.jpeg").write_bytes(b"jpg")
        os.utime(s / "Surface.flt", (1_700_000_000, 1_700_000_000 + (0 if lot == "OLD" else (i + 1) * 3600)))
    (setup / "EMPTY" / "E1").mkdir(parents=True)
    (setup / "EMPTY" / "E1" / "ProductInfo.ini").write_text("[x]\n")
    return sr, w


def test_collects_target_parents_siblings_and_photo_names(tmp_path):
    tool = _tool()
    sr, _ = _fake_nas(tmp_path)
    raw = rf"I:\4F-AOI-04\Scanresult\{JOB}\Setup1\YDD-PIDS3 RE"
    out = tmp_path / "out"
    out.mkdir()
    folder, found, _files = tool.run(out, targets=[raw, rf"I:\4F-AOI-04\Scanresult\{JOB}\Setup1\none"],
                                     roots={"4F-AOI-04": sr})
    assert found == 1 and folder is not None
    (z,) = sorted(folder.glob("*.zip"))
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        listing = zf.read("파일목록.txt").decode()
        photos = zf.read("사진목록.txt").decode()
        summary = zf.read("요약.txt").decode()
        siblings = zf.read("옆폴더목록.txt").decode()
    base = f"4F-AOI-04/{JOB}/Setup1"
    assert {f"{base}/YDD-PIDS3 RE/W1/Defects.csv", f"{base}/YDD-PIDS3 RE/W1/DieAlignMiss.flt",
            f"{base}/YDD-PIDS3 RE/W1/Zones/PI.ini", f"{base}/YDD-PIDS3 RE/lot.ini",
            f"{base}/setup.ini", f"4F-AOI-04/{JOB}/Recipe.ini"} <= names
    assert not any(n.endswith((".jpeg", ".zip")) for n in names)
    assert f"{base}/YDD-PIDS3 RE/W1/1.2.c.3.jpeg" in photos
    assert not any("top.ini" in n for n in names)                         # Scanresult 위는 안 담는다
    # 옆 LOT: 목록에는 전부, 내용은 Surface.flt 가 가장 최근인 두 곳만
    assert f"{base}/SXK-PIDS3/S1/Surface.flt" in names and f"{base}/VHM_PIDS3/S0/Surface.flt" in names
    assert not any("/OLD/" in n or "/EMPTY/" in n for n in names)
    assert f"{base}/OLD/S2/\t사진 1\t하위폴더 0\tflt Surface.flt" in siblings
    assert f"{base}/EMPTY/E1/ProductInfo.ini\t" in siblings
    assert "YDD-PIDS3 RE" not in siblings                                 # 고른 폴더는 목록에 다시 안 넣는다
    assert "flt 종류 DieAlignMiss.flt" in summary and "Defects.csv 1" in summary and "못 찾음" in summary
    assert f"{base}/YDD-PIDS3 RE/W1/WaferAlignData.zip\t" in listing


def test_nothing_found_leaves_no_folder(tmp_path):
    tool = _tool()
    assert tool.run(tmp_path, targets=[r"I:\4F-AOI-04\Scanresult\x\y"], roots={}) == (None, 0, 0)
    assert list(tmp_path.iterdir()) == []


def test_targets_and_limits():
    tool = _tool()
    assert len(tool.TARGETS) == 3 and tool.TARGETS[0].endswith("YDD-PIDS3 RE")
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
