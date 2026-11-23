"""[멀티레시피수집-임시] 멀티 레시피 Defect 정보 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``multi_recipe_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 웨이퍼 폴더의 사진이 아닌 파일(하위 폴더 포함)을 담고 사진 · 큰 파일 · 압축은 이름만 남기는 것,
③ 부모 폴더(Job 까지)는 파일만 담는 것, ④ 적힌 드라이브가 없으면 같은 장비의 다른 공유에서 찾는 것,
⑤ zip 을 28 MB 조각으로 나누는 것, ⑥ 버튼이 로딩바 규칙대로 도는 것.
"""

from __future__ import annotations

import importlib.util
import re
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aoi_verification" / "app"
MARK = "[멀티레시피수집-임시]"
MB = 2 ** 20


def _tool():
    spec = importlib.util.spec_from_file_location("multi_recipe_collect", ROOT / "dev" / "multi_recipe_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collect_code_lives_only_in_the_listed_places():
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    assert set(found) == {"ui/multi_recipe_collect_tmp.py", "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 2
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2


def test_nothing_outside_the_marks_mentions_the_button():
    for path in APP.rglob("*.py"):
        if path.name == "multi_recipe_collect_tmp.py":
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[멀티레시피수집-임시\] 시작.*?# ── \[멀티레시피수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"multi_recipe_collect|MRCP_COLLECT", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(MRCP_COLLECT_\w+)",
                          (APP / "ui" / "multi_recipe_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(MRCP_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def _fake_nas(tmp_path: Path):
    """``<share>/AOI-16/Scanresult/Job/6324/Step/Wafer`` 하나."""
    sr = tmp_path / "share" / "AOI-16" / "Scanresult"
    job = sr / "2D@R5-T254"
    w = job / "6324" / "CMG" / "GX1"
    (w / "Zones").mkdir(parents=True)
    (w / "Recipe2-Zones" / "deep").mkdir(parents=True)
    (w / "Defects.csv").write_text("#,Type,Area\n1,Height-z,0\n")
    (w / "Surface.flt").write_bytes(b"\x01" * 64)
    (w / "Height.flt").write_bytes(b"\x02" * 208)
    (w / "Height.flt.md").write_text("<RecordSize Size=\"208\"/>")
    (w / "1.2.c.3.1.jpeg").write_bytes(b"jpg")
    (w / "old.zip").write_bytes(b"zip")
    (w / "Zones" / "a.ini").write_text("[General]\nZoneName=a\nZoneID=1\n")
    (w / "Recipe2-Zones" / "deep" / "b.ini").write_text("[General]\nZoneName=b\n")
    (w.parent / "lot.ini").write_text("lot")
    (w.parent / "Other").mkdir()
    (w.parent / "Other" / "skip.ini").write_text("other wafer")
    (job / "EquipmentClassifications.ini").write_text("[Height]\n2097152=Missing Bump\n")
    (sr / "top.ini").write_text("scanresult level")
    return sr, w


def test_collects_wafer_files_parents_and_photo_names(tmp_path, monkeypatch):
    tool = _tool()
    sr, _ = _fake_nas(tmp_path)
    monkeypatch.setattr(tool, "FILE_MB", 100 / MB)       # Height.flt(208 B) 는 크다고 보고 이름만
    raw = r"V:\AOI-16\Scanresult\2D@R5-T254\6324\CMG\GX1"
    out = tmp_path / "out"
    out.mkdir()
    folder, wafers, files = tool.run(out, wafers=[raw, r"V:\AOI-16\Scanresult\none\X"],
                                     roots={"AOI-16": sr})
    assert wafers == 1 and folder is not None
    (z,) = sorted(folder.glob("*.zip"))
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        listing = zf.read("파일목록.txt").decode()
        photos = zf.read("사진목록.txt").decode()
        summary = zf.read("요약.txt").decode()
    base = "AOI-16/2D@R5-T254/6324/CMG/GX1"
    assert {f"{base}/Defects.csv", f"{base}/Surface.flt", f"{base}/Height.flt.md",
            f"{base}/Zones/a.ini", f"{base}/Recipe2-Zones/deep/b.ini",
            "AOI-16/2D@R5-T254/6324/CMG/lot.ini",
            "AOI-16/2D@R5-T254/EquipmentClassifications.ini"} <= names
    assert f"{base}/Height.flt" not in names and f"{base}/Height.flt\t208\t뺌" in listing
    assert f"{base}/old.zip" not in names
    assert f"{base}/1.2.c.3.1.jpeg" in photos and not any(n.endswith(".jpeg") for n in names)
    assert not any("Other" in n or "top.ini" in n for n in names)      # 옆 웨이퍼 · Scanresult 위는 안 담는다
    assert "Defects.csv 있음" in summary and "못 찾음" in summary


def test_written_drive_path_is_used_when_it_exists(tmp_path):
    tool = _tool()
    _, w = _fake_nas(tmp_path)
    assert tool.locate(str(w), roots={}) == w


def test_nothing_found_leaves_no_folder(tmp_path):
    tool = _tool()
    assert tool.run(tmp_path, wafers=[r"V:\AOI-16\Scanresult\x\y"], roots={}) == (None, 0, 0)
    assert list(tmp_path.iterdir()) == []


def test_list_and_limits():
    tool = _tool()
    assert len(tool.WAFERS) == 10 and tool.WAFERS[0].endswith("GX57010487")
    assert all(tool._split(w)[0] == "AOI-16" for w in tool.WAFERS)
    assert tool.PART_MB == 28 and tool.FILE_MB == 30


def test_plan_parts_stays_under_28_mb():
    tool = _tool()
    sizes = [("요약.txt", 1000)] + [(f"f{i}", s * MB) for i, s in enumerate([12, 7, 9, 4, 6, 3, 2, 8, 5, 1])]
    parts = tool.plan_parts(sizes)
    total = dict(sizes)
    assert parts[0][0] == "요약.txt"
    assert all(sum(total[n] for n in p) <= tool.PART_MB * MB for p in parts)
    assert sorted(n for p in parts for n in p) == sorted(total)


def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import multi_recipe_collect_tmp as mod

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
            progress(1, 4, "Job 2/4")
            return tmp_path / "멀티레시피수집_x", 3, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    win._mrcp_collect_btn.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "12" in said[0] and "멀티레시피수집_x" in said[0]
    calls = win._loading.calls
    assert calls[0] == ("show", True) and calls[1] == ("progress", 0, 0)   # busy 로 시작
    assert calls[-1] == ("hide",)


def test_file_that_cannot_fit_one_part_is_listed_not_zipped(tmp_path, monkeypatch):
    """압축해도 한 조각(28 MB)을 넘는 파일은 올릴 수 없다 — 이름 · 크기만 적는다."""
    import os as _os
    tool = _tool()
    sr, w = _fake_nas(tmp_path)
    (w / "Big.flt").write_bytes(_os.urandom(4096))          # 압축이 안 되는 내용
    monkeypatch.setattr(tool, "PART_MB", 3000 / MB)
    out = tmp_path / "out"
    out.mkdir()
    folder, _, _ = tool.run(out, wafers=[r"V:\AOI-16\Scanresult\2D@R5-T254\6324\CMG\GX1"],
                            roots={"AOI-16": sr})
    names, listing = set(), ""
    for z in folder.glob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            names |= set(zf.namelist())
            if "파일목록.txt" in zf.namelist():
                listing = zf.read("파일목록.txt").decode()
    assert not any(n.endswith("Big.flt") for n in names)
    assert "Big.flt\t4096\t뺌(압축해도" in listing
    assert any(n.endswith("Defects.csv") for n in names)


def test_stop_is_checked_between_files(tmp_path):
    tool = _tool()
    sr, w = _fake_nas(tmp_path)
    col = tool.Collector(tmp_path / "s.zip")
    calls = iter([False, False, True])            # 사진(이름만) · Defects.csv 다음에 중지
    col.take(w, "a", 0, expired=lambda: next(calls, True))
    col.close("")
    assert col.files == 1                                      # 첫 파일만 담고 멈춤
