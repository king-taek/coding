"""[QORVO수집-임시] QORVO 번호 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``qorvo_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② 세 단계(번호상자 · QORVO Scanresult · QORVO Conder)가 정해 둔 작은 파일과 사진 **이름**만 담는 것,
③ zip 을 28 MB 조각으로 나누는 것, ④ 버튼이 로딩바 규칙대로 도는 것.
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
MARK = "[QORVO수집-임시]"
MB = 2 ** 20


def _tool():
    spec = importlib.util.spec_from_file_location("qorvo_collect", ROOT / "dev" / "qorvo_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_collect_code_lives_only_in_the_listed_places():
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    assert set(found) == {"ui/qorvo_collect_tmp.py", "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 2
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2


def test_nothing_outside_the_marks_mentions_the_button():
    for path in APP.rglob("*.py"):
        if path.name == "qorvo_collect_tmp.py":
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[QORVO수집-임시\] 시작.*?# ── \[QORVO수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"qorvo_collect|QORVO_COLLECT", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(QORVO_COLLECT_\w+)", (APP / "ui" / "qorvo_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(QORVO_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def _wafer(d: Path):
    d.mkdir(parents=True)
    for n in ("ProductInfo.ini", "WaferMapRecipe.ini", "WaferInfo.ini", "EquipmentInfo.ini",
              "Params_WaferInfo.ini", "ColorImageGrabingInfo.ini", "Recipe.ini"):
        (d / n).write_text(f"[{n}]\nv={d.as_posix()}\n")   # 내용이 같으면 한 번만 담는다 — 폴더마다 다르게
    (d / "s_DieLocation.dat").write_bytes(b"\x00" * 64)          # 큰 die 맵 — 어느 단계도 담지 않는다
    (d / "Surface.flt").write_bytes(b"\x00" * 152)
    (d / "W_3_4_IC - Other_10_20.jpg").write_bytes(b"jpg")


def _run(tool, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    folder, n, files = tool.run(out)
    zf = zipfile.ZipFile(next(folder.glob("*.zip")))
    return n, files, set(zf.namelist()), zf


def test_three_stages_take_only_small_files_and_photo_names(tmp_path, monkeypatch):
    tool = _tool()
    sr11 = tmp_path / "AOI-11" / "Scanresult"
    sr8 = tmp_path / "AOI-08" / "Scanresult"
    _wafer(sr11 / "R_OTHER_JOB" / "SETUP" / "LOT1" / "W01")                       # 다른 제품도 번호상자에 든다
    _wafer(sr8 / "2D@R2-W97218SGB1K16_0860463PD-0A" / "6321" / "CTX" / "SQ67P01-D1")
    _wafer(sr8 / "2D@R2-W97218SGB1K16_0860463PD-0A" / "6321" / "CTX" / "SQ67P99-A0")
    _wafer(sr8 / "2D@R2-W97218SGB1K16_0860463PD-0A" / "6321" / "CTX" / "SQ67P98-A0")
    con = tmp_path / "1. Conder Scan" / "575. QORVO US" / "44. W97218SGZ4BK16" / "75. 6H7ENK (CTX)" / "1. UBM"
    (con / "SQ67P01-D1").mkdir(parents=True)
    (con / "SQ67P01-D1.txt").write_text("DEVICE:W97218SGZ4BK16\nROWCT:1\nCOLCT:1\nRowData:000\n")
    (con / "6H7ENK@6412.lot").write_text("lot")
    (con / "Thumbs.db").write_bytes(b"db")
    (con / "SQ67P01-D1" / "2D@RE-W97218SGB1K16_0860463PD-0A_CTX_SQ67P01-D1_20_140_IC - Other_1_2.jpg").write_bytes(b"j")
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr8, sr11])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(tmp_path / "1. Conder Scan")])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [])
    n, files, names, zf = _run(tool, tmp_path)

    box = [x for x in names if x.startswith("번호상자/AOI-11/R_OTHER_JOB/SETUP/LOT1/W01/")]
    assert sorted(Path(x).name for x in box) == sorted(tool.BOX_FILES)          # 번호상자: 작은 ini 다섯 개만
    w = "QORVO/Scanresult/AOI-08/2D@R2-W97218SGB1K16_0860463PD-0A/6321/CTX/SQ67P01-D1/"
    assert w + "ColorImageGrabingInfo.ini" in names
    dups = zf.read("중복목록.txt").decode("utf-8")                                 # 번호상자가 먼저 담은 같은 파일은 한 번만
    assert w + "WaferMapRecipe.ini" in names or f"{w}WaferMapRecipe.ini\t= 번호상자/" in dups
    assert not any(x.endswith(("s_DieLocation.dat", "Surface.flt", ".jpg")) for x in names)
    picked = {x.split("/")[6] for x in names if x.startswith("QORVO/Scanresult/") and x.count("/") >= 7}
    assert "SQ67P01-D1" in picked and len(picked) == tool.QORVO_INI_WAFERS     # (1,2) 밀린 웨이퍼를 먼저
    c = "QORVO/Conder/575. QORVO US/44. W97218SGZ4BK16/75. 6H7ENK (CTX)/1. UBM/"
    assert c + "SQ67P01-D1.txt" in names and c + "6H7ENK@6412.lot" in names
    assert c + "Thumbs.db" not in names
    photos = zf.read("사진목록.txt").decode("utf-8")
    assert "SQ67P01-D1_20_140_IC - Other_1_2.jpg" in photos                       # Conder 사진은 이름만
    assert n >= 3 and files > 0


def test_box_stage_takes_several_lots_on_the_first_equipment_only(tmp_path, monkeypatch):
    tool = _tool()
    sr11 = tmp_path / "AOI-11" / "Scanresult"
    sr5 = tmp_path / "AOI-5" / "Scanresult"
    for lot in ("LOT1", "LOT2", "LOT3", "LOT4"):
        _wafer(sr11 / "JOB" / "S" / lot / "W1")
        _wafer(sr5 / "JOB" / "S" / lot / "W1")
    monkeypatch.setattr(tool, "nas_roots", lambda: [sr5, sr11])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    monkeypatch.setattr(tool, "QORVO_KNOWN_LOTS", [])
    _, _, names, _ = _run(tool, tmp_path)
    lots = lambda aoi: {x.split("/")[4] for x in names if x.startswith(f"번호상자/{aoi}/")}   # noqa: E731
    assert len(lots("AOI-11")) == tool.BOX_FIRST_LOTS and not lots("AOI-5")   # 다른 장비는 고칠 곳 수집기가 맡는다


def test_nothing_found_leaves_no_folder(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "nas_roots", lambda: [])
    monkeypatch.setattr(tool, "CONDER_ROOTS", [])
    assert tool.run(tmp_path) == (None, 0, 0)
    assert not list(tmp_path.iterdir())


def test_zip_is_split_under_the_upload_limit():
    tool = _tool()
    sizes = [("요약.txt", 1)] + [(f"f{i}", 10 * MB) for i in range(7)]
    parts = tool.plan_parts(sizes)
    assert parts[0][0] == "요약.txt"
    assert all(sum(dict(sizes)[n] for n in p) <= tool.PART_MB * MB for p in parts)
    assert sorted(n for p in parts for n in p) == sorted(n for n, _ in sizes)


def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import qorvo_collect_tmp as mod

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
            progress(0, 0, "번호상자 AOI-11")
            return tmp_path / "QORVO수집_x", 3, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    win._qorvo_collect_btn.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "12" in said[0] and "QORVO수집_x" in said[0]
    calls = win._loading.calls
    assert calls[0] == ("show", True) and calls[1] == ("progress", 0, 0)   # busy 로 시작
    assert calls[-1] == ("hide",)
