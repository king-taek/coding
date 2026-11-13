"""좌표 가설 실험실(``dev/coord_lab.py``) — 저장소 실물 샘플에서 나온 결과를 못 박는다.

도구는 앱 코드와 **독립인 재계산**이다(표준 파이썬만).  여기 단언은 "가설이 옳다" 가 아니라
"이 표본에서 이렇게 나왔다" 다 — 깨지면 샘플이나 도구가 바뀐 것이니 보고서 수치를 다시 본다.
출처 등급: 정답 LIVE = 관측(Conder 파일명) · EXP = 파일(번호표).
"""

import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev"))

import coord_lab as lab  # noqa: E402


@pytest.fixture(scope="module")
def run():
    camtek, kla_files, live = lab.scan(lab.DEFAULT_ROOTS)
    recs = [lab.analyze_camtek(f, live) for f in sorted(camtek)]
    for scope in ("LOT", "PRD"):
        lab.borrow(recs, scope)
    lab.app_check(recs, live)
    klas = [k for k in (lab.analyze_kla(p) for p in sorted(kla_files)) if k]
    return recs, klas, lab.score(recs)


def test_live_name_tokens():
    assert lab.parse_live("2D@RE-W_0860463PD-0A_CTX_SQ67P01-D1_20_140_IC - Other_115_746.jpg") \
        == (20, 140, 115.0, 746.0)
    assert lab.parse_live("00MEU018XYG1_2_0_23_2.jpg") is None        # KLA 사진 이름(식별자 1개)
    assert lab.parse_live("120397.101071.c.1986767493.jpeg") is None


def test_position_grid_is_anchored_at_stage_origin(run):
    """A1 — 위치 쪽은 이미 하나의 식이다: 결함 전부에서 Col == floor(X/pitch_x)."""
    a1 = [r["A1"] for r in run[0] if "A1" in r]
    assert sum(a["n"] for a in a1) > 6000
    assert sum(a["x_bad"] for a in a1) == 0


def test_no_pure_geometry_rule_numbers_every_product(run):
    """번호는 기하만으로 안 나온다 — 계산식 넷 모두 정답(LIVE)과 다른 웨이퍼가 있다."""
    sc = run[2]
    for h in ("C1D", "G0", "G3", "CEN"):
        assert sc[f"{h}|LIVE"]["both"] < sc[f"{h}|LIVE"]["n"], h
    for h in ("EXP", "LUT"):                       # 장비가 적어 둔 표는 전부 맞는다
        assert sc[f"{h}|LIVE"]["both"] == sc[f"{h}|LIVE"]["n"] > 0, h


def test_borrowed_number_position_matches_truth_and_app(run):
    """LOT/PRD — 다른 웨이퍼의 (u, v)를 빌린 번호가 정답과 같고, 앱의 다단 규칙과도 같다.

    ⚠ R2: 정답이 있는 건 고유 웨이퍼 5장 · 제품 2종뿐이다(같은 웨이퍼 사본에서는 빌리지 않는다).  나머지는 '앱과 같다' 일 뿐이다."""
    recs, _, sc = run
    for h in ("LOT", "PRD"):
        assert sc[f"{h}|LIVE"]["both"] == sc[f"{h}|LIVE"]["n"] > 0, h
    # 독립인 빌릴 곳이 없는 웨이퍼(GX57007306 은 사본뿐)는 가설을 내지 않는다 — 자기 답 베끼기 금지.
    assert not any("PRD" in r.get("hyp", {}) for r in recs if r["wafer"] == "GX57007306")
    both = [(r["hyp"]["PRD"], r["hyp"]["APP"]) for r in recs
            if r.get("hyp", {}).get("PRD") and r.get("hyp", {}).get("APP")]
    assert len(both) > 50 and all(tuple(a) == tuple(b) for a, b in both)
    # stage 위 놓인 자리가 한 열 다른 두 스캔(RDL2 AOI-5 col 기준 2 ↔ RDL4 col 기준 1)을 넘나든다.
    assert {tuple(r["hyp"]["PRD"]) for r in recs
            if r.get("hyp", {}).get("PRD") and abs(r["px"] - 37247.9) < 0.01} == {(1, 6)}


def test_app_matches_every_sample_with_truth(run):
    sc = run[2]
    assert sc["APP|LIVE"]["both"] == sc["APP|LIVE"]["n"] > 20


def test_kla_plan_range_follows_center_rule(run):
    k1 = [k["K1"] for k in run[1] if "K1" in k]
    assert len(k1) > 40 and all(all(k) for k in k1)


# ── NAS 순회 모드 ─────────────────────────────────────────────────────────
def _wafer(d: Path, center=True):
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text(
        "[Geometric]\nDiameter=300000\n" + ("Center_X=167949\nCenter_Y=178990\n" if center else "")
        + "[Geometry]\nDieStep_X=37247.9\nDieStep_Y=44905.3\n", encoding="utf-8")
    (d / "ColorImageGrabingInfo.ini").write_text(
        "[79771.95864.c.1.1.jpeg]\nX=79771.1\nY=95864.7\nCol=2\nRow=2\nRecipeNumber=1\n", encoding="utf-8")
    (d / "79771.95864.c.1.1.jpeg").write_bytes(b"")


def test_nas_walk_samples_each_lot_and_collects_only_odd_folders(tmp_path):
    import time
    import zipfile

    root = tmp_path / "AOI-1" / "Scanresult" / "job" / "Setup1"
    for i in range(10):
        _wafer(root / "LOT-A" / f"W{i:02d}")
    _wafer(root / "LOT-B" / "X00", center=False)                 # 중심이 없는 폴더 = 모을 대상
    logs = []
    folders, cut = lab.nas_walk([tmp_path / "AOI-1" / "Scanresult"], time.time() + 60,
                                per_lot=3, lots=5, log=logs.append)
    assert not cut
    assert sorted(f.parent.name for f in folders) == ["LOT-A"] * 3 + ["LOT-B"]   # LOT 마다 몇 장만

    recs = [lab.analyze_camtek(f, {}) for f in sorted(folders)]
    odd = {Path(r["folder"]).name: lab.flags(r) for r in recs if lab.flags(r)}
    assert list(odd) == ["X00"] and "중심 없음" in odd["X00"]
    zips, n = lab.collect_zip(recs, {}, tmp_path / "좌표실험_test")
    assert n == 1 and [z.name for z in zips] == ["좌표실험_test_수집.zip"]
    names = zipfile.ZipFile(zips[0]).namelist()
    assert any(x.endswith("X00/Params_WaferInfo.ini") for x in names)
    assert any(x.endswith("X00/사진목록.txt") for x in names)        # 사진은 이름만
    assert not any(x.lower().endswith(".jpeg") for x in names)


def test_nas_walk_stops_at_deadline(tmp_path):
    _wafer(tmp_path / "LOT" / "W0")
    folders, cut = lab.nas_walk([tmp_path], 0.0, log=lambda *_: None)   # 이미 지난 마감
    assert cut and folders == []


def test_collect_zip_splits_into_parts_under_the_limit(tmp_path):
    """수집 zip 이 한도를 넘으면 따로 풀리는 조각으로 나눈다(사용자 요청: 30 MB)."""
    root = tmp_path / "AOI-1" / "Scanresult" / "job" / "Setup1"
    recs = []
    for i in range(4):
        folder = root / f"LOT-{i}" / "X00"
        _wafer(folder, center=False)
        (folder / "DieLut.dat").write_bytes(os.urandom(400_000))      # 압축되지 않는 0.4 MB
        recs.append(lab.analyze_camtek(folder, {}))
    zips, n = lab.collect_zip(recs, {}, tmp_path / "좌표실험_test", part_mb=1)
    assert n == 4 and len(zips) > 1
    assert [z.name for z in zips[:2]] == ["좌표실험_test_수집.zip", "좌표실험_test_수집_2.zip"]
    assert all(z.stat().st_size <= 2 ** 20 for z in zips)
    names = [x for z in zips for x in zipfile.ZipFile(z).namelist()]     # 조각마다 온전한 zip
    assert sum(x.endswith("/DieLut.dat") for x in names) == 4 and len(names) == len(set(names))


def _conder_tree(root):
    """Conder Scan 흉내: 고객 \\ 디바이스 \\ LOT (S/M) \\ 공정 \\ 웨이퍼 + 파일 보관함."""
    good = root / "531. 고객" / "1. 제품" / "7. E1X000 (ABC)" / "1. FVI" / "GX57007306"
    good.mkdir(parents=True)
    (good / "LOT_GX57007306_3_4_100_200.jpg").write_bytes(b"")
    (good / "LOT_OTHERWAFER_3_4_100_200.jpg").write_bytes(b"")           # 다른 웨이퍼 사진
    for sub in (("531. 고객", "1. 제품", "8. E1X111 (XYZ)", "1. FVI"),      # S/M 이 다른 LOT
                ("0. 전산 X", "PLAT", "1. 보관 (ABC)")):                   # 보관함
        junk = root.joinpath(*sub) / "GX57007306"
        junk.mkdir(parents=True)
        (junk / "LOT_GX57007306_3_4_100_200.jpg").write_bytes(b"")
    stray = good.parent / "9"                                            # 이름이 약한 폴더
    stray.mkdir()
    (stray / "LOT_9_3_4_100_200.jpg").write_bytes(b"")
    return good


def test_conder_is_searched_by_strip_mark_not_by_walking_everything(tmp_path):
    """LOT 폴더 끝의 S/M 으로 바로 간다.  실측(2026-11-10): 이름이 ``9`` 인 폴더를 보관함에서 찾아 웨이퍼로 짚었다."""
    good = _conder_tree(tmp_path)
    index, cut = lab.conder_index([tmp_path], time.time() + 60, log=lambda *_: None)
    assert not cut and {m: len(v) for m, v in index.items()} == {"ABC": 1, "XYZ": 1}   # 보관함은 안 들어간다
    wanted = {"GX57007306": "ABC", "9": "ABC", "NOMARK01": None}
    live, cut = lab.nas_conder([tmp_path], wanted, time.time() + 60, log=lambda *_: None)
    assert not cut and list(live) == [good]
    assert [n for n, _ in live[good]] == ["LOT_GX57007306_3_4_100_200.jpg"]   # ID 토큰이 든 사진만
    assert not lab.strong_id("9") and not lab.strong_id("00123") and lab.strong_id("PGEE48")


def test_strip_mark_comes_from_waferinfo_then_lot_folder_name(tmp_path):
    w = tmp_path / "job" / "CCG" / "W01"
    w.mkdir(parents=True)
    assert lab.strip_mark_of(w) == "CCG"                                  # LOT 폴더 이름(가정)
    (w / "WaferInfo.ini").write_text("[Lot]\nInputLot=gfw-RDL4\n", encoding="utf-8")
    assert lab.strip_mark_of(w) == "GFW"                                  # 파일이 먼저
    other = tmp_path / "job" / "Setup1" / "W01"
    other.mkdir(parents=True)
    assert lab.strip_mark_of(other) is None


def test_stop_ends_the_walk_at_once_and_leaves_no_files(tmp_path):
    """[중지] 는 큰 폴더 한가운데서도 먹고, 찾는 중에 멈추면 결과 파일을 만들지 않는다."""
    _conder_tree(tmp_path)
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 3                                            # 훑기 시작한 직후 중지

    live, cut = lab.nas_conder([tmp_path], {"GX57007306": "ABC"}, stop, log=lambda *_: None)
    assert cut and not live
    out = tmp_path / "out"
    out.mkdir()
    assert lab.run_nas([str(tmp_path)], out, stop=lambda: True) == (None, 0, 0)
    assert list(out.iterdir()) == []


def test_stopping_midway_keeps_what_was_already_computed(tmp_path):
    """실측(2026-11-10): LOT 3000개를 찾은 뒤 멈췄더니 아무것도 남지 않았다 — 찾는 즉시 계산해 남긴다."""
    root = tmp_path / "AOI-1" / "Scanresult" / "job"
    for lot in ("Setup1", "Setup2", "Setup3"):
        for i in range(3):
            _wafer(root / lot / "CCG" / f"WAFER{i:02d}")
    out = tmp_path / "out"
    out.mkdir()
    seen = []

    def progress(done, total, message):
        seen.append(message)

    def stop():                                    # 첫 LOT 계산이 끝난 뒤 중지
        return any(m.startswith("웨이퍼 ") for m in seen)

    base, wafers, _odd = lab.run_nas([str(tmp_path / "AOI-1" / "Scanresult")], out, progress=progress, stop=stop)
    assert 0 < wafers < 9                          # 일부만 돌았고
    (z,) = out.glob("*.zip")                       # 그만큼은 남았다 — 결과물은 zip 하나뿐(사용자 요청 2026-11-12)
    assert base is not None and sorted(p.name for p in out.iterdir()) == [z.name]
    assert {Path(str(base)).name + e for e in (".txt", ".json", ".html")} <= set(zipfile.ZipFile(z).namelist())
    assert any("남은 시간" in m for m in seen)      # 진행 문구가 계속 바뀐다


# ───────── 2026-11-11 NAS 1차 수집에서 나온 것 ─────────
def _block_wafer(d: Path, extra_ini="", prod_extra="", recipe=""):
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text(
        "[Geometry]\nDieStep_X=23003.7\nDieStep_Y=31831.7\n", encoding="utf-8")
    (d / "ProductInfo.ini").write_text(
        "[Geometric]\nColsInBlock=18\nRowsInBlock=23\n" + prod_extra, encoding="utf-8")
    (d / "ColorImageGrabingInfo.ini").write_text(
        extra_ini + "[78889.195422.c._1_.jpeg]\nX=78889.3504102319\nY=195422.326939756\nCol=61\nRow=141\n"
        "[64769.194066.c._2_.jpeg]\nX=64769.0133078009\nY=194066.420101495\nCol=50\nRow=140\n", encoding="utf-8")
    if recipe:
        (d / "Recipe.ini").write_text(recipe, encoding="utf-8")


def test_numbering_pitch_is_step_divided_by_dies_in_block(tmp_path):
    """실물(Qorvo XP2100, 파일): 스캔 칸 23003.7×31831.7 안에 번호 die 가 18×23 개 —
    ``Col == floor(X / (DieStep/ColsInBlock))`` 가 397건 전부 맞았고 DieStep 그대로는 전부 틀렸다."""
    _block_wafer(tmp_path / "LOT" / "W1")
    r = lab.analyze_camtek(tmp_path / "LOT" / "W1", {})
    assert r["block"] == (18, 23) and r["px"] == pytest.approx(23003.7 / 18) and r["py"] == pytest.approx(31831.7 / 23)
    assert r["A1"]["x_bad"] == 0 and r["A1"]["y_diff"] == {"0": [0]}
    assert "Col≠floor(X/pitch)" not in lab.flags(r)


def test_alignment_photos_are_not_counted_as_defects(tmp_path):
    """실물(TB500 EDC AOI-28, 파일): ``.wlup.jpeg``(Type=18, Col=0·Row=0) 정렬 사진 2장이 검산을 깼다."""
    wlup = "[148988.179602.wlup.jpeg]\nX=148988.2979\nY=179602.9125\nCol=0\nRow=0\nType=18\n"
    _block_wafer(tmp_path / "LOT" / "W1", extra_ini=wlup)
    r = lab.analyze_camtek(tmp_path / "LOT" / "W1", {})
    assert r["A1"]["n"] == 2 and r["A1"]["x_bad"] == 0 and r["skipped_align"] == 1


def _shift_wafer(d: Path, export: bool):
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text("[Geometry]\nDieStep_X=1000\nDieStep_Y=1000\n", encoding="utf-8")
    (d / "ProductInfo.ini").write_text(
        "[ExportData]\ncMax=11\nrMax=7\ncMin=2\nrMin=2\n" if export else "[General]\nA=1\n", encoding="utf-8")
    (d / "Recipe.ini").write_text("[Display]\nDisplayRowShift=-3\nDisplayColShift=-1\n", encoding="utf-8")
    (d / "ColorImageGrabingInfo.ini").write_text(
        "[a.c.1.jpeg]\nX=5100\nY=3200\nCol=5\nRow=3\n[b.c.2.jpeg]\nX=6300\nY=4400\nCol=6\nRow=4\n", encoding="utf-8")
    return {d: [(n, lab.parse_live(n)) for n in ("L_T_W_3_4_100_200.jpg", "L_T_W_4_3_300_400.jpg")]}


def test_live_names_are_read_as_written_when_the_number_table_agrees(tmp_path):
    """실물(KENDALL A0 PI3, 관측 10표): 화면 시프트가 (−1,−3)인데 LIVE 이름은 번호표 (2,7) 그대로였다.
    T254 FS UBM 은 시프트를 더한 이름이었다 — 번호표가 없으면 둘을 가를 수 없으니 그렇게 적는다."""
    live = _shift_wafer(tmp_path / "A" / "W", export=True)
    r = lab.analyze_camtek(tmp_path / "A" / "W", live)
    assert r["truth_live"]["raw"] == (2, 7) and r["truth_live"]["value"] == (2, 7)
    assert r["truth_live"]["shift_mode"] == "안 더함" and "번호표≠LIVE" not in lab.flags(r)
    live = _shift_wafer(tmp_path / "B" / "W", export=False)
    r = lab.analyze_camtek(tmp_path / "B" / "W", live)
    assert r["truth_live"]["raw"] == (2, 7) and r["truth_live"]["value"] == (1, 10)     # 예전 해석 유지
    assert r["truth_live"]["shift_mode"] == "미구분"


_MAP_TXT = ("DEVICE:AST254\nWAFER:GX57007306\nFNLOC:180\nROWCT:2\nCOLCT:3\nXDIES:14.28\nYDIES:30.99\n"
            "RowData:___ 000 000\nRowData:000 003 ___\n")


def test_conder_map_text_is_collected_with_a_file_list(tmp_path):
    """Conder 만으로 맵을 그리려면 LOT 폴더의 ``<WaferID>.txt`` 가 필요하다(사용자 요청 2026-11-11)."""
    good = _conder_tree(tmp_path)
    lot = good.parent.parent
    (lot / "GX57007306.txt").write_text(_MAP_TXT, encoding="utf-8")
    (lot / "OTHERWAFER.txt").write_text(_MAP_TXT, encoding="utf-8")
    (lot / "요약.xlsx").write_bytes(b"x")
    extra = {}
    lab.nas_conder([tmp_path], {"GX57007306": "ABC"}, time.time() + 60, log=lambda *_: None, extra=extra)
    assert list(extra["maps"]) == [lot / "GX57007306.txt"]
    assert sorted(extra["files"][lot]) == ["GX57007306.txt", "OTHERWAFER.txt", "요약.xlsx"]
    info = lab.conder_map_info(extra["maps"][lot / "GX57007306.txt"])
    assert info == {"FNLOC": 180.0, "ROWCT": 2, "COLCT": 3, "XDIES": 14.28, "YDIES": 30.99,
                    "rows": 2, "cols": 3, "dies": 4, "bad": 1}
    zips, n = lab.collect_zip([], {}, tmp_path / "좌표실험_c", conder=extra)        # 이상 폴더가 없어도 담는다
    names = zipfile.ZipFile(zips[0]).namelist()
    assert n == 0 and any(x.startswith("Conder/") and x.endswith("GX57007306.txt") for x in names)
    listing = zipfile.ZipFile(zips[0]).read("Conder/파일목록.txt").decode("utf-8")
    assert "요약.xlsx" in listing


def test_conder_map_is_compared_with_the_camtek_wafer(tmp_path):
    live = _shift_wafer(tmp_path / "A" / "GX57007306", export=True)
    r = lab.analyze_camtek(tmp_path / "A" / "GX57007306", live)
    lab.attach_conder_maps(r, {"maps": {tmp_path / "x" / "GX57007306.txt": _MAP_TXT}})
    m = r["conder_map"][0]
    assert m["COLCT"] == 3 and m["exp_cols"] == 10 and m["exp_rows"] == 6
    assert m["pitch_ratio"] == pytest.approx((14.28 / 1.0, 30.99 / 1.0))        # XDIES(mm→µm) ÷ 번호 pitch


# ───────── 3차 수집: 내 결론이 틀렸는지 가리는 항목들 ─────────
_RECTS = ("[DieRectInfo]\nDiceInBlockFileExists=1\n0_0=1, 20000, 20000, 500, 500\n1_0=1, 19000, 20000, 30000, 500\n"
          "0_1=1, 20000, 20000, 500, 26000\n1_1=1, 19000, 20000, 30000, 26000\n")


def _rect_wafer(d: Path):
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text("[Geometry]\nDieStep_X=49157.9\nDieStep_Y=50407.5\n", encoding="utf-8")
    (d / "ProductInfo.ini").write_text(
        "[Geometric]\nColsInBlock=2\nRowsInBlock=2\n" + _RECTS + "[ExportData]\ncMax=9\nrMax=9\ncMin=1\nrMin=1\n",
        encoding="utf-8")
    (d / "ColorImageGrabingInfo.ini").write_text(           # 첫 결함은 균등 나눔이면 옆 die 로 넘어가는 자리
        "[a.c.1.jpeg]\nX=73957.9\nY=5000\nCol=2\nRow=0\n[b.c.2.jpeg]\nX=31000\nY=27000\nCol=1\nRow=1\n", encoding="utf-8")
    return {d: [(n, lab.parse_live(n)) for n in ("L_T_W_1_9_24300_4500.jpg", "L_T_W_0_8_1000_1000.jpg")]}


def test_die_in_block_comes_from_the_rect_table_not_even_division(tmp_path):
    """실물(AMD Sepio 6×13, 파일): 균등 나눔은 357건 중 45건이 틀렸고 ``[DieRectInfo]`` 표는 전부 맞았다.
    die 안 좌표는 그 die 의 시작점(x0, y0)부터 잰다(관측: XP2100 장비 화면 899·259)."""
    live = _rect_wafer(tmp_path / "L" / "W")
    r = lab.analyze_camtek(tmp_path / "L" / "W", live)
    assert r["sub_rule"] == "칸 표" and r["A1"]["x_bad"] == 0 and r["A1"]["uniform_bad"] == 1
    assert r["truth_live"]["value"] == (1, 9) and r["truth_live"]["ok"] and not r["truth_live"].get("loose")
    assert "칸 안 die 제품" in lab.flags(r)


def test_single_die_offset_is_tested_both_ways(tmp_path):
    """칸 안 die 가 하나뿐인 제품도 ``0_0`` 에 시작점이 적혀 있다(N7G751 3.36·1.68) — 빼야 하는지는 표로 가린다."""
    d = tmp_path / "L" / "W"
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text("[Geometry]\nDieStep_X=1000\nDieStep_Y=1000\n", encoding="utf-8")
    (d / "ProductInfo.ini").write_text("[DieRectInfo]\n0_0=1, 900, 900, 50, 40\n", encoding="utf-8")
    (d / "ColorImageGrabingInfo.ini").write_text(
        "[a.c.1.jpeg]\nX=5100\nY=3200\nCol=5\nRow=3\n[b.c.2.jpeg]\nX=6300\nY=4400\nCol=6\nRow=4\n", encoding="utf-8")
    live = {d: [(n, lab.parse_live(n)) for n in ("L_T_W_3_4_50_160.jpg", "L_T_W_4_3_250_360.jpg")]}
    r = lab.analyze_camtek(d, live)
    assert r["rect0"] == (50.0, 40.0) and r["live_x0"] == {"plain": 0, "minus_x0": 2}


def test_conder_map_orientation_and_defect_cells_are_checked(tmp_path):
    """맵 txt 의 '왼쪽부터 · 아래부터' 가 틀렸다면 뒤집은 쪽이 die 목록과 더 맞는다 — 네 방향을 다 센다."""
    d = tmp_path / "L" / "GX57007306"
    d.mkdir(parents=True)
    (d / "Params_WaferInfo.ini").write_text("[Geometry]\nDieStep_X=1000\nDieStep_Y=1000\n", encoding="utf-8")
    (d / "ColorImageGrabingInfo.ini").write_text("[a.c.1.jpeg]\nX=3100\nY=7200\nCol=3\nRow=7\n", encoding="utf-8")
    (d / "DieLut.dat").write_text("".join(f'<DieLutData Col="{c}" Row="{r}"/>' for c, r in
                                          ((2, 7), (3, 7), (3, 6), (4, 6))), encoding="utf-8")
    live = {d: [(n, lab.parse_live(n)) for n in ("L_T_W_1_0_100_200.jpg", "L_T_W_2_1_300_400.jpg")]}
    r = lab.analyze_camtek(d, live)
    lab.attach_conder_maps(r, {"maps": {tmp_path / "x" / "GX57007306.txt": _MAP_TXT}})
    m = r["conder_map"][0]
    assert m["orient"] == {"그대로": 4, "좌우": 2, "상하": 2, "둘다": 4} or m["orient"]["그대로"] == 4
    assert m["cam_dies"] == 4 and m["live_die"] == [2, 2] and m["live_bad"] == [1, 2]


def test_walk_order_spreads_over_old_and_new_folders():
    """1·2차는 가장 최근 폴더만 봤다 — 3차는 오래된 것까지 고르게 본다."""
    assert lab._spread(list(range(8))) == [0, 4, 2, 6, 1, 5, 3, 7]
    assert lab._spread([]) == [] and sorted(lab._spread(list(range(5)))) == list(range(5))


def test_big_map_is_not_cut_and_row_numbers_follow_the_header():
    """3차 수집은 맵을 300 KB 에서 잘라 302×273 맵의 행 번호가 26칸 밀렸다 — 방향이 틀린 것처럼 보였다."""
    assert lab._MAP_CAP >= 2 * 2 ** 20                           # 앱이 읽는 상한(2 MB) 이상
    cut = "ROWCT:4\nCOLCT:2\nRowData:000 000\nRowData:000 003\n"  # 4행 중 위 2행만 남은 본문
    dies, bad, _c, n = lab.conder_cells(cut)
    assert n == 4 and (0, 3) in dies and bad == {(1, 2)}         # 아래부터 센 행 번호가 그대로


def test_conder_photo_folder_is_checked_against_its_own_map_only(tmp_path):
    """공정마다 맵이 다른 제품(36열/24열)이 있다 — 사진 폴더 바로 옆 맵과만 대조하고 섞임 · 넘침 · 밀림을 적는다."""
    d = tmp_path / "LOT (ABC)" / "2. FVI" / "GX57007306"
    names = ["2D@R_ABC_GX57007306_1_0_100_200.jpg",              # (1,0) = 불량 칸(003)
             "2D@R_ABC_GX57007306_2_1_14500_200.jpg",            # die 안 x 14,500 > XDIES 14.28 mm → 옆 die 로 넘친다
             "INCI_ABC_GX57007306_0_1_100_200.jpg"]              # 다른 레시피 · (0,1) 은 die 가 없는 칸
    live = {d: [(n, lab.parse_live(n)) for n in names]}
    maps = {"maps": {d.parent / "GX57007306.txt": _MAP_TXT,
                     tmp_path / "LOT (ABC)" / "1. OR" / "GX57007306.txt": _MAP_TXT.replace("COLCT:3", "COLCT:9")}}
    (c,) = lab.conder_folder_checks(live, maps)
    assert c["map"] and c["cols"] == 3 and c["n"] == 3 and c["recipes"] == {"2D@R_ABC": 2, "INCI_ABC": 1}
    assert c["in_die"] == 2 and c["on_bad"] == 1 and c["shift"] == [0, 0] and c["spill"] == 1
    assert c["span"] == pytest.approx([42.84, 61.98]) and c["inner_max"]["2D@R_ABC"] == [14500.0, 200.0]
    (none,) = lab.conder_folder_checks({tmp_path / "x" / "W1": live[d]}, maps)
    assert none["map"] is False and "in_die" not in none         # 옆에 맵이 없으면 대조하지 않는다


def test_lots_seen_in_earlier_runs_are_skipped(tmp_path):
    """같은 LOT 을 또 돌면 한 시간이 같은 자료로 찬다 — 앞선 결과에 든 LOT 은 세지 않고 지나간다."""
    root = tmp_path / "AOI-1" / "Scanresult" / "job" / "Setup1"
    for lot in ("LOT-A", "LOT-B"):
        _wafer(root / lot / "W00")
    out = tmp_path / "out"
    out.mkdir()
    (out / "좌표실험_1.json").write_text('{"camtek": [{"lot": %s}]}' % __import__("json").dumps(str(root / "LOT-A")),
                                      encoding="utf-8")
    seen = lab.known_lots(out)
    assert str(root / "LOT-A") in seen
    folders, _cut = lab.nas_walk([tmp_path / "AOI-1"], time.time() + 60, lots=1, log=lambda *_: None, skip=seen)
    assert [f.parent.name for f in folders] == ["LOT-B"]         # lots=1 이어도 건너뛴 LOT 은 세지 않는다
    assert all(ln.startswith(("#", "J:", "M:", "X:", "\\\\")) or len(ln) > 3 for ln in
               (lab.REPO / "dev" / "coord_lab_seen.txt").read_text(encoding="utf-8").splitlines())


def test_conder_name_coordinates_are_checked_against_the_equipment_defects():
    """Conder 사진 이름의 번호 · die 안 좌표가 장비가 적은 결함과 같은지 한 장씩 가른다(사용자 요청 2026-11-12)."""
    inner = [(5, 10, 100.0, 200.0), (6, 10, 300.0, 400.0), (7, 9, 500.0, 600.0)]      # (i, j, die 안 x, y)
    origin = (2, 12)                                                                # col = i−2, row = 12−j
    named = [("same.jpg", (3, 2, 100.5, 199.0)),            # 번호도 좌표도 같다
             ("moved.jpg", (3, 2, 300.0, 400.0)),           # 좌표는 (4,2) 결함 것 — 번호가 col −1
             ("inner.jpg", (5, 3, 550.0, 600.0)),           # 같은 die 인데 x 가 +50
             ("flip.jpg", (9, 9, 900.0, 200.0)),            # px−x 로 뒤집어야 맞는다
             ("none.jpg", (9, 9, 1.0, 1.0))]
    c = lab.conder_xy_check(inner, origin, named, 1000.0, 1000.0)
    assert (c["n"], c["same"], c["moved"], c["inner"], c["none"]) == (5, 1, {"-1,0": 1}, 1, 2)
    assert c["inner_d"] == [50.0, 0.0] and c["flip_x"] == 1 and c["flip_y"] == 0
    assert [k for k, _n in c["examples"]] == ["moved", "inner"]
    blind = lab.conder_xy_check(inner, None, named, 1000.0, 1000.0)                 # 번호표가 없으면 좌표만
    assert blind["xy_only"] == 2 and blind["same"] == 0 and not blind["moved"]


def test_odd_folder_raw_includes_small_files_and_setup_level_recipe(tmp_path):
    """틀린 원인을 찾으려면 정해 둔 이름 말고도 폴더의 설정 · 글자 파일과 위 폴더의 레시피가 있어야 한다."""
    d = tmp_path / "job" / "Setup1" / "LOT-B" / "X00"
    _wafer(d, center=False)
    (d / "Extra.xml").write_text("<a/>", encoding="utf-8")
    (d / "big.dat").write_bytes(b"0" * 100)
    (d / "huge.dat").write_bytes(b"0" * (lab._RAW_SMALL + 1))
    (d.parent.parent / "Recipe.ini").write_text("[Display]\nDisplayColShift=0\n", encoding="utf-8")
    r = lab.analyze_camtek(d, {})
    report = tmp_path / "좌표실험_t.json"
    report.write_text("{}", encoding="utf-8")
    zips, n = lab.collect_zip([r], {}, tmp_path / "좌표실험_t", reports=[report])
    names = zipfile.ZipFile(zips[0]).namelist()
    assert n == 1 and names[0] == "좌표실험_t.json"
    for want in ("X00/Extra.xml", "X00/big.dat", "X00/폴더목록.txt", "X00/_상위/Setup1/Recipe.ini"):
        assert any(x.endswith(want) for x in names), want
    assert not any(x.endswith("X00/huge.dat") for x in names)      # 큰 .dat 가 zip 25개를 만들었다(5차 수집)
    assert not any(x.lower().endswith(".jpeg") for x in names)
    assert lab.collect_zip.__defaults__[1] == 20                    # 20 MB 마다 나눈다(사용자 요청)


_DIELOC_MD = ('<root><RecordSize Size="24"/><Fields><Field Name="Type" Id="1" Offset="0" Vartype="3"/>'
              '<Field Name="x" Id="2" Offset="8" Vartype="5"/><Field Name="y" Id="3" Offset="16" Vartype="5"/></Fields></root>')


def _dieloc(points):
    import struct
    return b"".join(struct.pack("<i4xdd", 0, x, y) for x, y in points)


def test_die_location_is_kept_as_positions_only():
    """수 MB 짜리 s_DieLocation.dat 대신 die 위치만 담는다 — 위치는 하나도 잃지 않는다."""
    pts = [(100.0, 50.0), (200.0, 50.0), (200.0, 150.0)]                # 격자 2×2 에서 한 칸이 빈다
    body = lab.dieloc_text(_dieloc(pts), _DIELOC_MD)
    lines = body.splitlines()
    assert lines[1] == "격자" and lines[2] == "x\t100.0\t200.0" and lines[3] == "y\t50.0\t150.0"
    assert lines[4:] == ["11", "01"]
    assert lab.dieloc_text(_dieloc(pts), None) is None                   # 배치를 모르면 바꾸지 않는다
    assert lab.dieloc_text(b"123", _DIELOC_MD) is None


def test_collected_zips_are_repacked_small_without_collecting_again(tmp_path):
    """zip 25개를 올릴 수 없다(사용자 2026-11-12) — 이미 모은 것을 큰 .dat 만 빼고 다시 묶는다."""
    big = bytes(range(256)) * 2000                                       # 512 KB
    with zipfile.ZipFile(tmp_path / "좌표실험_t_수집.zip", "w") as z:
        z.writestr("좌표실험_t.json", "{}")
        z.writestr("이유.txt", "a")
        z.writestr("000_L_W/Recipe.ini", "[Display]")
        z.writestr("000_L_W/DieAlignment.dat", big)
        z.writestr("000_L_W/s_DieLocation.dat", _dieloc([(1.0, 2.0)]))
    with zipfile.ZipFile(tmp_path / "좌표실험_t_수집_2.zip", "w") as z:
        z.writestr("000_L_W/s_DieLocation.dat.md", _DIELOC_MD)           # 폴더가 두 조각에 걸쳐 있다
        z.writestr("000_L_W/사진목록.txt", "x" * (lab._RAW_SMALL + 1))    # 우리가 쓴 목록은 커도 남긴다
        z.writestr("Conder/000_P/W.txt", "ROWCT:1")
    assert lab.find_collected(tmp_path) is None                          # 두 개쯤은 그냥 올린다
    (tmp_path / "좌표실험_t_수집_3.zip").write_bytes((tmp_path / "좌표실험_t_수집_2.zip").read_bytes())
    assert lab.find_collected(tmp_path) == tmp_path
    made, before, after = lab.slim_zips(tmp_path)
    names = set(zipfile.ZipFile(made[0]).namelist())
    assert [p.name for p in made] == ["좌표실험_t_줄임.zip"] and after < before
    assert names == {"좌표실험_t.json", "이유.txt", "000_L_W/Recipe.ini", "000_L_W/s_DieLocation.txt",
                     "000_L_W/s_DieLocation.dat.md", "000_L_W/사진목록.txt", "Conder/000_P/W.txt"}
    assert len(list(tmp_path.glob("*_수집*.zip"))) == 3                   # 원래 zip 은 그대로
    assert lab.find_collected(tmp_path) is None and lab.slim_zips(tmp_path)[0] == []   # 이미 줄였다


def test_conder_photos_of_another_scan_are_not_compared(tmp_path):
    """같은 웨이퍼라도 다른 공정 · 레시피의 사진은 결함이 다르다 — 5차 수집은 이것을 '좌표가 다르다' 로 셌다."""
    d = tmp_path / "RCP" / "Setup1" / "LOT-B" / "X00"
    assert lab.same_scan_prefix(d) == "RCP_LOT-B_X00_"
    _wafer(d)
    q = (2, 2, 5276.1, 6054.1)                                           # 장비 결함과 같은 die 안 좌표
    live = {tmp_path / "conder" / "1. RDL" / "X00": [("RCP_LOT-B_X00_2_2_5276.1_6054.1.jpg", q)],
            tmp_path / "conder" / "2. PI" / "X00": [("OTHER_LOT-B_X00_2_2_1.0_1.0.jpg", (2, 2, 1.0, 1.0))]}
    r = lab.analyze_camtek(d, live)
    assert [(c["n"], Path(c["dir"]).parent.name) for c in r["conder_xy"]] == [(1, "1. RDL")]
    assert r["conder_xy"][0]["none"] == 0 and r["conder_other_scan"] == 1
    assert "Conder 좌표≠장비 좌표" not in lab.flags(r)


def test_shift_only_walk_keeps_only_lots_with_a_display_shift(tmp_path):
    """시프트 조사 모드(2026-11-13) — 화면 시프트가 0 이 아닌 레시피의 LOT 만 모으고, 나머지는
    세지 않고 지나간다(lots=1 이어도 시프트 LOT 을 찾을 때까지 간다).  위 폴더 레시피도 읽는다."""
    root = tmp_path / "AOI-1" / "Scanresult" / "job"
    for lot in ("LOT-A", "LOT-B"):
        _wafer(root / "Plain" / lot / "W00")
    _wafer(root / "Shift" / "LOT-C" / "W00")
    (root / "Shift" / "Recipe.ini").write_text("[Display]\nDisplayColShift=-1\nDisplayRowShift=0\n",
                                               encoding="utf-8")
    (root / "Plain" / "LOT-A" / "W00" / "Recipe.ini").write_text("[Display]\nDisplayColShift=0\n",
                                                                 encoding="utf-8")
    folders, _cut = lab.nas_walk([tmp_path / "AOI-1"], time.time() + 60, lots=1,
                                 log=lambda *_: None, want=lab.has_shift)
    assert [f.parent.name for f in folders] == ["LOT-C"]
    r = lab.analyze_camtek(folders[0], {})
    assert r["shift"] == (-1, 0) and r["shift_where"] == "상위"     # 앱은 폴더 안 Recipe.ini 만 본다
    assert lab.recipe_shift(root / "Plain" / "LOT-B" / "W00") == (None, None)


def test_shift_walk_skips_sibling_lots_of_a_plain_setup_and_lists_every_setup(tmp_path):
    """레시피는 셋업 단위 — 시프트 없는 셋업은 LOT 하나만 보고 넘어간다(1차 수집은 그 셋업의 LOT 을
    전부 열어 30분에 웨이퍼 3장뿐이었다).  확인한 셋업은 시프트가 없어도 목록에 남는다."""
    root = tmp_path / "AOI-1" / "Scanresult" / "job"
    for lot in ("LOT-A", "LOT-B", "LOT-C"):
        _wafer(root / "Plain" / lot / "W00")
    _wafer(root / "Shift" / "LOT-D" / "W00")
    (root / "Shift" / "LOT-D" / "W00" / "Recipe.ini").write_text(
        "[Display]\nDisplayColShift=-1\nDisplayRowShift=-3\n", encoding="utf-8")
    (root / "Shift" / "LOT-D" / "W00" / "ProductInfo.ini").write_text(
        "[ExportData]\ncMin=1\n", encoding="utf-8")
    census = []
    folders, _cut = lab.nas_walk([tmp_path / "AOI-1"], time.time() + 60, lots=5, log=lambda *_: None,
                                 want=lambda picked: lab.has_shift(picked, census))
    assert [f.parent.name for f in folders] == ["LOT-D"]
    assert sorted(Path(c[0]).name for c in census) == ["Plain", "Shift"]   # Plain 은 LOT 하나만 봤다
    out = lab.write_census(census, tmp_path / "목록.txt").read_text(encoding="utf-8").splitlines()
    assert out[0] == "# 확인한 셋업 2개 · 화면 시프트 있음 1개"
    assert out[2].endswith("\t-1,-3\t폴더\t있음") and out[3].endswith("\t레시피 없음\t-\t없음")
