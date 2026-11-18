"""블록 제품(한 칸에 die 여러 개) col·row — 실물 골든.

``ProductInfo.ini`` 의 ``XDieIndex/YDieIndex`` 가 die 가 아니라 **큰 칸(블록)** 간격이고,
``[Geometric] ColsInBlock/RowsInBlock`` · ``[DieRectInfo] a_b=사용,폭,높이,x0,y0`` 이 칸 안의
die 배치다.  번호표(``[ExportData]``)는 die 단위다.  공식: 큰 칸 ``I = floor(X/칸 간격)`` →
칸 안에서 가장 가까운 die 열 ``a`` → ``i = I×ColsInBlock + a``, die 안 ``x = 칸 안 X − x0`` (Y 동일).
균등 나눔(칸 간격÷칸 수)은 근사라 AMD Sepio 에서 45/357 틀렸고 칸 표는 795/795 맞았다(관측).

실물: Qorvo XP2100 7-8WW058SOD2 (INI 만, 사진은 이름뿐).
장비 화면: ``78889.195422.c._1_`` = col 25 · row 111 · x 899 · y 259 (관측, 사용자 확인 2026-11-11).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, clear_caches
from aoi_verification.app.coords import wafer_geometry as wg

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "dev" / "좌표 확인" / "블록제품(XP2100)" / "7-8WW058SOD2"


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _photo_names() -> list[str]:
    text = (FOLDER / "사진목록.txt").read_text(encoding="utf-8")
    return [s.strip() for s in text.splitlines() if s.strip() and not s.startswith("#")]


def test_block_layout_is_read():
    bl = wg.block_layout(FOLDER)
    assert bl is not None
    assert (bl.cols, bl.rows) == (18, 23)


def test_block_geometry_uses_export_grid():
    g = wg.camtek_geometry(FOLDER)
    assert g is not None and g.block is not None
    assert "DieRectInfo" in g.source and "ExportData" in g.source
    assert (g.col_origin, g.row_total) == (36, 252)


def test_equipment_screen_example():
    c = camtek_ini.resolve(FOLDER / "78889.195422.c._1_.jpeg")
    assert c is not None
    assert (c.col, c.row) == (25, 111)              # 오차 0
    assert abs(c.x - 899) <= 100 and abs(c.y - 259) <= 100


def test_every_photo_column_matches_equipment_col_field():
    """X 등호: 칸 표로 구한 die 열이 장비 INI 의 Col 과 전부 같아야 한다."""
    g = wg.camtek_geometry(FOLDER)
    text = (FOLDER / "ColorImageGrabingInfo.ini").read_text(encoding="utf-8", errors="replace")
    checked = 0
    for block in text.split("[")[1:]:
        name, _, body = block.partition("]")
        kv = dict(line.split("=", 1) for line in body.splitlines() if "=" in line)
        if "Col" not in kv or name.endswith(".wlup"):
            continue
        i, _j, _x, _y = g.block.locate(float(kv["X"]), float(kv["Y"]))
        assert i == int(kv["Col"]), name
        checked += 1
    assert checked >= 100


def test_all_photos_resolve():
    names = _photo_names()
    got = [camtek_ini.resolve(FOLDER / n) for n in names]
    assert sum(c is not None for c in got) >= len(names) - 1
