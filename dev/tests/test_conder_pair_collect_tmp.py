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


def _lot(dev: Path, name: str, with_kla: bool, with_camtek: bool = True, ts: int = 1000, wid: str = "W1",
         kla_wid=None, cam_wid=None):
    """LOT 하나 — KLA 단계 폴더는 웨이퍼 ID 이름, Camtek 단계 폴더도 웨이퍼 ID 이름(둘이 같으면 짝)."""
    lot = dev / name
    if with_kla:
        k = lot / "1. RDL6" / (kla_wid or wid)
        k.mkdir(parents=True)
        (k / f"{wid}_2_0_23_2.jpg").write_bytes(JPG)
        (k / f"{wid}.001").write_text(f'FileVersion 1 7;\nWaferID "{wid}";\n{name}\n')
        (k / "Thumbs.db").write_bytes(b"thumb")
    if with_camtek:
        c = lot / "2. PIDS3" / (cam_wid or wid)
        c.mkdir(parents=True)
        (c / "TB500_RDL4_3_5_1200.5_800.2.jpg").write_bytes(JPG)
        (c / "TB500_RDL4_4_5_10.5_80.2.jpg").write_bytes(JPG)
    lot.mkdir(parents=True, exist_ok=True)
    (lot / f"{wid}.txt").write_text(f"ROWCT:5\n{name}\n")
    (lot / "x.lot").write_text(f"lot {name}")
    os.utime(lot, (ts, ts))
    return lot


def _tree(base: Path, n_pair_lots: int = 2):
    dev = base / "100. CUST" / "1. TB500 PRODUCT"
    for i in range(n_pair_lots):
        _lot(dev, f"{i + 1}. L{i} (S)", True, ts=1000 + i, wid=f"WAFER{i}XX")
    _lot(dev, "9. CAMONLY (M)", False, ts=5000, wid="WCAMONLY")      # 가장 최근이지만 KLA 단계가 없다
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
    assert f"{arc}/1. RDL6/WAFER0XX/WAFER0XX.001" in names                  # KLA 정보파일
    assert f"{arc}/WAFER0XX.txt" in names and f"{arc}/x.lot" in names        # LOT 맵 txt · .lot
    assert not any(n.endswith((".jpg", "Thumbs.db")) for n in names)
    assert b"IMAGEBYTES" not in blob
    assert f"{arc}/2. PIDS3/WAFER0XX/TB500_RDL4_3_5_1200.5_800.2.jpg" in txt["사진목록.txt"]
    assert re.search(r"WAFER0XX\.001\t\d+\t담음\t\d{4}-\d\d-\d\d \d\d:\d\d", txt["파일목록.txt"])   # 수정 시각
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


def test_lots_already_collected_are_skipped_and_noted(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool)
    monkeypatch.setattr(tool, "DONE_LOTS", frozenset({"2. L1 (S)"}))       # 1차에 받은 LOT
    folder, lots, files = tool.run(tmp_path / "desk", _noop)
    names, txt, _ = _zip_all(folder)
    assert lots == 1
    assert not any("2. L1 (S)/" in n and n.startswith("Conder짝/") for n in names)
    assert "이미 받음" in txt["LOT요약.tsv"]


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


def test_bisect_order_spreads_over_time():
    tool = _tool()
    assert tool.bisect_order(0) == [] and tool.bisect_order(1) == [0] and tool.bisect_order(2) == [0, 1]
    assert tool.bisect_order(9) == [0, 8, 4, 2, 6, 1, 3, 5, 7]              # 최근 · 오래됨 · 가운데 · 4분점 …
    assert sorted(tool.bisect_order(37)) == list(range(37))


def test_ids_match_equal_or_contained_with_min_length():
    tool = _tool()
    assert tool.ids_match("42194742EWG5", "42194742EWG5")
    assert tool.ids_match("42194742EWG5", "194742EWG5")                     # 줄어든 폴더 이름(10자)
    assert not tool.ids_match("W1", "W10")                                  # 짧은 쪽이 8자 미만
    assert not tool.ids_match("ABCDEFGH1", "XXABCDEFGH2")


def test_pair_across_devices_and_lots_is_found_and_taken(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    cust = base / "100. CUST"
    _lot(cust / "1. TB500 A", "1. LA (S)", True, wid="WAFERAAAXX")           # 같은 LOT 짝
    _lot(cust / "2. TB500 B", "1. LB (S)", True, with_camtek=False, wid="42194742EWG5")
    _lot(cust / "3. TB500 C", "1. LC (S)", False, with_camtek=True, wid="194742EWG5")
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    names, txt, blob = _zip_all(folder)
    assert lots == 3
    assert "Conder짝/100. CUST/2. TB500 B/1. LB (S)/1. RDL6/42194742EWG5/42194742EWG5.001" in names
    assert "Conder짝/100. CUST/2. TB500 B/1. LB (S)/42194742EWG5.txt" in names   # 단계 · LOT 폴더 파일
    assert "Conder짝/100. CUST/3. TB500 C/1. LC (S)/2. PIDS3/194742EWG5" in txt["사진목록.txt"]
    rows = [ln.split("\t") for ln in txt["짝목록.tsv"].splitlines()[1:]]
    cross = [r for r in rows if r[3] == "0"]
    assert cross == [["100. CUST", "100. CUST/2. TB500 B/1. LB (S)/1. RDL6/42194742EWG5",
                      "100. CUST/3. TB500 C/1. LC (S)/2. PIDS3/194742EWG5", "0", "1"]]
    assert any(r[3] == "1" and r[4] == "1" for r in rows)
    assert b"IMAGEBYTES" not in blob


def test_devices_are_visited_round_robin_even_if_time_runs_out(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    for d in ("1. TB500 A", "2. TB500 B", "3. TB500 C"):
        for i in range(4):
            _lot(base / "100. CUST" / d, f"{i + 1}. L{i} (S)", True, ts=1000 + i, wid=f"W{d[0]}{i}")
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    seen = []
    real = tool._conder_steps

    def spy(d, k=0, out=None):
        if k == 0:
            seen.append(d.parent.name)
        return real(d, k, out)

    monkeypatch.setattr(tool, "_conder_steps", spy)
    folder, _, _ = tool.run(tmp_path / "desk", _noop, stop=lambda: len(seen) >= 3)   # 3개 훑고 중지
    assert seen == ["1. TB500 A", "2. TB500 B", "3. TB500 C"]
    _, txt, _ = _zip_all(folder)
    assert all(d in txt["LOT요약.tsv"] for d in ("TB500 A", "TB500 B", "TB500 C"))


def test_look_limit_per_device_and_time_spread(tmp_path, monkeypatch):
    tool = _tool()
    _setup(tmp_path, monkeypatch, tool, n=5)
    monkeypatch.setattr(tool, "LOOK_PER_DEVICE", 2)                         # 최근 · 가장 오래된 것
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    _, txt, _ = _zip_all(folder)
    seen = {ln.split("\t")[1] for ln in txt["LOT요약.tsv"].splitlines()[1:]}
    assert {s.split("/")[-1] for s in seen} == {"9. CAMONLY (M)", "1. L0 (S)"}
    assert lots == 1


def test_wafers_per_lot_cap(tmp_path, monkeypatch):
    tool = _tool()
    base = tmp_path / "1. Conder Scan"
    dev = base / "100. CUST" / "1. TB500 A"
    lot = dev / "1. L (S)"
    for i in range(5):
        for step, nm in (("1. RDL6", "k"), ("2. PIDS3", "c")):
            w = lot / step / f"WAFERNO{i}XX"
            w.mkdir(parents=True)
            if nm == "k":
                (w / "a.001").write_text('WaferID "X";\n')
                (w / f"WAFERNO{i}XX_2_0_23_2.jpg").write_bytes(JPG)
            else:
                (w / "T_R_3_5_1.5_2.5.jpg").write_bytes(JPG)
    monkeypatch.setattr(tool, "CONDER_ROOTS", [str(base)])
    monkeypatch.setattr(tool, "MAX_WAFERS_PER_LOT", 4)
    folder, lots, _ = tool.run(tmp_path / "desk", _noop)
    names, txt, _ = _zip_all(folder)
    taken = {ln.rsplit("/", 1)[0] for ln in txt["사진목록.txt"].splitlines() if ln}
    assert lots == 1 and len(taken) == 4                                    # 같은 LOT 의 짝 웨이퍼 4개(KLA 2 + Camtek 2)
    rows = [ln.split("\t") for ln in txt["짝목록.tsv"].splitlines()[1:]]
    assert len(rows) == 5 and sum(r[4] == "1" for r in rows) == 2


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
