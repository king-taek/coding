"""[Threshold수집-임시] Threshold 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``threshold_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 도구가 Kendall · TB500 Job 만 모으고 사진 · 압축 · 큰 파일은 버리는 것, ③ zip 을 30 MB 조각으로
나누되 조각을 25 MB 이상 채우는 것, ④ 버튼이 로딩바 규칙대로 도는 것.
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
MARK = "[Threshold수집-임시]"
MB = 2 ** 20


def _tool():
    spec = importlib.util.spec_from_file_location("threshold_collect", ROOT / "dev" / "threshold_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
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
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    monkeypatch.setattr(tool, "KLA_PAIRS", [])
    monkeypatch.setattr(tool, "CONDER_ROW_LOTS", [])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [])
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
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    monkeypatch.setattr(tool, "KLA_PAIRS", [])
    monkeypatch.setattr(tool, "CONDER_ROW_LOTS", [])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [])
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
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    monkeypatch.setattr(tool, "KLA_PAIRS", [])
    monkeypatch.setattr(tool, "CONDER_ROW_LOTS", [])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [])
    folder, wafers, _ = tool.run(tmp_path / "desk")
    z = zipfile.ZipFile(next(folder.glob("*.zip")))
    flts = [n for n in z.namelist() if n.endswith("Surface.flt")]
    assert wafers == 3 and len(flts) == 1                                # 내용이 같아 한 번만 담김
    assert flts[0].startswith("비교스캔/4F-AOI-05/YDD-Contrast Avg24/")
    dup = z.read("중복목록.txt").decode()
    assert "W02/Surface.flt" in dup and "W03/Surface.flt" in dup
    assert "비교 LOT 못 찾음" in z.read("요약.txt").decode()


def test_kla_pairs_take_camtek_and_kla_folders_side_by_side(tmp_path, monkeypatch):
    """KLA 폴더는 <루트>\\<날짜>\\<LOT>\\<웨이퍼 시작>_<슬롯>, 시각이 안 맞으면 같은 날짜 · 같은 슬롯 중 가장 가까운 폴더를 쓴다."""
    tool = _tool()
    sr = tmp_path / "AOI-20" / "Scanresult"
    _wafer(sr / "TB500_RDL4 - Multi" / "Setup1" / "VPK" / "31266529EWF6")
    kroot = tmp_path / "H"
    k1 = kroot / "2026-10-03" / "TB500.INT895@6412" / "2026-10-03-02-08_1"
    k2 = kroot / "2026-10-03" / "TB500.INT895@6412" / "2026-10-03-02-31_2"
    for k in (k1, k2):
        k.mkdir(parents=True)
        (k / "31266529EWF6.001").write_text('WaferID "31266529EWF6";')
        (k / "31266529EWF6_2_0_23_2.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(tool, "kla_bases", lambda: [tmp_path / "없음", kroot])
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    monkeypatch.setattr(tool, "PAIR_LOTS", [])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [])
    monkeypatch.setattr(tool, "KNOWN_LOTS", [])
    monkeypatch.setattr(tool, "KLA_PAIRS", [
        ("4F-K1", "TB500.INT895@6412", ["2026-10-03-02-08", "2026-10-03-02-30"], "AOI-20", "TB500_RDL4 - Multi", "Setup1", "VPK",
         ["31266529EWF6", "NOPE"], [1, 2]),
    ])
    folder, wafers, _ = tool.run(tmp_path / "desk")
    z = zipfile.ZipFile(next(folder.glob("*.zip")))
    names = z.namelist()
    assert wafers == 1
    assert "KLA비교/TB500.INT895@6412/Camtek/AOI-20/31266529EWF6/Surface.flt" in names
    assert "KLA비교/TB500.INT895@6412/KLA/2026-10-03-02-08_1/31266529EWF6.001" in names
    assert any(n.endswith("2026-10-03-02-31_2/31266529EWF6.001") for n in names) or \
        "2026-10-03-02-31_2" in z.read("중복목록.txt").decode()         # 1분 달라도 같은 날짜 · 슬롯 2
    assert "31266529EWF6_2_0_23_2.jpg" in z.read("사진목록.txt").decode()
    assert "Camtek 웨이퍼 못 찾음" in z.read("요약.txt").decode()


def test_qorvo_known_lots_come_before_conder(tmp_path, monkeypatch):
    """스캔 기록의 QORVO LOT 은 Conder 를 훑기 전에 모은다(Conder 에서 시간이 다 가도 남게)."""
    tool = _tool()
    sr = tmp_path / "AOI-14" / "Scanresult"
    for w in ("GKT7P03-A6", "GKT7P04-D1", "GKT7P05-F4", "GKT7P06-B2"):
        _wafer(sr / "R_Qorvo XP2100_0861004PD_RDL1" / "SETUP" / "MMG" / w, recipe=w.encode())
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    monkeypatch.setattr(tool, "PAIR_LOTS", [])
    monkeypatch.setattr(tool, "KLA_PAIRS", [])
    monkeypatch.setattr(tool, "CONDER_ROW_LOTS", [])
    monkeypatch.setattr(tool, "KNOWN_LOTS", [])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(tmp_path / "conder")])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [("AOI-14", "R_Qorvo XP2100_0861004PD_RDL1", "SETUP", "MMG"),
                                                   ("AOI-12", "R_Qorvo X", "SETUP", "GONE")])
    folder, wafers, _ = tool.run(tmp_path / "desk")
    z = zipfile.ZipFile(next(folder.glob("*.zip")))
    log = z.read("요약.txt").decode()
    assert wafers == tool.QORVO_KNOWN_WAFERS                            # 같은 LOT 을 Job 검색에서 다시 담지 않는다
    assert log.index("QORVO Scanresult LOT") < log.index("QORVO Conder 폴더 못 찾음")
    assert "QORVO LOT 못 찾음: AOI-12" in log
    assert any(n.startswith("QORVO/Scanresult/AOI-14/R_Qorvo XP2100_0861004PD_RDL1/SETUP/MMG/") for n in z.namelist())


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


def test_kla_folder_uses_wafer_start_day_across_midnight(tmp_path, monkeypatch):
    """자정을 넘긴 LOT: LOT 실행은 11-09 시작이어도 웨이퍼 시작이 11-10 이면 11-10 폴더에서 찾는다."""
    tool = _tool()
    lot = "TB500.INT1133@6326"
    (tmp_path / "2026-11-09" / lot / "2026-11-09-23-50_1").mkdir(parents=True)
    want = tmp_path / "2026-11-10" / lot / "2026-11-10-02-08_25"
    want.mkdir(parents=True)
    (tmp_path / "2026-11-10" / lot / "2026-11-10-01-39_22").mkdir()
    monkeypatch.setattr(tool, "kla_bases", lambda: [tmp_path])
    assert tool.find_kla_folder(lot, "2026-11-10-02-08", 25) == want
    assert tool.find_kla_folder(lot, "2026-11-10-02-07", 25) == want      # 1분 어긋나도 같은 슬롯
    assert tool.find_kla_folder(lot, "2026-11-09-22-18", 25) is None      # LOT 실행 시각으로는 못 찾는다


def test_conder_row_lots_take_step_files_and_target_wafer_photo_names(tmp_path, monkeypatch):
    """Conder LOT 은 이름 끝 '(코드)' 로 찾고, 단계 폴더의 맵 · lot · csv 와 대상 웨이퍼 사진 이름만 담는다."""
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    other = base / "1. OTHER" / "3. X" / "5. 6AAA (DDU)"                 # 코드는 같지만 대상 웨이퍼가 없다
    (other / "1. PI" / "ZZZ").mkdir(parents=True)
    lot = base / "9. MSFT KENDALL" / "2. KENDALL A0" / "7. 6BBB (DDU)"
    step = lot / "1. PI6" / "1. OR"
    (step / "E5025794EWF5").mkdir(parents=True)
    (step / "E5025794EWF5" / "PI6_3_5_10.5_20.5.jpg").write_bytes(b"jpg")
    (step / "E5025794EWF5.txt").write_text("DEVICE:K\nROWCT:3\n")
    (step / "NOTME01EWA1").mkdir()
    (step / "NOTME01EWA1" / "PI6_1_1_1_1.jpg").write_bytes(b"jpg")
    (step / "NOTME01EWA1.txt").write_text("DEVICE:K\nROWCT:4\n")
    (lot / "1. PI6" / "6BBB@6412.lot").write_text("Lot")
    (lot / "1. PI6" / "6BBB@6412.csv").write_text("Wafer ID")
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    monkeypatch.setattr(tool, "CONDER_ROW_LOTS", [("DDU", ["E5025794EWF5"]), ("PAD", ["X"])])
    for name in ("PAIR_LOTS", "KLA_PAIRS", "QORVO_KNOWN_LOTS", "KNOWN_LOTS"):
        monkeypatch.setattr(tool, name, [])
    monkeypatch.setattr(tool, "nas_roots", lambda: [])
    folder, wafers, _ = tool.run(tmp_path / "desk")
    z = zipfile.ZipFile(next(folder.glob("*.zip")))
    names, photos = z.namelist(), z.read("사진목록.txt").decode()
    arc = "Conder번호확인/9. MSFT KENDALL/2. KENDALL A0/7. 6BBB (DDU)/1. PI6"
    assert wafers == 1
    assert f"{arc}/6BBB@6412.lot" in names and f"{arc}/6BBB@6412.csv" in names
    assert f"{arc}/1. OR/E5025794EWF5.txt" in names
    assert f"{arc}/1. OR/E5025794EWF5/PI6_3_5_10.5_20.5.jpg" in photos
    assert "NOTME01EWA1/" not in photos                                    # 다른 웨이퍼 사진은 안 담는다
    assert "1. OTHER" not in photos + "".join(names)
    assert "못 찾음: (PAD)" in z.read("요약.txt").decode()
