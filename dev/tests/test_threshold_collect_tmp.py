"""[Threshold수집-임시] Threshold 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``threshold_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 도구가 Kendall · TB500 Job 만 모으고 사진 · 압축 · 큰 파일은 버리는 것, ③ zip 을 28 MB 조각으로
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
    mod.DONE = set()          # 단계별 테스트는 끈 단계도 돌려 본다(끄는 것은 test_done_stages_are_skipped)
    mod.QORVO_INI_DONE = set()
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
    monkeypatch.setattr(tool, "collect_all_jobs", lambda *a, **k: 0)     # 모든 Job 단계는 따로 시험한다
    monkeypatch.setattr(tool, "collect_odd_flt", lambda *a, **k: 0)
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
    monkeypatch.setattr(tool, "collect_all_jobs", lambda *a, **k: 0)     # 모든 Job 단계는 따로 시험한다
    monkeypatch.setattr(tool, "collect_odd_flt", lambda *a, **k: 0)
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
    monkeypatch.setattr(tool, "collect_all_jobs", lambda *a, **k: 0)     # 모든 Job 단계는 따로 시험한다
    monkeypatch.setattr(tool, "collect_odd_flt", lambda *a, **k: 0)
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


@pytest.mark.parametrize("which", ["collect", "repack"])
def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path, which):
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

        @staticmethod
        def repack(out, progress=None, stop=None):
            progress(5, 9, "다시 담는 중 5/9")
            return tmp_path / "Threshold수집_x_다시담음", 5, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    (win._thresh_collector if which == "collect" else win._thresh_repacker).button.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "12" in said[0] and "Threshold수집_x" in said[0]
    assert ("다시담음" in said[0]) == (which == "repack")
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


def test_rich_surface_head_goes_into_its_own_parts_under_the_limit(tmp_path, monkeypatch):
    """한 버튼 수집 안에서 RichSurface 앞부분을 조각으로 담는다.  조각 하나가 zip 조각 하나에 들어가고, 이으면 원본 앞부분이다."""
    tool = _tool()
    w = tmp_path / "LOT" / f"{tool.PAIR_WAFER}_18"
    w.mkdir(parents=True)
    raw = bytes(range(256)) * 400                                         # 102,400 바이트
    (w / "s_RichSurface.dat").write_bytes(raw)
    (w / "s_RichSurface.dat.md").write_text("RecordSize 64")
    (tmp_path / "LOT" / "OTHER01EWA1").mkdir()
    monkeypatch.setattr(tool, "RICH_LOT", str(tmp_path / "LOT"))
    monkeypatch.setattr(tool, "RICH_PART_BYTES", 30_000)
    monkeypatch.setattr(tool, "RICH_PARTS", 3)
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_rich_surface(col, lambda *_: None, lambda *_: None, lambda: False) == 3
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        arc = f"RichSurface/{w.name}"
        got = b"".join(z.read(f"{arc}/s_RichSurface_part{k}.dat") for k in (1, 2, 3))
        assert f"{arc}/s_RichSurface.dat.md" in z.namelist()
    assert got == raw[:90_000]
    assert 27_000_000 + 2_000 < tool.PART_MB * MB                          # 실제 조각(27 MB)도 압축 없이 한도 안


def test_rich_surface_missing_is_only_noted(tmp_path, monkeypatch):
    tool = _tool()
    (tmp_path / "LOT" / "OTHER01EWA1").mkdir(parents=True)
    monkeypatch.setattr(tool, "RICH_LOT", str(tmp_path / "LOT"))
    said = []
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_rich_surface(col, said.append, lambda *_: None, lambda: False) == 0
    col.close("")
    assert said and "못 찾음" in said[0]


def test_qorvo_ini_takes_small_ini_from_every_setup_of_conder_wafers(tmp_path, monkeypatch):
    """EDGE 가 아닌 셋업도 본다.  Conder 와 같은 웨이퍼만, ini 4개와 첫 웨이퍼의 die 맵만 담는다."""
    tool = _tool()
    sr = tmp_path / "AOI-8" / "Scanresult"
    job = sr / "2D@R2-W96721Z5BK16_0858867PD-0A"
    for setup, lot in (("6321", "NPG-EDGE"), ("6110", "NPG"), ("6110", "NPG-RE")):
        (job / setup / "WaferMapRecipe.ini").parent.mkdir(parents=True, exist_ok=True)
        (job / setup / "Setup.ini").write_text(f"[S]\nName={setup}\n")
        for wid in ("GCTAH01-B0", "GCTAH09-F2", "GCTAH17-E6", "ZZZZZ01-A1"):
            w = job / setup / lot / wid
            w.mkdir(parents=True)
            for n in tool.QORVO_INI:
                (w / n).write_text(f"[{n}]\nWafer={wid}\nLot={lot}\n")
            (w / "s_DieLocation.dat").write_bytes(f"{lot}{wid}".encode() * 10)
            (w / "s_DieLocation.dat.md").write_text(f"{lot} {wid}")
            (w / "Surface.flt").write_bytes(b"\x00" * 152)
            (w / "P_1_2_x.jpg").write_bytes(b"jpg")
    (sr / "R_KENDALL_A0_PI5" / "SETUP" / "LOT1" / "W01").mkdir(parents=True)  # 다른 Job 은 안 본다
    (sr / "R_KENDALL_A0_PI5" / "SETUP" / "LOT1" / "W01" / "Recipe.ini").write_text("k")
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    col = tool.Collector(tmp_path / "s.zip")
    wafers = tool.collect_qorvo_ini(col, lambda *_: None, lambda *_: None, lambda: False)
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        names = z.namelist()
    assert wafers == 3 * tool.QORVO_INI_WAFERS
    base = "QORVO/Scanresult/AOI-8/2D@R2-W96721Z5BK16_0858867PD-0A"
    for setup, lot in (("6321", "NPG-EDGE"), ("6110", "NPG"), ("6110", "NPG-RE")):
        first, second = f"{base}/{setup}/{lot}/GCTAH01-B0", f"{base}/{setup}/{lot}/GCTAH09-F2"
        assert all(f"{first}/{n}" in names for n in tool.QORVO_INI + tool.QORVO_DIE_MAP)
        assert all(f"{second}/{n}" in names for n in tool.QORVO_INI)
        assert f"{second}/s_DieLocation.dat" not in names                     # die 맵은 LOT 마다 첫 웨이퍼만
    assert f"{base}/6110/Setup.ini" in names                                  # 셋업 폴더 바로 아래 파일
    assert not any("ZZZZZ" in n or "GCTAH17" in n or n.endswith(".flt") or "KENDALL" in n for n in names)
    assert not col.photos                                                     # 사진 이름도 안 남긴다


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


def test_all_jobs_take_one_recent_wafer_and_only_the_map_files(tmp_path, monkeypatch):
    """모든 Job 에서 최근 LOT 웨이퍼 1장씩, 사진 위치 · die 맵 파일만(파일당 상한 안) 담는다."""
    import os
    tool = _tool()
    sr = tmp_path / "AOI-9" / "Scanresult"
    for job, lots in (("R_OTHER_PRODUCT", ("OLD", "NEW")), ("R_KENDALL_A0_PI5", ("L1",))):
        for i, lot in enumerate(lots):
            for wid in ("W01", "W02"):
                w = sr / job / "SETUP" / lot / wid
                w.mkdir(parents=True)
                for n in tool.WIDE_FILES:
                    (w / n).write_text(f"{job} {lot} {wid} {n}")
                (w / "Recipe.ini").write_text("x")
                (w / "Surface.flt").write_bytes(b"\x00" * 152)
                (w / "P_1_2_x.jpg").write_bytes(b"jpg")
            os.utime(sr / job / "SETUP" / lot, (1000 + i, 1000 + i))
    big = sr / "R_OTHER_PRODUCT" / "SETUP" / "NEW" / "W01" / "s_DieLocation.dat"
    big.write_bytes(b"\x00" * (tool.WIDE_FILE_MB * MB + 1))
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_all_jobs(col, lambda *_: None, lambda *_: None, lambda: False) == 2
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        names = [n for n in z.namelist() if n.startswith("모든Job/")]
    base = "모든Job/AOI-9/R_OTHER_PRODUCT/SETUP/NEW/W01"
    assert f"{base}/ColorImageGrabingInfo.ini" in names and f"{base}/WaferInfo.dat" in names
    assert f"{base}/s_DieLocation.dat" not in names                          # 파일당 상한을 넘는다
    assert not any("/OLD/" in n or "/W02/" in n for n in names)               # 최근 LOT · 1장만
    assert not any(n.endswith((".flt", "Recipe.ini")) for n in names)
    assert any(n.startswith("모든Job/AOI-9/R_KENDALL_A0_PI5/") for n in names)


def _flt_md(size: int) -> str:
    return f'<Layout>\n<RecordSize Size="{size}"/>\n</Layout>\n'


def test_all_jobs_list_flt_by_name_and_find_old_record_sizes(tmp_path, monkeypatch):
    """모든 Job 단계는 Surface.flt.md 를 담고 Surface.flt 는 이름 · 크기만 적는다.  152 가 아닌 장비를 알려 준다."""
    tool = _tool()
    roots = []
    for aoi, size in (("AOI-02", 152), ("AOI-12", 120)):
        sr = tmp_path / aoi / "Scanresult"
        w = sr / "R_JOB" / "SETUP" / "L1" / "W01"
        w.mkdir(parents=True)
        (w / "Params_WaferInfo.ini").write_text("x")
        (w / "Surface.flt").write_bytes(b"\x00" * size)
        (w / "Surface.flt.md").write_text(_flt_md(size))
        roots.append(sr)
    monkeypatch.setattr(tool, "nas_roots", lambda: roots)
    odd = set()
    col = tool.Collector(tmp_path / "s.zip")
    tool.collect_all_jobs(col, lambda *_: None, lambda *_: None, lambda: False, odd=odd)
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        names = z.namelist()
        listing = z.read("파일목록.txt").decode("utf-8")
    assert odd == {"AOI-12"}
    assert "모든Job/AOI-12/R_JOB/SETUP/L1/W01/Surface.flt.md" in names
    assert not any(n.endswith("/Surface.flt") for n in names)
    assert "모든Job/AOI-12/R_JOB/SETUP/L1/W01/Surface.flt\t120\t이름만" in listing


def test_odd_flt_takes_pi_live_jobs_first_with_photo_names(tmp_path, monkeypatch):
    tool = _tool()
    sr = tmp_path / "AOI-05" / "Scanresult"
    for job in ("R_A_RDL", "R_B_PI4", "R_C_LIVE", "R_D_UBM"):
        w = sr / job / "SETUP" / "L1" / "W01"
        w.mkdir(parents=True)
        for n in tool.ODD_FILES:
            (w / n).write_text(f"{job} {n}")
        (w / "Params_WaferInfo.ini").write_text(f"{job} params")
        (w / "Recipe.ini").write_text("x")
        (w / "W01_1_2_Surface Dark_3_4.jpg").write_bytes(b"jpg")
    other = tmp_path / "AOI-07" / "Scanresult" / "R_E_PI" / "SETUP" / "L1" / "W01"   # 152 장비는 안 본다
    other.mkdir(parents=True)
    (other / "Params_WaferInfo.ini").write_text("x")
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr, tmp_path / "AOI-07" / "Scanresult"])
    monkeypatch.setattr(tool, "ODD_JOBS", 2)
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_odd_flt(col, lambda *_: None, lambda *_: None, lambda: False, {"AOI-5"}) == 2
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        names = z.namelist()
    jobs = {n.split("/")[2] for n in names if n.startswith("구형flt/")}
    assert jobs == {"R_B_PI4", "R_C_LIVE"}                                    # PI · LIVE 먼저, 최대 2개
    assert "구형flt/AOI-05/R_B_PI4/SETUP/L1/W01/RTP.txt" in names
    assert not any(n.endswith(("Recipe.ini", "Params_WaferInfo.ini")) or "AOI-07" in n for n in names)
    assert any(p.endswith(".jpg") for p in col.photos)


def test_conder_siblings_take_lots_whose_wafer_is_in_two_steps(tmp_path, monkeypatch):
    """같은 웨이퍼가 두 단계에 나오는 LOT 만, 맵 없이 사진만 있는 단계 폴더도 포함해 담는다."""
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    dev = base / "575. QORVO US" / "1. W96760"
    two = dev / "3. 6G80DD (THV)"
    (two / "2. FVI" / "1. OR" / "GWT2P24-A3").mkdir(parents=True)              # 사진만, 맵 없음
    (two / "2. FVI" / "1. OR" / "GWT2P24-A3" / "FVI_1_2_3_4.jpg").write_bytes(b"jpg")
    (two / "2. FVI" / "1. OR" / "Thumbs.db").write_bytes(b"thumb")
    (two / "2. FVI" / "2. 재 AOI" / "GWT2P24-A3").mkdir(parents=True)
    (two / "2. FVI" / "2. 재 AOI" / "GWT2P24-A3.txt").write_text("DEVICE:W96760\n")
    (two / "2. FVI" / "2. 재 AOI" / "6G80DD.lot").write_text("lot")
    one = dev / "4. 6H11AA (TWO)"
    (one / "1. FVI" / "GXX01-A1").mkdir(parents=True)
    (one / "1. FVI" / "GXX01-A1.txt").write_text("DEVICE:W96760\n")
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_conder_siblings(col, lambda *_: None, lambda *_: None, lambda: False) == 1
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        names = z.namelist()
        listing = z.read("파일목록.txt").decode("utf-8")
    arc = "Conder옆폴더/575. QORVO US/1. W96760/3. 6G80DD (THV)/2. FVI"
    assert f"{arc}/2. 재 AOI/GWT2P24-A3.txt" in names and f"{arc}/2. 재 AOI/6G80DD.lot" in names
    assert f"{arc}/1. OR/GWT2P24-A3/FVI_1_2_3_4.jpg" in col.photos
    assert not any("6H11AA" in n or "Thumbs" in n for n in names)
    assert re.search(r"GWT2P24-A3\.txt\t\d+\t담음\t\d{4}-\d\d-\d\d \d\d:\d\d", listing)       # 수정 시각


def test_qorvo_ini_prefers_wafers_from_the_shifted_scan(tmp_path, monkeypatch):
    tool = _tool()
    sr = tmp_path / "AOI-11" / "Scanresult"
    lot = sr / "2D@RE-W97218SGB1K16_0860463PD-0A" / "6321" / "FVI"
    for wid in ("SQ62G01-A1", "SQ62G02-B2", "SQ67P13-E6"):
        (lot / wid).mkdir(parents=True)
        for n in tool.QORVO_INI:
            (lot / wid / n).write_text(f"{wid} {n}")
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    col = tool.Collector(tmp_path / "s.zip")
    tool.collect_qorvo_ini(col, lambda *_: None, lambda *_: None, lambda: False)
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        wafers = {n.split("/")[-2] for n in z.namelist() if n.endswith("ProductInfo.ini")}
    assert wafers == {"SQ67P13-E6", "SQ62G01-A1"}                             # 밀린 스캔의 웨이퍼 먼저, 2장


def test_conder_must_lot_is_taken_whole_and_only_once(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    lot = base / "575. QORVO US" / "44. W97218SGZ4BK16" / "75. 6H7ENK (CTX)"
    for step in ("1. UBM", "2. FVI"):
        (lot / step / "SQ67P13-E6").mkdir(parents=True)
        (lot / step / "SQ67P13-E6.txt").write_text(f"DEVICE:W97218 {step}\n")
        (lot / step / "SQ67P13-E6" / f"{step[3:]}_1_2_3_4.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_conder_must(col, lambda *_: None, lambda *_: None) == 1
    assert tool.collect_conder_siblings(col, lambda *_: None, lambda *_: None, lambda: False) == 0   # 두 번 담지 않는다
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        names = z.namelist()
    arc = "Conder옆폴더/575. QORVO US/44. W97218SGZ4BK16/75. 6H7ENK (CTX)"
    assert f"{arc}/2. FVI/SQ67P13-E6.txt" in names and f"{arc}/1. UBM/SQ67P13-E6.txt" in names
    assert f"{arc}/2. FVI/SQ67P13-E6/FVI_1_2_3_4.jpg" in col.photos


def test_qorvo_ini_skips_jobs_already_taken(tmp_path, monkeypatch):
    tool = _tool()
    tool.QORVO_INI_DONE = {"W96721Z5BK16"}
    w = tmp_path / "AOI-8" / "Scanresult" / "2D@R2-W96721Z5BK16_0858867PD-0A" / "6321" / "NPG" / "GCTAH01-B0"
    w.mkdir(parents=True)
    (w / "ProductInfo.ini").write_text("x")
    monkeypatch.setattr(tool, "nas_roots", lambda: [tmp_path / "AOI-8" / "Scanresult"])
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_qorvo_ini(col, lambda *_: None, lambda *_: None, lambda: False) == 0
    col.close("")


def test_done_stages_are_skipped(tmp_path, monkeypatch):
    """다 받은 단계(DONE)는 run() 이 건너뛴다 — 다시 모으지 않는다."""
    tool = _tool()
    tool.DONE = {"pair", "rich", "conder_rows", "kla", "qorvo", "known"}
    called = []
    for name in ("collect_rich_surface", "collect_conder_rows", "collect_kla_pairs", "collect_qorvo",
                 "collect_qorvo_ini", "collect_all_jobs", "collect_odd_flt", "collect_conder_must",
                 "collect_conder_siblings"):
        monkeypatch.setattr(tool, name, lambda *a, _n=name, **k: called.append(_n) or 0)
    monkeypatch.setattr(tool, "resolve_pair", lambda raw: called.append("pair") or None)
    monkeypatch.setattr(tool, "find_lot", lambda *a: called.append("known") or None)
    monkeypatch.setattr(tool, "nas_roots", lambda: [tmp_path])
    tool.run(tmp_path)
    assert set(called) == {"collect_qorvo_ini", "collect_all_jobs", "collect_odd_flt",
                           "collect_conder_must", "collect_conder_siblings"}



def test_all_jobs_take_multi_recipe_files(tmp_path, monkeypatch):
    tool = _tool()
    sr = tmp_path / "AOI-03" / "Scanresult"
    w = sr / "R_MULTI" / "Setup1" / "L1" / "W01"
    w.mkdir(parents=True)
    for n in ("Params_WaferInfo.ini", "Recipe2-ProductInfo.ini", "Recipe1-ProductInfo.ini", "DieLut.dat", "Recipe2.ini"):
        (w / n).write_text(n)
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    col = tool.Collector(tmp_path / "s.zip")
    tool.collect_all_jobs(col, lambda *_: None, lambda *_: None, lambda: False)
    col.close("")
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        got = {n.rsplit("/", 1)[-1] for n in z.namelist() if n.startswith("모든Job/")}
    assert got == {"Params_WaferInfo.ini", "Recipe2-ProductInfo.ini", "Recipe1-ProductInfo.ini", "DieLut.dat"}


def _old_collection(folder: Path):
    """PR #132 판 수집 결과 비슷한 zip 두 조각."""
    folder.mkdir(parents=True)
    scan = "QORVO/Scanresult/AOI-11/2D@RE-W97218SGB1K16_X/6321/FVI/SQ67P13-E6"
    with zipfile.ZipFile(folder / "Threshold수집_x_1.zip", "w") as z:
        z.writestr("요약.txt", "원래 요약")
        z.writestr("RichSurface/W_18/s_RichSurface_part1.dat", b"r" * 100)
        z.writestr(f"{scan}/Params_WaferInfo.ini", "p")
        z.writestr(f"{scan}/ProductInfo.ini", "same")
        z.writestr("비교스캔/AOI-05/YDD/W/Surface.flt", b"f" * 100)
        z.writestr("KLA비교/L/KLA/x.001", "k")
    with zipfile.ZipFile(folder / "Threshold수집_x_2.zip", "w") as z:
        z.writestr("AOI-16/R_KENDALL/SETUP/L/W/ProductInfo.ini", "same-src")
        z.writestr("QORVO/Conder/1. W97218/75. 6H7ENK (CTX)/2. FVI/SQ67P01-D1.txt", "map")
        z.writestr("QORVO/Conder/2. W96760/9. 6G80DD (THV)/1. OR/SQ67P13-E6.txt", "pair map")
        z.writestr("QORVO/Conder/2. W96760/9. 6G80DD (THV)/1. OR/GXX01-A1.txt", "other")
        z.writestr("사진목록.txt", "비교스캔/AOI-05/YDD/W/a.jpg\nQORVO/Conder/2. W96760/9. 6G80DD (THV)/1. OR/SQ67P13-E6/b.jpg\n")
        z.writestr("파일목록.txt", f"{scan}/ProductInfo.ini\t4\t담음\nKLA비교/L/KLA/x.001\t1\t담음\n")
        z.writestr("중복목록.txt", f"{scan}/Recipe.ini\t= AOI-16/R_KENDALL/SETUP/L/W/ProductInfo.ini\n")


def test_repack_keeps_only_what_is_still_needed(tmp_path, monkeypatch):
    tool = _tool()
    _old_collection(tmp_path / "Threshold수집_x")
    monkeypatch.setattr(tool, "REPACK_SRC", "Threshold수집_x")
    folder, kept, total = tool.repack(tmp_path)
    assert folder == tmp_path / "Threshold수집_x_다시담음"
    names, texts = set(), {}
    for z in folder.glob("*.zip"):
        with zipfile.ZipFile(z) as f:
            names |= set(f.namelist())
            texts.update({n: f.read(n) for n in f.namelist()})
    scan = "QORVO/Scanresult/AOI-11/2D@RE-W97218SGB1K16_X/6321/FVI/SQ67P13-E6"
    assert "RichSurface/W_18/s_RichSurface_part1.dat" in names and f"{scan}/ProductInfo.ini" in names
    assert texts[f"{scan}/Recipe.ini"] == b"same-src"                        # 버린 원본의 내용을 남길 이름으로
    assert "QORVO/Conder/1. W97218/75. 6H7ENK (CTX)/2. FVI/SQ67P01-D1.txt" in names
    assert "QORVO/Conder/2. W96760/9. 6G80DD (THV)/1. OR/SQ67P13-E6.txt" in names   # Scanresult 와 같은 웨이퍼
    assert not any(n.startswith(("비교스캔/", "KLA비교/", "AOI-16/")) or "GXX01" in n for n in names)
    photos = texts["사진목록.txt"].decode("utf-8")
    assert "SQ67P13-E6/b.jpg" in photos and "비교스캔" not in photos
    assert "KLA비교" not in texts["파일목록.txt"].decode("utf-8")
    assert "원래 요약" in texts["요약.txt"].decode("utf-8")
    assert (kept, total) == (6, 9)


def test_repack_without_the_folder_makes_nothing(tmp_path):
    tool = _tool()
    assert tool.repack(tmp_path) == (None, 0, 0)
