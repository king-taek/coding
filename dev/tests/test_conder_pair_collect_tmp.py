"""[Conder짝수집-임시] Conder 짝 자료 수집 버튼 — 버튼을 지울 때 이 파일도 함께 지운다.

지키는 것: ① 지울 곳이 ``conder_pair_collect_tmp.py`` docstring 의 목록 그대로라는 것,
② KLA 단계와 Camtek 단계가 함께 있는 LOT 만 담고 정보파일 · 맵 txt 는 담되 사진 · 큰 파일은 이름만 남기는 것,
③ 맞는 장치가 없어도 장치 목록이 남고 폴더를 못 읽거나 한 단계가 터져도 zip 을 남기는 것,
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
MARK = "[Conder짝수집-임시]"
MB = 2 ** 20
JPG = b"\xff\xd8IMAGEBYTES-MUST-NOT-BE-COPIED"


def _tool():
    spec = importlib.util.spec_from_file_location("conder_pair_collect", ROOT / "dev" / "conder_pair_collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _noop(*_a, **_k):
    return None


def _zip_all(folder: Path):
    """폴더 안 zip 전부 → (이름들, 최상위 txt/tsv 내용, 전체 바이트)."""
    names, txt, blob = [], {}, b""
    for p in sorted(folder.glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                names.append(n)
                data = z.read(n)
                blob += data
                if "/" not in n and n.endswith((".txt", ".tsv")):
                    txt[n] = data.decode("utf-8")
    return names, txt, blob


def test_collect_code_lives_only_in_the_listed_places():
    found = {}
    for path in APP.rglob("*.py"):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if MARK in ln]
        if lines:
            found[path.relative_to(APP).as_posix()] = lines
    assert set(found) == {"ui/conder_pair_collect_tmp.py", "ui/conder_pair_label_tmp.py",
                          "ui/main_window.py", "i18n/ko.py"}
    assert len(found["ui/main_window.py"]) == 4          # 수집 버튼 2줄 + 확인 버튼 2줄
    assert all(ln.rstrip().endswith("이 줄 삭제") for ln in found["ui/main_window.py"])
    assert len(found["i18n/ko.py"]) == 2


def test_nothing_outside_the_marks_mentions_the_button():
    for path in APP.rglob("*.py"):
        if path.name in ("conder_pair_collect_tmp.py", "conder_pair_label_tmp.py"):
            continue
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"# ── \[Conder짝수집-임시\] 시작.*?# ── \[Conder짝수집-임시\] 끝[^\n]*", "", text, flags=re.S)
        text = "\n".join(ln for ln in text.splitlines() if MARK not in ln)
        assert not re.search(r"conder_pair_collect|conder_pair_label|CPAIR_COLLECT|CPAIR_LABEL", text), path


def test_strings_used_by_the_button_all_exist():
    ko = (APP / "i18n" / "ko.py").read_text(encoding="utf-8")
    used = set(re.findall(r"KO\.(CPAIR_COLLECT_\w+)",
                          (APP / "ui" / "conder_pair_collect_tmp.py").read_text(encoding="utf-8")))
    defined = set(re.findall(r"^(CPAIR_COLLECT_\w+)\s*=", ko, flags=re.M))
    assert used and used == defined


def _lot(dev: Path, name: str, with_kla: bool, with_camtek: bool = True, ts: int = 1000):
    lot = dev / name
    if with_kla:
        k = lot / "1. RDL6" / "2026-08-02-23-09_4"
        k.mkdir(parents=True)
        (k / "W1_2_0_23_2.jpg").write_bytes(JPG)
        (k / "W1.001").write_text(f'FileVersion 1 7;\nWaferID "W1";\n{name}\n')
        (k / "Thumbs.db").write_bytes(b"thumb")
    if with_camtek:
        c = lot / "2. PIDS3" / "W1"
        c.mkdir(parents=True)
        (c / "TB500_RDL4_3_5_1200.5_800.2.jpg").write_bytes(JPG)
        (c / "TB500_RDL4_4_5_10.5_80.2.jpg").write_bytes(JPG)
    lot.mkdir(parents=True, exist_ok=True)
    (lot / "W1.txt").write_text(f"ROWCT:5\n{name}\n")
    (lot / "x.lot").write_text(f"lot {name}")
    os.utime(lot, (ts, ts))
    return lot


def _tree(base: Path, n_pair_lots: int = 2):
    dev = base / "100. CUST" / "1. TB500 PRODUCT"
    for i in range(n_pair_lots):
        _lot(dev, f"{i + 1}. L{i} (S)", True, ts=1000 + i)
    _lot(dev, "9. CAMONLY (M)", False, ts=5000)                       # 가장 최근이지만 KLA 단계가 없다
    other = base / "200. OTHER" / "1. ZZZ9"
    _lot(other, "1. O1 (S)", True)
    return base


def _setup(tmp_path, monkeypatch, tool, n=2):
    base = _tree(tmp_path / "1. Conder Scan", n)
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    return base


def test_pair_lots_are_taken_with_info_file_and_map_but_no_image_bytes(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)
    folder, lots, files = tool.run(tmp_path / "desk", _noop)
    assert lots == 2 and files > 0
    names, txt, blob = _zip_all(folder)
    arc = "Conder짝/100. CUST/1. TB500 PRODUCT/1. L0 (S)"
    assert f"{arc}/1. RDL6/2026-08-02-23-09_4/W1.001" in names              # KLA 정보파일
    assert f"{arc}/W1.txt" in names and f"{arc}/x.lot" in names              # LOT 맵 txt · .lot
    assert not any(n.endswith((".jpg", "Thumbs.db")) for n in names)
    assert b"IMAGEBYTES" not in blob
    assert f"{arc}/2. PIDS3/W1/TB500_RDL4_3_5_1200.5_800.2.jpg" in txt["사진목록.txt"]
    assert re.search(r"W1\.001\t\d+\t담음\t\d{4}-\d\d-\d\d \d\d:\d\d", txt["파일목록.txt"])   # 수정 시각
    assert "CAMONLY" not in "\n".join(n for n in names if n.startswith("Conder짝/"))
    assert "ZZZ9" not in "\n".join(names)


def test_summary_lists_every_step_looked_at_including_untaken_lots(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)
    folder, _, _ = tool.run(tmp_path / "desk", _noop)
    _, txt, _ = _zip_all(folder)
    rows = [ln.split("\t") for ln in txt["LOT요약.tsv"].splitlines()[1:]]
    cust = "100. CUST 1. TB500 PRODUCT"
    got = {(r[1].split("/")[-1], r[2], r[3], r[4], r[5], r[6]) for r in rows}
    assert ("1. L0 (S)", "1. RDL6", "KLA", "1", "1", "담음") in got
    assert ("1. L0 (S)", "2. PIDS3", "Camtek", "1", "2", "담음") in got
    assert ("9. CAMONLY (M)", "2. PIDS3", "Camtek", "1", "2", "이름만") in got
    assert all(r[0] == cust for r in rows)                                  # 다른 장치(ZZZ9)는 없다


def test_device_list_is_written_even_without_a_matching_device(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    _lot(base / "200. OTHER" / "1. ZZZ9", "1. O1 (S)", True, ts=2000)
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    assert folder is not None and lots == 0
    names, txt, _ = _zip_all(folder)
    assert re.search(r"200\. OTHER\t1\. ZZZ9\t1\t1970-01-01", txt["Conder장치목록.txt"])
    assert "LOT요약.tsv" in txt and "Conder짝/" not in "\n".join(names)


def test_lots_per_device_and_look_limit_are_respected(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool, n=5)
    monkeypatch.setattr(tool, "LOTS_PER_DEVICE", 3)
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    assert lots == 3
    names, _, _ = _zip_all(folder)
    got = {n.split("/")[3] for n in names if n.startswith("Conder짝/") and n.count("/") > 3}
    assert got == {"5. L4 (S)", "4. L3 (S)", "3. L2 (S)"}                   # 최근 것부터
    monkeypatch.setattr(tool, "LOOK_PER_DEVICE", 1)                         # 가장 최근 1개(CAMONLY)만 훑는다
    folder2, lots2, _ = tool.run(tmp_path / "desk2", _noop)
    assert lots2 == 0 and folder2 is not None


def test_zip_is_split_when_it_grows_past_the_part_size(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)
    monkeypatch.setattr(tool, "PART_MB", 0.0001)
    monkeypatch.setattr(tool, "MIN_PART_MB", 0.00001)
    folder, _, _ = tool.run(tmp_path / "desk", _noop)
    zips = sorted(folder.glob("conder짝_*_*.zip"))
    assert len(zips) >= 2 and not (folder / "_작업중.zip").exists()


def test_output_goes_under_the_date_folder(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)
    folder, _, _ = tool.run(tmp_path / "desk", _noop)
    assert re.fullmatch(r"\d{8}", folder.name) and folder.parent == tmp_path / "desk"
    assert re.fullmatch(r"conder짝_\d{4}\.zip", next(folder.glob("*.zip")).name)
    base = tool.default_out_base()
    assert base.parts[-3:] == ("프로그램 개발중", "분석용 자료 수집", "AOI 레시피 검증")


def test_unreachable_root_does_not_crash(tmp_path, monkeypatch):
    tool = _tool()
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(tmp_path / "없는 공유")])
    out = tmp_path / "desk"
    assert tool.run(out, _noop) == (None, 0, 0)
    assert not any(out.iterdir())


def test_unreadable_info_file_does_not_stop_the_scan(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    _lot(base / "100. CUST" / "1. T254 X", "1. L1 (S)", True)
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    real = Path.open

    def deny(self, *a, **k):
        if self.suffix == ".001":
            raise PermissionError(13, "denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "open", deny)
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    assert folder is not None and lots == 0                                 # 정보파일을 못 읽어 KLA 로 못 알아봄 → 이름만


def test_a_failing_stage_still_leaves_a_zip_with_the_error(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)

    def boom(*_a, **_k):
        raise OSError(64, "지정된 네트워크 이름을 더 이상 사용할 수 없습니다")

    monkeypatch.setattr(tool, "collect_pairs", boom)
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    assert folder is not None and lots == 0
    _, txt, _ = _zip_all(folder)
    assert "짝 LOT: 오류로 이 단계를 멈추고 다음 단계로 — OSError" in txt["요약.txt"]
    assert "100. CUST\t1. TB500 PRODUCT" in txt["Conder장치목록.txt"]


def test_stop_flag_is_honored(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)
    folder, lots, _ = tool.run(tmp_path / "desk", _noop, stop=lambda: True)
    assert lots == 0                                                         # 바로 중지 — 건너뜀 기록만 남는다


def test_camtek_name_rule():
    tool = _tool()
    assert tool.is_camtek_name("TB500_RDL4_3_5_1200.5_800.2")
    assert tool.is_camtek_name("A_B_-1_4_1_2")
    assert not tool.is_camtek_name("W1_2_0_23_2")                           # KLA 이미지 이름은 식별자가 1개


def test_plan_parts_fills_each_part_between_23_and_28_mb():
    tool = _tool()
    sizes = [("요약.txt", 1000)] + [(f"f{i}", s * MB) for i, s in enumerate([12, 7, 9, 4, 6, 3, 2, 8, 5, 1])]
    parts = tool.plan_parts(sizes)
    total = dict(sizes)
    used = [sum(total[n] for n in p) for p in parts]
    assert parts[0][0] == "요약.txt" and tool.PART_MB == 28
    assert all(u <= tool.PART_MB * MB for u in used)
    assert all(u >= tool.MIN_PART_MB * MB for u in used[:-1])
    assert sorted(n for p in parts for n in p) == sorted(total)


def test_button_runs_in_background_and_reports(styled_qapp, monkeypatch, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtWidgets import QMainWindow, QMessageBox

    from aoi_verification.app.ui import conder_pair_collect_tmp as mod

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
        def default_out_base():
            return tmp_path

        @staticmethod
        def run(out, progress=None, stop=None):
            progress(0, 0, "TB500 1. L0 (S)")
            return tmp_path / "20261117", 3, 12

    win = QMainWindow()
    said = []
    monkeypatch.setattr(mod, "_load_tool", lambda: _Tool)
    monkeypatch.setattr(mod.sheets, "ask", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mod.sheets, "info", lambda parent, title, text: said.append(text))
    mod.attach(win)
    win._loading = _Loading()

    win._cpair_collect_btn.click()
    deadline = time.time() + 20
    while not said and time.time() < deadline:
        styled_qapp.processEvents()
    assert said and "3" in said[0] and "12" in said[0] and "20261117" in said[0]
    calls = win._loading.calls
    assert calls[0] == ("show", True) and calls[1] == ("progress", 0, 0)   # busy 로 시작
    assert calls[-1] == ("hide",)
