"""[고칠곳수집-임시] 고칠 곳 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``data_mining_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 단계마다 정한 파일만 담고 사진 · 큰 파일은 이름만 남기는 것, ③ 한 단계가 터지거나 폴더를 못 읽어도
나머지를 모아 zip 을 남기는 것, ④ zip 을 28 MB 조각으로 나누는 것, ⑤ 버튼이 로딩바 규칙대로 도는 것.
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
MARK = "[고칠곳수집-임시]"
MB = 2 ** 20


def _tool():
    spec = importlib.util.spec_from_file_location("data_mining_collect", ROOT / "dev" / "data_mining_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _noop(*_a, **_k):
    return None


def _zip_names(path: Path):
    with zipfile.ZipFile(path) as z:
        return z.namelist(), {n: z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".txt") and "/" not in n}


def test_collect_code_lives_only_in_the_listed_places():
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    assert set(found) == {"ui/data_mining_collect_tmp.py", "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 2
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2


def test_nothing_outside_the_marks_mentions_the_button():
    for path in APP.rglob("*.py"):
        if path.name == "data_mining_collect_tmp.py":
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[고칠곳수집-임시\] 시작.*?# ── \[고칠곳수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"data_mining_collect|DMINE_COLLECT", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(DMINE_COLLECT_\w+)",
                          (APP / "ui" / "data_mining_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(DMINE_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def _flt_md(size: int) -> str:
    return f'<Layout>\n<RecordSize Size="{size}"/>\n</Layout>\n'


def _scan_wafer(w: Path, tool, record: int = 152):
    w.mkdir(parents=True)
    for n in tool.WIDE_FILES:
        (w / n).write_text(f"{w} {n}")
    (w / "Surface.flt.md").write_text(_flt_md(record))
    (w / "Surface.flt").write_bytes(b"\x00" * record)
    (w / "Recipe2-ProductInfo.ini").write_text("[Geometric]\nAlignmentDieCol=2\n")
    (w / "Recipe.ini").write_text("x")
    (w / "Recipe2.ini").write_text("x")
    (w / "P_1_2_x.jpg").write_bytes(b"jpg")


def test_all_jobs_take_one_recent_wafer_and_only_the_listed_files(tmp_path, monkeypatch):
    """최근 LOT 웨이퍼 1장에서 정한 파일(다중 레시피 · QORVO 번호 쓰레드 요청 포함)만, 파일당 상한 안에서 담는다."""
    tool = _tool()
    sr = tmp_path / "AOI-9" / "Scanresult"
    for i, lot in enumerate(("OLD", "NEW")):
        for wid in ("W01", "W02"):
            _scan_wafer(sr / "R_OTHER_PRODUCT" / "SETUP" / lot / wid, tool)
        os.utime(sr / "R_OTHER_PRODUCT" / "SETUP" / lot, (1000 + i, 1000 + i))
    big = sr / "R_OTHER_PRODUCT" / "SETUP" / "NEW" / "W01" / "s_DieLocation.dat"
    big.write_bytes(b"\x00" * (tool.WIDE_FILE_MB * MB + 1))
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_all_jobs(col, _noop, _noop, lambda: False) == 1
    col.close("")
    names, txt = _zip_names(tmp_path / "s.zip")
    base = "모든Job/AOI-9/R_OTHER_PRODUCT/SETUP/NEW/W01"
    got = {n.rsplit("/", 1)[-1] for n in names if n.startswith("모든Job/")}
    assert got == (set(tool.WIDE_FILES) - {"s_DieLocation.dat"}) | {"Recipe2-ProductInfo.ini"}
    assert {"WaferMapRecipe.ini", "WaferInfo.ini", "EquipmentInfo.ini", "DieLut.dat"} <= got
    assert all(n.startswith(base) for n in names if n.startswith("모든Job/"))     # 최근 LOT · 1장만
    assert f"{base}/s_DieLocation.dat\t{tool.WIDE_FILE_MB * MB + 1}\t뺌" in txt["파일목록.txt"]
    assert f"{base}/Surface.flt\t152\t이름만" in txt["파일목록.txt"]
    assert "P_1_2_x.jpg" not in txt["사진목록.txt"]                                # 이 단계는 사진 이름도 안 적는다


def test_all_jobs_take_wafer2table_from_wafer_and_parents_up_to_the_job(tmp_path, monkeypatch):
    """블록 제품: 웨이퍼 폴더 · 부모 2단계(Job 폴더까지)의 Wafer2Table.ini 를 같은 경로로 담는다."""
    tool = _tool()
    sr = tmp_path / "AOI-14" / "Scanresult"
    job = sr / "R_BLOCK"
    _scan_wafer(job / "SETUP" / "1104_X" / "GIX1", tool)
    (job / "SETUP" / "1104_X" / "GIX1" / "Wafer2Table.ini").write_text("wafer")
    (job / "SETUP" / "1104_X" / "Wafer2Table.ini").write_text("lot")
    (job / "SETUP" / "Wafer2Table.ini").write_text("setup")
    (job / "Wafer2Table.ini").write_text("job")           # 부모 3단계 — 앱이 보지 않는다
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    col = tool.Collector(tmp_path / "s.zip")
    tool.collect_all_jobs(col, _noop, _noop, lambda: False)
    col.close("")
    names, _ = _zip_names(tmp_path / "s.zip")
    base = "모든Job/AOI-14/R_BLOCK"
    got = {n for n in names if n.endswith("Wafer2Table.ini")}
    assert got == {f"{base}/SETUP/1104_X/GIX1/Wafer2Table.ini", f"{base}/SETUP/1104_X/Wafer2Table.ini",
                   f"{base}/SETUP/Wafer2Table.ini"}


def test_all_jobs_visit_each_equipment_newest_job_then_each_name_once(tmp_path, monkeypatch):
    """장비마다 가장 최근 Job 은 이름이 겹쳐도 보고(152 가 아닌 장비를 찾게), 나머지는 같은 이름을 한 번만 본다."""
    tool = _tool()
    roots = {aoi: tmp_path / aoi / "Scanresult" for aoi in ("AOI-02", "AOI-12")}
    plan = [("AOI-02", "R_SAME", 3000, 152), ("AOI-02", "R_ONLY2", 2000, 152),
            ("AOI-12", "R_SAME", 2500, 120), ("AOI-12", "R_ONLY2", 1500, 120), ("AOI-12", "R_OLD12", 1000, 120)]
    for aoi, job, t, rec in plan:
        _scan_wafer(roots[aoi] / job / "SETUP" / "L1" / "W01", tool, rec)
        os.utime(roots[aoi] / job, (t, t))
    monkeypatch.setattr(tool, "nas_roots", lambda: list(roots.values()))
    odd = set()
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_all_jobs(col, _noop, _noop, lambda: False, odd=odd) == 4
    col.close("")
    names, _ = _zip_names(tmp_path / "s.zip")
    seen = {tuple(n.split("/")[1:3]) for n in names if n.startswith("모든Job/")}
    assert seen == {("AOI-02", "R_SAME"), ("AOI-12", "R_SAME"), ("AOI-02", "R_ONLY2"), ("AOI-12", "R_OLD12")}
    assert odd == {"AOI-12"}


def test_all_jobs_stop_at_the_zip_budget(tmp_path, monkeypatch):
    tool = _tool()
    sr = tmp_path / "AOI-3" / "Scanresult"
    for job in ("R_A", "R_B", "R_C"):
        _scan_wafer(sr / job / "SETUP" / "L1" / "W01", tool)
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr])
    monkeypatch.setattr(tool, "WIDE_ZIP_MB", 0)
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_all_jobs(col, _noop, _noop, lambda: False) == 1
    col.close("")


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
    assert tool.collect_odd_flt(col, _noop, _noop, lambda: False, odd={"AOI-5"}) == 2
    col.close("")
    names, _ = _zip_names(tmp_path / "s.zip")
    jobs = {n.split("/")[2] for n in names if n.startswith("구형flt/")}
    assert jobs == {"R_B_PI4", "R_C_LIVE"}                                    # PI · LIVE 먼저, 최대 2개
    assert "구형flt/AOI-05/R_B_PI4/SETUP/L1/W01/RTP.txt" in names
    assert not any(n.endswith(("Recipe.ini", "Params_WaferInfo.ini")) or "AOI-07" in n for n in names)
    assert any(p.endswith(".jpg") for p in col.photos)


def _conder(base: Path):
    dev = base / "575. QORVO US" / "1. W96760"
    two = dev / "3. 6G80DD (THV)"
    (two / "2. FVI" / "1. OR" / "GWT2P24-A3").mkdir(parents=True)              # 사진만, 맵 없음
    (two / "2. FVI" / "1. OR" / "GWT2P24-A3" / "FVI_1_2_3_4.jpg").write_bytes(b"jpg")
    (two / "2. FVI" / "1. OR" / "Thumbs.db").write_bytes(b"thumb")
    (two / "2. FVI" / "2. 재 AOI" / "GWT2P24-A3").mkdir(parents=True)
    (two / "2. FVI" / "2. 재 AOI" / "GWT2P24-A3.txt").write_text("DEVICE:W96760\n")
    (two / "2. FVI" / "2. 재 AOI" / "6G80DD.lot").write_text("lot")
    one = dev / "4. 6H11AA (TWO)"
    (one / "1. FVI" / "GXX01-A1 memo").mkdir(parents=True)
    (one / "1. FVI" / "GXX01-A1 memo" / "FVI_3_4_5_6.jpg").write_bytes(b"jpg")
    (one / "1. FVI" / "GXX01-A1.txt").write_text("DEVICE:W96760\n")


def test_conder_siblings_take_lots_whose_wafer_is_in_two_steps(tmp_path, monkeypatch):
    """같은 웨이퍼가 두 단계에 나오는 LOT 만 담고(맵 없이 사진만 있는 단계 폴더 포함), 본 LOT 은 모두 이름목록에 적는다."""
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    _conder(base)
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    col = tool.Collector(tmp_path / "s.zip")
    assert tool.collect_conder_siblings(col, _noop, _noop, lambda: False) == 1
    col.close("")
    names, txt = _zip_names(tmp_path / "s.zip")
    arc = "Conder옆폴더/575. QORVO US/1. W96760/3. 6G80DD (THV)/2. FVI"
    assert f"{arc}/2. 재 AOI/GWT2P24-A3.txt" in names and f"{arc}/2. 재 AOI/6G80DD.lot" in names
    assert f"{arc}/1. OR/GWT2P24-A3/FVI_1_2_3_4.jpg" in col.photos
    assert not any("6H11AA" in n or "Thumbs" in n for n in names)
    assert re.search(r"GWT2P24-A3\.txt\t\d+\t담음\t\d{4}-\d\d-\d\d \d\d:\d\d", txt["파일목록.txt"])   # 수정 시각
    listed = txt["Conder이름목록.txt"]
    assert f"담음\t{arc}/2. 재 AOI\t맵 1: GWT2P24-A3.txt\t사진 폴더 1: GWT2P24-A3" in listed
    assert ("이름만\tConder옆폴더/575. QORVO US/1. W96760/4. 6H11AA (TWO)/1. FVI\t맵 1: GXX01-A1.txt"
            "\t사진 폴더 1: GXX01-A1 memo") in listed                      # 03: 폴더 이름이 맵 이름과 다른 예


def test_conder_kla_folder_info_file_is_taken_photos_stay_names(tmp_path, monkeypatch):
    """사진이 있는 KLA 폴더의 정보파일(.001 · 확장자 없음 · 헤더 WaferID)만 담고 사진 · Thumbs.db · 무관한 파일은 안 담는다.
    옆 단계 LOT 이 아니어도 담는다."""
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    lot = base / "100. CUST" / "1. DEV" / "2. L1 (LOT)"
    k1 = lot / "1. FVI" / "2026-08-02-23-09_4"
    k2 = lot / "1. FVI" / "2026-08-02-23-10_5"
    for k in (k1, k2):
        k.mkdir(parents=True)
        (k / "00NUR_2_0_23_2.jpg").write_bytes(b"jpg")
        (k / "Thumbs.db").write_bytes(b"thumb" * 10)
    (k1 / "00NUR031.001").write_text('FileVersion 1 7;\nWaferID "00NUR031XYE5";\nSlot 4;\n')
    (k2 / "00NUR032").write_text('WaferID "00NUR032XYE5";\n')                  # 확장자 없음
    (k2 / "notes.txt").write_text("memo")                                       # 헤더 없음 → 정보파일 아님
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    col = tool.Collector(tmp_path / "s.zip")
    tool.collect_conder_siblings(col, _noop, _noop, lambda: False)
    col.close("")
    names, _ = _zip_names(tmp_path / "s.zip")
    arc = "Conder옆폴더/100. CUST/1. DEV/2. L1 (LOT)/1. FVI"
    assert {n for n in names if n.startswith("Conder옆폴더/")} == {
        f"{arc}/2026-08-02-23-09_4/00NUR031.001", f"{arc}/2026-08-02-23-10_5/00NUR032"}
    assert f"{arc}/2026-08-02-23-09_4/00NUR_2_0_23_2.jpg" in col.photos          # 정보파일만으론 좌표를 사진에 못 잇는다
    assert f"{arc}/2026-08-02-23-10_5/00NUR_2_0_23_2.jpg" in col.photos


def test_unreadable_folder_is_not_a_wafer(tmp_path, monkeypatch):
    """Windows 의 is_file 은 권한 · 네트워크 오류를 올린다 — 그 폴더만 건너뛴다."""
    tool = _tool()

    def deny(self):
        raise PermissionError(13, "denied", str(self))

    monkeypatch.setattr(Path, "is_file", deny)
    assert tool.is_wafer(tmp_path) is False


def test_a_failing_stage_does_not_lose_the_others(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    _conder(base)

    def boom(*_a, **_k):
        raise OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다")

    monkeypatch.setattr(tool, "collect_all_jobs", boom)
    monkeypatch.setattr(tool, "nas_roots", lambda: [])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    folder, wafers, files = tool.run(tmp_path / "desk")
    assert folder is not None and folder.name.startswith("고칠곳수집_") and files == 2
    zips = list(folder.glob("*.zip"))
    assert len(zips) == 1 and not (folder / "_작업중.zip").exists()
    names, txt = _zip_names(zips[0])
    assert any(n.startswith("Conder옆폴더/") for n in names)
    assert "모든 Job: 오류로 이 단계를 멈추고 다음 단계로 — OSError" in txt["요약.txt"]


def test_nothing_found_leaves_no_folder(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "nas_roots", lambda: [])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    out = tmp_path / "desk"
    assert tool.run(out) == (None, 0, 0)
    assert not any(out.iterdir())


def test_stage_limits_fit_inside_the_total():
    """전체 시간 상한은 단계 시간 상한의 합보다 길어야 마지막 단계가 잘리지 않는다."""
    tool = _tool()
    assert tool.MINUTES > tool.WIDE_MINUTES + tool.ODD_MINUTES + tool.SIB_MINUTES
    assert tool.PART_MB == 28


def test_plan_parts_fills_each_part_between_23_and_28_mb():
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

    from aoi_verification.app.ui import data_mining_collect_tmp as mod

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
            return tmp_path / "고칠곳수집_x", 3, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    win._dmine_collect_btn.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "12" in said[0] and "고칠곳수집_x" in said[0]
    calls = win._loading.calls
    assert calls[0] == ("show", True) and calls[1] == ("progress", 0, 0)   # busy 로 시작
    assert calls[-1] == ("hide",)
