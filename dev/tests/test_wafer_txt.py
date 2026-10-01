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
    assert a.pitch_x > 8034.38 and a.pitch_y > 30702.34          # LOT 사진보다 작지 않다


def test_estimated_die_map_fills_the_wafer(tmp_path):
    """XDIES 가 없으면 사진 최댓값만으론 하한이라 맵이 작게 그려졌다(실측 69 mm/150).
    die 맵이 원을 채우도록 맞춘다 — 최외곽 모서리 ≈ 0.97·반경, 원 밖으로 나가지 않는다."""
    import math
    lot = _lot(tmp_path, dx="", dy="")
    fr = wm.frame_for_folder(lot / "GX57004924", "camtek")
    far = max(math.hypot(fr.grid_x0 + (kx + a) * fr.pitch_x, fr.grid_y0 + (ky + b) * fr.pitch_y)
              for kx, ky in fr.die_cells for a in (0, 1) for b in (0, 1))
    assert 0.95 * fr.radius <= far <= fr.radius


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


# ---------------------------------------------------------------------------
# 경고 — 실제와 다르게 그려졌을 수 있는 맵(배너) · 참고용 안내(하단)
# ---------------------------------------------------------------------------
def _codes(data):
    return [c for c, _ in wm.map_warnings(data)]


def test_no_warning_when_map_and_die_size_are_known(tmp_path):
    lot = _lot(tmp_path)
    paths = [p for w in sorted(_PHOTOS) for p in _photos(lot, w)]
    assert _codes(wm.build_map(resolve_batch(paths))) == []


def test_estimated_die_size_warns(tmp_path):
    lot = _lot(tmp_path, dx="", dy="")
    assert _codes(wm.build_map(resolve_batch(_photos(lot, "GX57004924")))) == \
        [wm.WARN_PITCH_ASSUMED]


def test_mixed_grids_and_off_die_and_unplaced_warn(tmp_path):
    """한 슬롯만 맵 파일이 없어 격자가 다르면 — 이번 (3,3)→(9,3) 유형 — 경고한다."""
    lot = _lot(tmp_path)
    (lot / "GX57007304.txt").unlink()
    (lot / "GX57004924" / "no_coords.jpg").write_bytes(b"")
    paths = [p for w in sorted(_PHOTOS) for p in _photos(lot, w)]
    codes = _codes(wm.build_map(resolve_batch(paths)))
    assert wm.WARN_MIXED_FRAMES in codes and wm.WARN_UNPLACED in codes


def test_off_die_point_warns_with_count(tmp_path):
    lot = _lot(tmp_path)
    (lot / "GX57004924" / f"{_P}GX57004924_0_0_Bump_10.0_10.0.jpg").write_bytes(b"")  # 맵 밖 칸
    data = wm.build_map(resolve_batch(_photos(lot, "GX57004924")))
    assert (wm.WARN_OFF_DIE, 1) in wm.map_warnings(data)


def _wait(qt, dlg):
    import time
    end = time.monotonic() + 10
    while dlg.is_building() and time.monotonic() < end:
        qt.processEvents()
        time.sleep(0.01)
    qt.processEvents()


@pytest.fixture
def qt():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_dialog_shows_banner_only_when_needed_and_always_the_disclaimer(qt, tmp_path):
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.widgets.wafer_map_dialog import WaferMapDialog
    ok = _lot(tmp_path / "ok")
    bad = _lot(tmp_path / "bad", dx="", dy="")
    dlg = WaferMapDialog()
    try:
        dlg.show_folder(ok)
        _wait(qt, dlg)
        assert not dlg.warn_banner.isVisibleTo(dlg)
        assert dlg.disclaimer.isVisibleTo(dlg)
        assert dlg.disclaimer.text() == i18n.KO.WAFER_MAP_DISCLAIMER
        dlg.show_folder(bad)
        _wait(qt, dlg)
        assert dlg.warn_banner.isVisibleTo(dlg)
        assert dlg.warn_banner.text().startswith(i18n.KO.WAFER_MAP_WARN_HEAD)
        assert i18n.KO.WAFER_MAP_WARN_PITCH_ASSUMED in dlg.warn_banner.text()
        dlg.set_fullscreen(True)                    # 칸을 세는 전체화면에서도 경고는 남는다
        assert dlg.warn_banner.isVisibleTo(dlg) and not dlg.disclaimer.isVisibleTo(dlg)
        dlg.set_fullscreen(False)
        assert dlg.disclaimer.isVisibleTo(dlg)
    finally:
        dlg.set_fullscreen(False)
        dlg.deleteLater()


def test_two_maps_name_the_side_in_the_banner():
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.widgets.wafer_map_dialog import WaferMapDialog
    pytest.importorskip("PyQt6.QtWidgets")
    bad = wm.MapData(None, (), (Path("x.jpg"),))
    text = WaferMapDialog.warning_text([("기준 · A", bad), ("검증 · B", wm.MapData(None, (), ()))])
    assert "기준 · A — " in text and "검증 · B" not in text
    assert WaferMapDialog.warning_text([("x", wm.MapData(None, (), ()))]) == ""
    assert i18n.KO.WAFER_MAP_WARN_UNPLACED_FMT.format(n=1) in text
