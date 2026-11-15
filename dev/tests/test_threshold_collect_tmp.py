"""[Threshold수집-임시] Threshold 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``threshold_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 도구가 Kendall · TB500 Job 만 모으고 사진 · 압축 · 큰 파일은 버리는 것, ③ zip 을 28 MB 조각으로
나누되 조각을 25 MB 이상 채우는 것, ④ 버튼이 로딩바 규칙대로 도는 것.
"""

from __future__ import annotations

import importlib.util
import re
import struct
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "aoi_verification" / "app"
MARK = "[Threshold수집-임시]"
MB = 2 ** 20


def _tool():
    spec = importlib.util.spec_from_file_location("threshold_collect", ROOT / "dev" / "threshold_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DONE = set()          # 단계별 테스트는 끈 단계도 돌려 본다(끄는 것은 test_done_stages_are_skipped)
    return mod


def test_collect_code_lives_only_in_the_listed_places():
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    assert set(found) == {"ui/threshold_collect_tmp.py", "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 2
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2


def test_nothing_outside_the_marks_mentions_the_button():
    for path in APP.rglob("*.py"):
        if path.name == "threshold_collect_tmp.py":
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[Threshold수집-임시\] 시작.*?# ── \[Threshold수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"threshold_collect|THRESH_COLLECT", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(THRESH_COLLECT_\w+)", (APP / "ui" / "threshold_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(THRESH_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def _wafer(d: Path, recipe: bytes = b"[Recipe]\nMinArea=3\n"):
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text("[General]\nSetupPath=c:\\job\\x\\\n")
    (d / "Recipe.ini").write_bytes(recipe)
    (d / "Surface.flt").write_bytes(b"\x00" * 152)
    (d / "W_3_4_Surface Dark_10_20.jpg").write_bytes(b"jpg")
    (d / "old.zip").write_bytes(b"zip")


def test_collects_known_lots_and_drops_useless_files(tmp_path, monkeypatch):
    """스캔 기록에서 고른 LOT 으로 바로 찾아간다(장비 이름 앞 0 무시 · 구분 폴더 한 단 허용),
    목록에 없는 LOT 은 담지 않고, 못 찾은 LOT 은 요약에 남긴다."""
    tool = _tool()
    sr = tmp_path / "AOI-01" / "Scanresult"
    sr4 = tmp_path / "4F-AOI-05" / "Scanresult"
    _wafer(sr / "R_KENDALL_A0_PI5" / "SETUP" / "LOT1" / "W01")
    _wafer(sr / "R_KENDALL_A0_PI5" / "SETUP" / "LOT1" / "W02")
    _wafer(sr / "R_KENDALL_A0_PI5" / "SETUP" / "LOT2" / "W03")               # 목록에 없는 LOT
    _wafer(sr4 / "TB500_RDL2 - Multi" / "Setup1" / "6324" / "TNY" / "W77", recipe=b"[Recipe]\nMinArea=9\n")
    (sr / "R_KENDALL_A0_PI5" / "SETUP" / "LOT1" / "W01" / "huge.dat").write_bytes(b"\x00" * (tool.FILE_MB * MB + 1))
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr, sr4])
    monkeypatch.setattr(tool, "PAIR_LOTS", [])
    monkeypatch.setattr(tool, "KNOWN_LOTS", [
        ("AOI-1", "R_KENDALL_A0_PI5", "SETUP", "LOT1"),
        ("4F-AOI-05", "TB500_RDL2 - Multi", "Setup1", "TNY"),
        ("AOI-9", "R_KENDALL_X", "SETUP", "GONE"),
    ])
    out = tmp_path / "desk"
    folder, wafers, files = tool.run(out)
    assert folder.parent == out and folder.name.startswith("Threshold수집_")
    zips = list(folder.glob("*.zip"))
    assert len(zips) == 1 and not (folder / "_작업중.zip").exists()
    z = zipfile.ZipFile(zips[0])
    names = z.namelist()
    assert wafers == 3
    assert not any("LOT2" in n for n in names)
    assert any(n.startswith("4F-AOI-05/TB500_RDL2 - Multi/Setup1/6324/TNY/W77/") for n in names)
    assert not any(n.endswith((".jpg", ".zip")) or n.endswith("huge.dat") for n in names)
    assert any(n.endswith("W01/Surface.flt") for n in names)
    assert "Surface Dark" in z.read("사진목록.txt").decode()           # 사진은 이름만
    assert "huge.dat" in z.read("파일목록.txt").decode()
    recipes = [n for n in names if n.endswith("Recipe.ini")]
    assert len(recipes) == 2                                             # W02 의 같은 레시피는 중복으로 한 번만
    assert "W02/Recipe.ini" in z.read("중복목록.txt").decode()
    assert "LOT 못 찾음: AOI-9" in z.read("요약.txt").decode()


def test_pair_lots_take_only_the_compared_wafer(tmp_path, monkeypatch):
    """비교 스캔 LOT 에서는 같은 웨이퍼(PAIR_WAFER)만 담는다."""
    tool = _tool()
    lot = tmp_path / "4F-AOI-04" / "Scanresult" / "R_TB500_LIVE_PI4 - Enhanced" / "Setup1" / "YDD-No Image"
    _wafer(lot / "18_20234838EWG1")
    _wafer(lot / "3_35174140EWD4", recipe=b"[Recipe]\nMinArea=7\n")
    monkeypatch.setattr(tool, "PAIR_LOTS", [str(lot)])
    monkeypatch.setattr(tool, "nas_roots", lambda: [])
    folder, wafers, _ = tool.run(tmp_path / "desk")
    names = zipfile.ZipFile(next(folder.glob("*.zip"))).namelist()
    assert wafers == 1
    assert any("18_20234838EWG1/Surface.flt" in n for n in names)
    assert not any("35174140EWD4" in n for n in names)


def test_aoi_key_ignores_case_and_leading_zero():
    tool = _tool()
    assert tool._aoi_key("AOI-01") == tool._aoi_key("aoi-1") == "AOI-1"
    assert tool._aoi_key("4F-AOI-05") == "4F-AOI-5"


def test_pair_lots_take_every_wafer_first(tmp_path, monkeypatch):
    """비교 스캔 LOT(같은 웨이퍼를 Contrast 기준만 바꿔 스캔)은 웨이퍼 수 제한 없이 전부 담고,
    못 찾은 주소는 요약에 남긴다."""
    tool = _tool()
    lot = tmp_path / "4F-AOI-05" / "Scanresult" / "R_TB500_LIVE_PI4 - Enhanced" / "Setup1" / "YDD-Contrast Avg24"
    for w in ("W01", "W02", "W03"):
        _wafer(lot / w)
    monkeypatch.setattr(tool, "PAIR_LOTS", [str(lot), str(tmp_path / "없는폴더")])
    monkeypatch.setattr(tool, "nas_roots", lambda: [])
    folder, wafers, _ = tool.run(tmp_path / "desk")
    z = zipfile.ZipFile(next(folder.glob("*.zip")))
    flts = [n for n in z.namelist() if n.endswith("Surface.flt")]
    assert wafers == 3 and len(flts) == 1                                # 내용이 같아 한 번만 담김
    assert flts[0].startswith("비교스캔/4F-AOI-05/YDD-Contrast Avg24/")
    dup = z.read("중복목록.txt").decode()
    assert "W02/Surface.flt" in dup and "W03/Surface.flt" in dup
    assert "비교 LOT 못 찾음" in z.read("요약.txt").decode()


def test_plan_parts_fills_each_part_between_25_and_30_mb():
    tool = _tool()
    sizes = [("요약.txt", 1000)] + [(f"f{i}", s * MB) for i, s in enumerate([12, 7, 9, 4, 6, 3, 2, 8, 5, 1])]
    parts = tool.plan_parts(sizes)
    total = dict(sizes)
    used = [sum(total[n] for n in p) for p in parts]
    assert parts[0][0] == "요약.txt"
    assert all(u <= tool.PART_MB * MB for u in used)
    assert all(u >= tool.MIN_PART_MB * MB for u in used[:-1])
    assert sorted(n for p in parts for n in p) == sorted(total)


def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import threshold_collect_tmp as mod

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
            return tmp_path / "Threshold수집_x", 3, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    win._thresh_collect_btn.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "12" in said[0] and "Threshold수집_x" in said[0]
    calls = win._loading.calls
    assert calls[0] == ("show", True) and calls[1] == ("progress", 0, 0)   # busy 로 시작
    assert calls[-1] == ("hide",)


def test_rich_surface_whole_file_goes_in_as_needed_columns(tmp_path, monkeypatch):
    """파일 전체를 .md 의 열 위치로 읽어 필요한 열만 CSV 로 담는다.  float32 는 값이 그대로(경계 100 이 안 바뀐다),
    RICH_ROWS 줄마다 CSV 를 나눈다."""
    tool = _tool()
    w = tmp_path / "LOT" / f"{tool.PAIR_WAFER}_18"
    w.mkdir(parents=True)
    md = ('<root><RecordSize Size="40"/><Fields>'
          '<Field Name="reclassify" Id="1" Offset="0" Vartype="2"/><Field Name="x" Id="2" Offset="8" Vartype="5"/>'
          '<Field Name="Junk" Id="3" Offset="16" Vartype="4"/><Field Name="area" Id="4" Offset="20" Vartype="4"/>'
          '<Field Name="AbsDeltaFromRef_Average" Id="5" Offset="24" Vartype="4"/><Field Name="zone" Id="6" Offset="28" Vartype="17"/>'
          '</Fields></root>')
    (w / "s_RichSurface.dat.md").write_text(md)
    recs = [(29, 1.5, 7.0, 26.0, 100.0, 1), (0, 2.25, 7.0, 25.0, 99.99999237060547, 2), (0, 3.0, 7.0, 1.0, 9.0, 1)]
    raw = b"".join(struct.pack("<h6xdfffB11x", *r) for r in recs)
    assert len(raw) == 3 * 40
    (w / "s_RichSurface.dat").write_bytes(raw + b"\x00" * 7)            # 끝 조각(레코드보다 짧음)은 버린다
    monkeypatch.setattr(tool, "RICH_LOT", str(tmp_path / "LOT"))
    monkeypatch.setattr(tool, "RICH_ROWS", 2)
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_rich_surface(col, lambda *_: None, lambda *_: None, lambda: False) == 2
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        arc = f"RichSurface/{w.name}"
        lines = (z.read(f"{arc}/RichSurface_01.csv") + z.read(f"{arc}/RichSurface_02.csv").split(b"\n", 1)[1]).decode().split()
        assert f"{arc}/s_RichSurface.dat.md" in z.namelist() and "3개 중 3개" in z.read(f"{arc}/정보.txt").decode()
    assert lines[0] == "reclassify,x,area,AbsDeltaFromRef_Average,zone"          # Junk 는 안 담는다
    assert lines[1:] == ["29,1.50,26,100,1", "0,2.25,25,99.9999924,2", "0,3.00,1,9,1"]


def test_rich_surface_missing_is_only_noted(tmp_path, monkeypatch):
    tool = _tool()
    (tmp_path / "LOT" / "OTHER01EWA1").mkdir(parents=True)
    monkeypatch.setattr(tool, "RICH_LOT", str(tmp_path / "LOT"))
    said = []
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_rich_surface(col, said.append, lambda *_: None, lambda: False) == 0
    col.close("")
    assert said and "못 찾음" in said[0]


def test_rich_surface_read_error_does_not_stop_the_collection(tmp_path, monkeypatch):
    """NAS 권한 · 연결 문제로 RichSurface 를 못 읽어도 기록만 남기고 나머지 수집을 이어 간다."""
    tool = _tool()
    w = tmp_path / "LOT" / f"{tool.PAIR_WAFER}_18"
    w.mkdir(parents=True)
    (w / "s_RichSurface.dat").write_bytes(b"x" * 100)
    monkeypatch.setattr(tool, "RICH_LOT", str(tmp_path / "LOT"))

    def _deny(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(tool, "_take_rich_surface", _deny)
    said = []
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_rich_surface(col, said.append, lambda *_: None, lambda: False) == 0
    col.close("")
    assert said and "못 읽음" in said[0]


def test_done_stages_are_skipped(tmp_path, monkeypatch):
    """다 받은 단계(DONE)는 run() 이 건너뛴다 — 다시 모으지 않는다."""
    tool = _tool()
    tool.DONE = {"pair", "known", "rich"}
    called = []
    monkeypatch.setattr(tool, "collect_rich_surface", lambda *a, **k: called.append("rich") or 0)
    monkeypatch.setattr(tool, "collect_exports", lambda *a, **k: called.append("export") or 0)
    monkeypatch.setattr(tool, "resolve_pair", lambda raw: called.append("pair") or None)
    monkeypatch.setattr(tool, "find_lot", lambda *a: called.append("known") or None)
    monkeypatch.setattr(tool, "nas_roots", lambda: [tmp_path])
    tool.run(tmp_path)
    assert called == ["export"]


def test_rich_surface_folder_error_is_only_noted(tmp_path, monkeypatch):
    """폴더를 찾는 단계(is_dir)에서 권한 · 연결 오류가 나도 기록만 남기고 0 을 돌려준다(작업용 zip 이 살아남게)."""
    tool = _tool()

    def _deny(raw):
        raise PermissionError("WinError 64")

    monkeypatch.setattr(tool, "resolve_pair", _deny)
    said = []
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_rich_surface(col, said.append, lambda *_: None, lambda: False) == 0
    col.close("")
    assert said and "못 읽음" in said[0] and "WinError 64" in said[0]


def test_exports_take_defects_csv_and_note_missing(tmp_path, monkeypatch):
    """Export 목록 웨이퍼의 Defects.csv 를 담는다.  '폴더 전부' 표시면 웨이퍼 폴더 전체, Export 전이면 요약에 적는다."""
    tool = _tool()
    sr = tmp_path / "AOI-27" / "Scanresult"
    a = sr / "J1" / "Setup1" / "With_EDC" / "S3FAR079XMA7"
    _wafer(a)
    (a / "Defects.csv").write_text("#,Type,Area,Width,Length,Col,Row,X,Y,Classify,Visit\n1,1,70,3,9,4,5,10,20,29,0\n")
    b = sr / "J2" / "SETUP" / "DDU" / "18_E5025794EWF5"
    _wafer(b)                                                             # Export 전 — Defects.csv 없음
    c = sr / "J3" / "SETUP" / "L3" / "W9"
    _wafer(c)
    (c / "Defects.csv").write_text("#,Area\n1,5\n")
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    monkeypatch.setattr(tool, "EXPORT_WAFERS", [
        ("AOI-27", "J1", "Setup1", "With_EDC", "S3FAR079XMA7", True),
        ("AOI-27", "J2", "SETUP", "DDU", "E5025794EWF5", False),
        ("AOI-27", "J3", "SETUP", "L3", "W9", False),
        ("AOI-99", "J", "S", "L", "W", False),
    ])
    said = []
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_exports(col, said.append, lambda *_: None, lambda: False) == 2
    col.close("")
    names = zipfile.ZipFile(tmp_path / "s.zip").namelist()
    assert "Export/AOI-27/With_EDC/S3FAR079XMA7/Defects.csv" in names
    assert "Export/AOI-27/With_EDC/S3FAR079XMA7/Recipe.ini" in names      # 폴더 전부
    assert names.count("Export/AOI-27/L3/W9/Defects.csv") == 1
    assert not any(n.startswith("Export/AOI-27/L3/W9/") and not n.endswith("Defects.csv") for n in names)
    assert not any("DDU" in n for n in names)
    assert "Defects.csv 없음" in "\n".join(said)
    assert said[-1].startswith("Export Defects.csv 2/4") and "E5025794EWF5" in said[-1] and "AOI-99" in said[-1]
