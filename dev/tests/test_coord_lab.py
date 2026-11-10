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
    """Conder Scan 흉내: 고객 폴더 하나 + 파일 보관함 하나."""
    good = root / "531. 고객" / "1. 제품" / "1. LOT (ABC)" / "1. FVI" / "GX57007306"
    good.mkdir(parents=True)
    (good / "LOT_GX57007306_3_4_100_200.jpg").write_bytes(b"")
    (good / "LOT_OTHERWAFER_3_4_100_200.jpg").write_bytes(b"")           # 다른 웨이퍼 사진
    for name in ("9", "GX57007306"):                                     # 보관함 안의 우연한 같은 이름
        junk = root / "0. 전산 X" / "PLAT" / name
        junk.mkdir(parents=True)
        (junk / f"LOT_{name}_3_4_100_200.jpg").write_bytes(b"")
    stray = root / "531. 고객" / "9"
    stray.mkdir()
    (stray / "LOT_9_3_4_100_200.jpg").write_bytes(b"")
    return good


def test_conder_walk_does_not_take_storage_or_weak_names_for_a_wafer(tmp_path):
    """실측(2026-11-10): 이름이 ``9`` 인 폴더를 파일 보관함에서 찾아 웨이퍼로 짚었다."""
    good = _conder_tree(tmp_path)
    live, cut = lab.nas_conder([tmp_path], {"9", "GX57007306"}, time.time() + 60, log=lambda *_: None)
    assert not cut and list(live) == [good]
    assert [n for n, _ in live[good]] == ["LOT_GX57007306_3_4_100_200.jpg"]   # ID 토큰이 든 사진만
    assert not lab.strong_id("9") and not lab.strong_id("00123") and lab.strong_id("PGEE48")


def test_stop_ends_the_walk_at_once_and_leaves_no_files(tmp_path):
    """[중지] 는 큰 폴더 한가운데서도 먹고, 찾는 중에 멈추면 결과 파일을 만들지 않는다."""
    _conder_tree(tmp_path)
    calls = []

    def stop():
        calls.append(1)
        return len(calls) > 3                                            # 훑기 시작한 직후 중지

    live, cut = lab.nas_conder([tmp_path], {"GX57007306"}, stop, log=lambda *_: None)
    assert cut and not live
    out = tmp_path / "out"
    out.mkdir()
    assert lab.run_nas([str(tmp_path)], out, stop=lambda: True) == (None, 0, 0)
    assert list(out.iterdir()) == []
