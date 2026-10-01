"""LIVE 파일명 LOT 의 웨이퍼 맵 .txt — Wafer map 이 장비 맵대로 그려지는가.

실물 근거(``AST254 … E1X000.C03 DCC20 (WVU)\\2. BS CUP``, 2026-10 진단):
기하 파일이 하나도 없는 LOT 에서 앱이 **슬롯마다 자기 사진으로 pitch 를 추정**해

1. slot 을 바꾸면 격자가 바뀌고(추정 폭 1,632 ~ 14,942 µm),
2. die 없는 칸에 점이 찍히고(38/350),
3. 설정 화면 '전체'(LOT 합산)에서 GX57007306 의 파일명 (3,3) 사진이 (9,3) 칸에 찍혔다
   (격자는 첫 슬롯 프레임, 점은 자기 프레임).

LOT 폴더의 ``<WaferID>.txt`` 가 장비 웨이퍼 맵이고, 파일명 (col,row) 는 그 맵의
**왼쪽부터 0 · 아래부터 0** 칸이다(결함 칸 185 중 184 가 불량 bin — 다른 해석은 반증).
아래 맵 텍스트와 파일명은 그 실물 그대로다(사진 내용은 쓰지 않으므로 빈 파일).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from aoi_verification.app.coords import (camtek_ini, resolve_batch, wafer_geometry,
                                         wafer_map as wm, wafer_txt)

_HEAD = ("DEVICE:AST254-AG6S2-6CAA\nLOT:E1X000.C03 DCC20@6321\nWAFER:{w}\nFNLOC:{fn}\n"
         "ROWCT:9\nCOLCT:19\nBCEQU:000\nREFPX:8\nREFPY:1\nDUTMS:mm\nXDIES:{dx}\nYDIES:{dy}\n")
_MAPS = {
    "GX57004924": """\
___ ___ ___ ___ ___ ___ ___ 000 000 000 000 000 ___ ___ ___ ___ ___ ___ ___
___ ___ ___ 000 000 000 000 000 000 000 @@@ 000 003 000 000 000 ___ ___ ___
___ 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 ___
000 000 000 000 000 000 000 000 000 000 @@@ 000 000 000 000 000 000 @@@ 000
000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000
040 @@@ 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000
___ 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 ___
___ ___ ___ 000 000 000 000 000 000 000 000 000 000 000 000 000 ___ ___ ___
___ ___ ___ ___ ___ ___ ___ 000 000 000 000 @@@ ___ ___ ___ ___ ___ ___ ___""",
    "GX57007304": """\
___ ___ ___ ___ ___ ___ ___ 000 000 000 000 000 ___ ___ ___ ___ ___ ___ ___
___ ___ ___ 000 003 000 @@@ 000 000 000 000 000 000 000 000 000 ___ ___ ___
___ 000 000 000 000 @@@ 000 000 000 @@@ 000 000 000 000 000 000 000 000 ___
000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000 000
000 000 000 000 000 000 000 000 000 000 000 000 000 000 @@@ 000 000 000 000
000 000 000 003 000 000 000 000 000 000 000 000 000 000 003 000 000 000 000
___ 000 000 @@@ 000 000 000 000 000 000 000 000 000 000 000 000 000 000 ___
___ ___ ___ @@@ 000 000 000 @@@ 000 000 000 000 @@@ 000 000 000 ___ ___ ___
___ ___ ___ ___ ___ ___ ___ 000 003 000 000 000 ___ ___ ___ ___ ___ ___ ___""",
}
_P = "2D@R5-AS-T254-A01_0857122AY-0B_WVU_"
_PHOTOS = {
    "GX57004924": [
        "GX57004924_0_3_Over Sized Bump_5861.74498296693_30702.3341693859",
        "GX57004924_0_3_Undersized Bump_2108.27070397045_30401.091166803",
        "GX57004924_12_7_Metal Residue_6505.25933455976_23819.9527704463"],
    "GX57007304": [
        "GX57007304_14_3_Metal Residue_8034.37924730752_12188.0203661192",
        "GX57007304_3_3_Metal Residue_621.86273844965_27686.5636524923",
        "GX57007304_4_7_Metal Residue_2245.53580391832_18198.7251131793",
        "GX57007304_8_0_Metal Residue_3624.47601133332_1102.9983017979"],
}


@pytest.fixture(autouse=True)
def _clear_caches():
    for fn in (wm._camtek, wm._kla, wm._lot_extent, wafer_txt.load,
               camtek_ini.load_folder, camtek_ini.load_raw_folder,
               wafer_geometry.camtek_geometry, wafer_geometry.live_geometry):
        fn.cache_clear()
    yield


def _lot(tmp_path: Path, *, fn="180", dx="14.28", dy="30.99", txt=True) -> Path:
    lot = tmp_path / "2. BS CUP"
    for w, stems in _PHOTOS.items():
        (lot / w).mkdir(parents=True)
        for s in stems:
            (lot / w / f"{_P}{s}.jpg").write_bytes(b"")
        if txt:
            body = "".join(f"RowData:{ln}\n" for ln in _MAPS[w].splitlines())
            (lot / f"{w}.txt").write_text(_HEAD.format(w=w, fn=fn, dx=dx, dy=dy) + body,
                                          encoding="utf-8")
    return lot


def _photos(lot: Path, w: str) -> list[Path]:
    return sorted((lot / w).iterdir())


def _disp(frame, p):
    """평면 점 → 화면이 보여 주는 칸 (col, row) — 아래부터 0."""
    kx, ky = wm.cell_of(frame, p.x, p.y)
    return kx, (len({j for _, j in frame.die_cells}) - 1) - (-ky - 1)


def test_parse_reads_cells_with_row_from_top():
    t = _HEAD.format(w="W1", fn="180", dx="14.28", dy="30.99") + "".join(
        f"RowData:{ln}\n" for ln in _MAPS["GX57004924"].splitlines())
    wt = wafer_txt.parse(t, Path("W1.txt"), "W1")
    assert (wt.rows, wt.cols) == (9, 19)
    assert len(wt.cells) == 127              # csv 의 NET DIE Q'TY 와 같다
    assert (wt.die_x, wt.die_y) == (14280.0, 30990.0)
    assert (7, 0) in wt.cells and (0, 0) not in wt.cells      # 첫 줄 = 맨 위


@pytest.mark.parametrize("bad", [
    lambda t: t.replace("FNLOC:180", "FNLOC:90"),             # 방향 미확인 노치
    lambda t: t.replace("COLCT:19", "COLCT:20"),              # 머리와 모양 불일치
    lambda t: t.replace("WAFER:W1", "WAFER:OTHER"),           # 다른 웨이퍼 맵
])
def test_parse_refuses_what_it_cannot_trust(bad, caplog):
    t = _HEAD.format(w="W1", fn="180", dx="14.28", dy="30.99") + "".join(
        f"RowData:{ln}\n" for ln in _MAPS["GX57004924"].splitlines())
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        assert wafer_txt.parse(bad(t), Path("W1.txt"), "W1") is None
    assert caplog.records                                   # 조용히 버리지 않는다


@pytest.mark.parametrize("w", list(_PHOTOS))
def test_every_photo_lands_on_its_filename_die(tmp_path, w):
    """증상 2 — 점은 파일명 (col,row) 칸에, 그리고 die 가 있는 칸에 찍힌다."""
    lot = _lot(tmp_path)
    data = wm.build_map(resolve_batch(_photos(lot, w)))
    assert not data.unplaced and data.frame.die_cells
    assert data.frame.pitch_x == 14280.0 and data.frame.pitch_assumed is False
    for p in data.points:
        assert _disp(data.frame, p) == (p.col, p.row)
        assert wm.cell_of(data.frame, p.x, p.y) in data.frame.die_cells


def test_defect_dies_are_the_failed_bins(tmp_path):
    """규약의 실물 근거 — 결함 사진 칸은 맵에서 불량 bin(000 아님)이다."""
    lot = _lot(tmp_path)
    for w in _PHOTOS:
        grid = [ln.split() for ln in _MAPS[w].splitlines()]
        for p in wm.build_map(resolve_batch(_photos(lot, w))).points:
            assert grid[len(grid) - 1 - p.row][p.col] != "000"


def test_slots_share_one_grid(tmp_path):
    """증상 1 — 사진 수가 다른 슬롯이어도 격자(pitch·위상)가 같다."""
    lot = _lot(tmp_path)
    a, b = (wm.frame_for_folder(lot / w, "camtek") for w in _PHOTOS)
    assert (a.pitch_x, a.pitch_y, a.grid_x0, a.grid_y0) == \
        (b.pitch_x, b.pitch_y, b.grid_x0, b.grid_y0)


def test_lot_view_keeps_3_3_at_3_3(tmp_path):
    """증상 3 — 설정 화면 '전체'(LOT 합산)에서 다른 슬롯의 (3,3) 이 (3,3) 칸에 찍힌다."""
    lot = _lot(tmp_path)
    paths = [p for w in sorted(_PHOTOS) for p in _photos(lot, w)]
    data = wm.build_map(resolve_batch(paths))
    assert data.frame.die_cells is not None
    (p33,) = [p for p in data.points if (p.col, p.row) == (3, 3)]
    assert _disp(data.frame, p33) == (3, 3)
    assert all(wm.cell_of(data.frame, p.x, p.y) in data.frame.die_cells for p in data.points)


def test_xdies_contradicted_by_photos_is_not_used(tmp_path):
    """같은 웨이퍼의 ASSY 맵은 YDIES=27.3815 — 사진 y 30,702 µm 가 넘으므로 안 쓴다."""
    lot = _lot(tmp_path, dx="14.797", dy="27.3815")
    data = wm.build_map(resolve_batch(_photos(lot, "GX57004924")))
    assert data.frame.pitch_assumed is True and data.frame.pitch_y > 30702
    for p in data.points:                    # 칸은 여전히 맵 그대로
        assert _disp(data.frame, p) == (p.col, p.row)


def test_missing_xdies_estimates_once_per_lot(tmp_path):
    """XDIES 가 빈 파일(관측 18/33) — LOT 전체 사진으로 한 번 추정, 슬롯마다 같다."""
    lot = _lot(tmp_path, dx="", dy="")
    a, b = (wm.frame_for_folder(lot / w, "camtek") for w in _PHOTOS)
    assert a.pitch_assumed and (a.pitch_x, a.pitch_y) == (b.pitch_x, b.pitch_y)
    assert a.pitch_x == pytest.approx(8034.37924730752 * 1.05)   # LOT 최대 x(7304)


def test_without_txt_the_estimate_is_lot_wide_too(tmp_path):
    """맵 파일이 없는 LOT 도 슬롯마다 따로 추정하지 않는다(증상 1의 직접 원인)."""
    lot = _lot(tmp_path, txt=False)
    a, b = (wm.frame_for_folder(lot / w, "camtek") for w in _PHOTOS)
    assert a.die_cells is None and a.pitch_assumed
    assert (a.pitch_x, a.pitch_y) == (b.pitch_x, b.pitch_y)


def test_unverified_notch_falls_back_not_flipped(tmp_path):
    """FNLOC≠180 은 맵을 쓰지 않는다 — 계산 격자로 폴백(뒤집힌 맵을 그리지 않는다)."""
    lot = _lot(tmp_path, fn="90")
    frame = wm.frame_for_folder(lot / "GX57004924", "camtek")
    assert frame.die_cells is None and frame.pitch_assumed
