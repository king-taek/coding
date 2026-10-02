"""AVAGO 재리뷰 — 1차 리뷰 맵의 Reject die 사진을 재리뷰에서 빼는 순수 로직.

맵은 사용자가 준 실물 ``PH8Q66-02B6.txt`` (15966PA0-BW2, 54×70, die 2,962칸,
Reject 4칸)를 **토큰 하나까지 그대로** 다시 만든다(:func:`_real_map_text` — 줄마다
die 구간이 끊김 없이 이어져 있어 앞뒤 ``___`` 개수로 줄여 적었다).

⚠ Camtek 쪽(INI·``s_DieLocation.dat``)은 **합성**이다(R4 — 골든이 아니다).  실물
스캔 폴더는 아직 받지 못했다.  그래서 이 테스트가 못 박는 것은 '정렬 규칙대로 동작한다'
까지이고, 그 규칙(평행이동·방향)이 AVAGO 장비에서 맞는지는 실물 대조가 필요하다
(모듈 docstring 의 R1 '미구분').
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from aoi_verification.app.coords import (camtek_ini, rereview as rr, wafer_geometry,
                                         wafer_txt)

PX, PY = 9000.0, 11000.0
DIA = 300000.0

# 실물 맵 — 줄마다 (앞 ___ 수, 뒤 ___ 수), 70열 54줄.
_LEAD_TRAIL = [
    (29, 28), (24, 24), (21, 21), (18, 18), (16, 16), (14, 14), (13, 13), (11, 11),
    (10, 10), (9, 9), (8, 8), (7, 7), (6, 6), (5, 5), (5, 4), (4, 4), (3, 3), (3, 3),
    (2, 2), (2, 2), (1, 1), (1, 1), (1, 1), (1, 0), (0, 0), (0, 0), (0, 0), (0, 0),
    (0, 0), (0, 0), (1, 0), (1, 1), (1, 1), (1, 1), (2, 1), (2, 2), (2, 2), (3, 3),
    (4, 3), (4, 4), (5, 5), (6, 5), (6, 6), (7, 7), (8, 8), (10, 9), (11, 11),
    (12, 12), (14, 13), (15, 15), (17, 17), (20, 19), (22, 22), (26, 26)]
# (열, 위에서부터 줄) → bin
_REJECTS = {(27, 9): "007", (2, 23): "007", (41, 27): "014", (22, 47): "031"}
_HEAD = ("DEVICE:15966PA0-BW2\nLOT:PH8Q66.00@6321\nWAFER:{w}\nFNLOC:{fn}\nROWCT:54\n"
         "COLCT:70\nBCEQU:000\nREFPX:0\nREFPY:0\nDUTMS:mm\nXDIES:\nYDIES:\n")
WAFER = "PH8Q66-02B6"


def _grid() -> list[list[str]]:
    rows = []
    for j, (lead, trail) in enumerate(_LEAD_TRAIL):
        r = ["___"] * lead + ["000"] * (70 - lead - trail) + ["___"] * trail
        for (i, jj), b in _REJECTS.items():
            if jj == j:
                r[i] = b
        rows.append(r)
    return rows


def _real_map_text(wafer: str = WAFER, fnloc: str = "180") -> str:
    return _HEAD.format(w=wafer, fn=fnloc) + "".join(
        "RowData:" + " ".join(r) + "\n" for r in _grid())


def _map_cells() -> set:
    return {(i, j) for j, r in enumerate(_grid()) for i, t in enumerate(r) if t != "___"}


@pytest.fixture(autouse=True)
def _clear_caches():
    for fn in (camtek_ini.load_folder, camtek_ini.load_raw_folder,
               camtek_ini.load_abs_folder, camtek_ini.load_recipe_folder,
               wafer_geometry.camtek_geometry):
        fn.cache_clear()
    yield


# ---------------------------------------------------------------------------
# 합성 Camtek 웨이퍼 폴더
# ---------------------------------------------------------------------------
DX, DY = 3, 2           # 맵 칸 → stage 인덱스 평행이동(합성값)


def _wafer_folder(root: Path, name: str, defects, *, die_cells=None,
                  die_map: bool = True) -> Path:
    """defects: [(stem, 맵 열, 맵 줄)] — 그 칸 die 의 한가운데에 결함을 둔다.

    ``die_cells`` 를 안 주면 실물 맵의 die 영역을 (DX, DY) 만큼 옮겨 장비 die 맵으로 쓴다."""
    folder = root / name
    folder.mkdir(parents=True)
    ents = []
    for stem, i, j in defects:
        X, Y = (i + DX + 0.5) * PX, (j + DY + 0.5) * PY
        ents.append(f"[{stem}.jpeg]\nX={X}\nY={Y}\n"
                    f"Col={math.floor(X / PX)}\nRow={math.floor(Y / PY)}\n")
        (folder / f"{stem}.jpeg").write_bytes(b"")
    (folder / "ColorImageGrabingInfo.ini").write_text("\n".join(ents), encoding="utf-8")
    (folder / "Params_WaferInfo.ini").write_text(
        f"[Geometry]\nDieStep_X={PX:.6f}\nDieStep_Y={PY:.6f}\n"
        f"[Geometric]\nDiameter={DIA:.6f}\n", encoding="utf-8")
    if die_map:
        cells = die_cells if die_cells is not None else {(i + DX, j + DY)
                                                         for i, j in _map_cells()}
        recs = b"".join(bytes(16) + struct.pack("<dd", a * PX + 10.0, b * PY + 10.0)
                        for a, b in sorted(cells))
        (folder / wafer_geometry._DIE_MAP_FILE).write_bytes(recs)
        (folder / (wafer_geometry._DIE_MAP_FILE + ".md")).write_text(
            '<root><RecordSize Size="32"/><Fields>'
            '<Field Name="x" Id="1" Offset="16" Vartype="5"/>'
            '<Field Name="y" Id="2" Offset="24" Vartype="5"/>'
            '</Fields></root>', encoding="utf-8")
    return folder


def _map_dir(root: Path, text: str | None = None, name: str = WAFER) -> Path:
    d = root / "map"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.txt").write_text(text or _real_map_text(), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# 맵 읽기
# ---------------------------------------------------------------------------
class TestParseMap:
    def test_real_map_rejects_are_the_non_good_bins(self):
        rm = rr.parse_map(_real_map_text(), Path("m.txt"), WAFER)
        assert rm is not None
        assert (rm.rows, rm.cols, len(rm.cells)) == (54, 70, 2962)
        # 사용자 결정: 000 외 전부 Reject — 실물엔 007·014·031 이 4칸.
        assert rm.rejects == frozenset(_REJECTS)
        assert rm.rejects <= rm.cells

    def test_same_die_cells_as_wafer_txt(self):
        """형식 판정은 wafer_txt 하나 — 두 벌로 갈라지지 않는다."""
        text = _real_map_text()
        assert rr.parse_map(text, Path("m"), WAFER).cells == \
            wafer_txt.parse(text, Path("m"), WAFER).cells

    def test_untrusted_maps_are_refused(self):
        assert rr.parse_map(_real_map_text(fnloc="90"), Path("m"), WAFER) is None
        assert rr.parse_map(_real_map_text(), Path("m"), "OTHER-WAFER") is None

    def test_find_map_ignores_case(self, tmp_path):
        d = _map_dir(tmp_path, name=WAFER.lower())
        assert rr.find_map(d, WAFER) == d / f"{WAFER.lower()}.txt"
        assert rr.find_map(d, "PH8Q66-99XX") is None


# ---------------------------------------------------------------------------
# 정렬
# ---------------------------------------------------------------------------
class TestAlign:
    def test_translation_found_on_exact_match(self):
        cells = _map_cells()
        assert rr.align(cells, {(i + 7, j - 1) for i, j in cells}) == (7, -1)

    def test_a_few_unscanned_dies_still_align(self):
        """장비가 검사하지 않은 die(맵에만 있는 칸)는 MAX_UNSCANNED 개까지 허용한다 —
        실물 PH3Q42 의 2962 ≠ 2960/2961.  가장자리 칸이 빠져 최솟값이 밀려도 찾는다."""
        cells = _map_cells()
        moved = {(i + 7, j - 1) for i, j in cells}
        for gone in ((0 + 7, 24 - 1), (36 + 7, 51 - 1)):        # 왼쪽 끝 · 노치 옆
            moved.discard(gone)
        assert rr.align(cells, moved) == (7, -1)

    def test_any_die_outside_the_map_refuses(self):
        """장비 die 가 하나라도 맵 밖이면 쓰지 않는다 — 엉뚱한 die 의 사진이 빠질 수 있다."""
        cells = _map_cells()
        moved = {(i + 7, j - 1) for i, j in cells}
        moved.discard((30 + 7, 30 - 1))
        moved.add((999, 999))
        assert rr.align(cells, moved) is None

    def test_too_many_unscanned_refuses(self):
        """맵에만 있는 칸이 MAX_UNSCANNED 를 넘으면 부분 맵일 수 있다 — 쓰지 않는다."""
        cells = sorted(_map_cells())
        moved = {(i + 7, j - 1) for i, j in cells}
        for i, j in cells[1000:1000 + rr.MAX_UNSCANNED + 1]:
            moved.discard((i + 7, j - 1))
        assert rr.align(cells, moved) is None

    def test_real_die_map_fits_real_map_only_unflipped(self):
        """`관측` — 저장소의 실물 die 맵 PGEE48-13C5(2,960, 70×54)를 실물 맵 PH8Q66-02B6
        (2,962)에 대조: 그대로일 때 평행이동 (2,2) **하나**로 전부 포함, 맵에만 2칸
        (노치 옆 (36,51) · 3시 축 (60,27)).  좌우·상하·180° 뒤집기는 어떤 이동으로도
        포함되지 않는다 → 이 격자에서 방향은 확정(R1)."""
        import re

        d = Path(__file__).resolve().parents[1] / "좌표 확인" / "die맵" / "PGEE48-13C5(AOI-11)"
        txt = (d / "Params_WaferInfo.ini").read_text(encoding="utf-8", errors="ignore")
        px = float(re.search(r"DieStep_X=([\d.]+)", txt).group(1))
        py = float(re.search(r"DieStep_Y=([\d.]+)", txt).group(1))
        die = wafer_geometry.die_map_cells(d, px, py)
        assert die is not None and len(die) == 2960
        cells = frozenset(_map_cells())
        assert rr.align(cells, die) == (2, 2)
        assert sorted(c for c in cells if (c[0] + 2, c[1] + 2) not in die) == [
            (36, 51), (60, 27)]
        flips = ((lambda i, j: (69 - i, j)), (lambda i, j: (i, 53 - j)),
                 (lambda i, j: (69 - i, 53 - j)))
        for f in flips:
            assert rr.align({f(i, j) for i, j in cells}, die) is None

    def test_real_map_is_not_mirror_symmetric(self):
        """R1 — 이 실물 맵은 좌우·상하·180° 뒤집기와 모양이 다르다.

        그래서 이 웨이퍼에서는 '맵이 뒤집혀 있다' 는 경쟁 가설을 모양 대조가 **구분한다**
        (대칭 웨이퍼라면 구분 못 한다 — 모듈 docstring)."""
        cells = frozenset(_map_cells())
        assert rr._flip_hint(cells, cells, 54, 70) == ""


# ---------------------------------------------------------------------------
# 웨이퍼 계획
# ---------------------------------------------------------------------------
class TestPlanWafer:
    def test_photos_on_map_reject_dies_are_excluded(self, tmp_path):
        defects = [("on_007_a", 27, 9), ("on_007_b", 27, 9), ("on_014", 41, 27),
                   ("good_1", 30, 30), ("good_next_to_reject", 28, 9)]
        folder = _wafer_folder(tmp_path / "lot", WAFER, defects)
        imgs = sorted(folder.glob("*.jpeg"))
        plan = rr.plan_wafer(WAFER, folder, imgs, _map_dir(tmp_path))
        assert plan.aligned and plan.offset == (DX, DY)
        assert sorted(p.stem for p in plan.excluded) == ["on_007_a", "on_007_b", "on_014"]
        assert sorted(p.stem for p in plan.review) == ["good_1", "good_next_to_reject"]
        assert not plan.warnings

    def test_screen_die_index_matches_camtek_ini(self, tmp_path):
        """화면 표시 die (col,row) 는 매칭 화면과 같은 camtek_ini 값이다(두 벌 금지)."""
        folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 30, 30)])
        img = folder / "a.jpeg"
        plan = rr.plan_wafer(WAFER, folder, [img], _map_dir(tmp_path))
        c = camtek_ini.resolve(img)
        assert plan.die_of[img] == (c.col, c.row)

    def test_no_map_reviews_everything(self, tmp_path):
        folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9)])
        empty = tmp_path / "map"
        empty.mkdir()
        plan = rr.plan_wafer(WAFER, folder, [folder / "a.jpeg"], empty)
        assert [p.stem for p in plan.review] == ["a"] and not plan.excluded
        assert plan.warnings == [(rr.W_NO_MAP, None)]

    def test_unscanned_die_is_recorded_and_exclusion_still_works(self, tmp_path):
        """장비 die 맵에 1칸이 없어도(장비 미검사) 정렬해 Reject die 사진을 뺀다."""
        cells = {(i + DX, j + DY) for i, j in _map_cells()}
        cells.discard((1 + DX, 23 + DY))       # (1,23) 은 die 칸
        folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9)], die_cells=cells)
        plan = rr.plan_wafer(WAFER, folder, [folder / "a.jpeg"], _map_dir(tmp_path))
        assert plan.aligned and plan.offset == (DX, DY)
        assert [p.stem for p in plan.excluded] == ["a"]
        assert plan.unscanned == [(1, 23)] and not plan.warnings

    def test_die_outside_map_reviews_everything(self, tmp_path):
        """장비 die 가 맵 밖에 하나라도 있으면 정렬하지 않는다 — 전부 재리뷰 + 경고."""
        cells = {(i + DX, j + DY) for i, j in _map_cells()}
        cells.add((0 + DX, 0 + DY))            # (0,0) 은 맵의 ___ 칸
        folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9)], die_cells=cells)
        plan = rr.plan_wafer(WAFER, folder, [folder / "a.jpeg"], _map_dir(tmp_path))
        assert not plan.aligned and not plan.excluded
        assert plan.warnings == [(rr.W_ALIGN_FAIL, (2962, 2963))]

    def test_mirrored_die_map_is_reported_but_not_used(self, tmp_path):
        cells = {(69 - i + DX, j + DY) for i, j in _map_cells()}
        folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9)], die_cells=cells)
        plan = rr.plan_wafer(WAFER, folder, [folder / "a.jpeg"], _map_dir(tmp_path))
        assert not plan.aligned and not plan.excluded
        assert plan.flip_hint == "lr"

    def test_no_die_map_reviews_everything(self, tmp_path):
        folder = _wafer_folder(tmp_path / "lot", WAFER, [("a", 27, 9)], die_map=False)
        plan = rr.plan_wafer(WAFER, folder, [folder / "a.jpeg"], _map_dir(tmp_path))
        assert not plan.excluded
        assert plan.warnings == [(rr.W_NO_DIE_MAP, None)]

    def test_unplaced_and_off_map_photos_are_reviewed(self, tmp_path):
        folder = _wafer_folder(tmp_path / "lot", WAFER,
                               [("off", 0, 0), ("ok", 30, 30)])   # (0,0) 은 ___ 칸
        stray = folder / "no_coord.jpeg"
        stray.write_bytes(b"")
        imgs = [folder / "off.jpeg", folder / "ok.jpeg", stray]
        plan = rr.plan_wafer(WAFER, folder, imgs, _map_dir(tmp_path))
        assert plan.aligned and not plan.excluded
        assert sorted(p.stem for p in plan.review) == ["no_coord", "off", "ok"]
        assert dict(plan.warnings) == {rr.W_UNPLACED: 1, rr.W_OFF_MAP: 1}


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def test_stats_count_distinct_new_reject_dies(tmp_path):
    defects = [("x_on_007", 27, 9), ("a1", 30, 30), ("a2", 30, 30), ("b", 31, 30),
               ("g", 40, 40)]
    folder = _wafer_folder(tmp_path / "lot", WAFER, defects)
    imgs = sorted(folder.glob("*.jpeg"))
    plan = rr.plan_wafer(WAFER, folder, imgs, _map_dir(tmp_path))
    rejects = [folder / "a1.jpeg", folder / "a2.jpeg", folder / "b.jpeg"]
    st = rr.wafer_stats(plan, rejects)
    assert (st.total, st.excluded, st.reviewed, st.good, st.reject) == (5, 1, 4, 1, 3)
    # a1·a2 는 같은 die — 신규 Reject die 는 2개.  맵에 원래 Reject die 4개.
    assert (st.new_reject_dies, st.map_reject_dies, st.total_reject_dies) == (2, 4, 6)
    assert st.unknown_die_rejects == 0
    assert rr.new_reject_cells(plan, rejects) == {(30, 30), (31, 30)}


def test_lot_from_map_path():
    base = r"\\k5cifsn2\k5tsvdata$\1. Conder Scan\540. AVAGO TECH\14. 15966PA0-BW2"
    assert rr.lot_from_map_path(base + r"\288. PH3Q42.00 (FSX)\2. FVI\1. OR") == \
        "PH3Q42.00 (FSX)"
    assert rr.lot_from_map_path("Z:/a/290. PH8Q66.00 (NHW)/2. FVI/1. OR") == "PH8Q66.00 (NHW)"
    assert rr.lot_from_map_path(base) == ""                 # LOT 폴더 위 — 없음
    assert rr.lot_from_map_path("") == ""
