"""장비 die 목록(``DieLut.dat``) 번호 기준 · '중심식=격자식' 일치 때 ⚠ 없음 — 회귀 가드.

실물(현택님 수집 zip, 2026-10-26) ``dev/좌표 확인/die목록(DieLut)/``:

* ``RDL2 TEST AOI-5/32256616EWH0`` — 검토(Export Map) 전이라 번호표가 없다.  DieLut 범위
  (2, 8, 1, 6) → ``(col_origin, row_total) = (2, 6)``.  KLA 48쌍 · Conder LOT 맵 25장 ·
  AOI-4↔5 290쌍이 같은 번호였다(관측).
* ``GX57007306(T254)`` — 번호표 (2, 20, 2, 10) = DieLut.  부분 맵(50칸)은 row 를 9 로 냈던 자재.
* ``NELA26-01C1(AVAGO)`` — 번호표 (2, 71, 2, 55) = DieLut.  중심식은 row 56, 격자식은 55.

둘 다 있는 26 LOT · 51 폴더에서 DieLut 범위 = 번호표였다(관측, 예외 0).  번호표 있는 131 폴더
중 중심식=격자식인 123 폴더는 전부 번호표와 같았고, 틀린 8(AVAGO·QORVO)은 전부 둘이 달랐다.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, clear_caches
from aoi_verification.app.coords import wafer_geometry as wg
from aoi_verification.app.coords import wafer_map as wm
from aoi_verification.app.coords.models import NUMBER_WARN_NO_TABLE

_GOLD = Path(__file__).resolve().parents[1] / "좌표 확인" / "die목록(DieLut)"
_RDL2 = _GOLD / "RDL2 TEST AOI-5(번호표 없음)" / "32256616EWH0"
_T254 = _GOLD / "GX57007306(T254)"
_AVAGO = _GOLD / "NELA26-01C1(AVAGO)"


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _bbox(cells):
    return (min(i for i, _ in cells), max(i for i, _ in cells),
            min(j for _, j in cells), max(j for _, j in cells))


def _edit(path: Path, old: str, new: str) -> None:
    """INI 가 ANSI 든 UTF-16 이든 그대로 고친다(인코딩을 바꾸지 않는다)."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-16-le"):
        o, n = old.encode(enc), new.encode(enc)
        if o in raw:
            path.write_bytes(raw.replace(o, n, 1))
            return
    raise AssertionError(f"{old!r} 가 {path.name} 에 없다")


def _copy(tmp_path: Path, src: Path) -> Path:
    d = tmp_path / "LOT" / src.name
    shutil.copytree(src, d)
    return d


@pytest.mark.parametrize("folder, want", [
    (_T254, (2, 20, 2, 10)),
    (_AVAGO, (2, 71, 2, 55)),
    (_RDL2, (2, 8, 1, 6)),
])
def test_dielut_bbox_is_the_equipment_range(folder, want):
    cells = wg.dielut_cells(folder)
    assert cells and _bbox(cells) == want


@pytest.mark.parametrize("folder", [_T254, _AVAGO])
def test_table_wins_and_dielut_agrees(folder):
    g = wg.camtek_geometry(folder)
    assert wg.EXPORT_SOURCE_TAG in g.source
    lut = wg._dielut_origins(folder, g.pitch_x, g.pitch_y, wg._read_diameter(folder),
                             *wg._wafer_center(folder))
    assert lut == (g.col_origin, g.row_total)


def test_rdl2_without_table_uses_dielut_and_no_warning():
    g = wg.camtek_geometry(_RDL2)
    assert (g.col_origin, g.row_total) == (2, 6)
    assert wg.DIELUT_SOURCE_TAG in g.source and wg.EXPORT_SOURCE_TAG not in g.source
    coords = camtek_ini.load_folder(_RDL2)
    assert coords and {c.number_warn for c in coords.values()} == {""}


def test_wafer_map_cells_come_from_dielut():
    assert wm._camtek(_RDL2).cells == wg.dielut_cells(_RDL2)
    assert len(wg.dielut_cells(_RDL2)) == 30


def test_without_dielut_agreement_is_trusted(tmp_path):
    d = _copy(tmp_path, _RDL2)
    (d / wg._DIELUT_FILE).unlink()
    g = wg.camtek_geometry(d)
    assert (g.col_origin, g.row_total) == (2, 6)
    assert wg.AGREE_SOURCE_TAG in g.source and wg.DIELUT_SOURCE_TAG not in g.source
    assert {c.number_warn for c in camtek_ini.load_folder(d).values()} == {""}


def test_disagreement_keeps_the_warning(tmp_path):
    """AVAGO: 번호표·DieLut 를 빼면 중심식(56)과 격자식(55)이 갈린다 → 값은 유도, ⚠ 유지."""
    d = _copy(tmp_path, _AVAGO)
    (d / wg._DIELUT_FILE).unlink()
    _edit(d / "ProductInfo.ini", "[ExportData]", "[Other]")
    g = wg.camtek_geometry(d)
    assert not wg.is_trusted_source(g.source), g.source
    assert {c.number_warn for c in camtek_ini.load_folder(d).values()} == {NUMBER_WARN_NO_TABLE}


def test_dielut_outside_wafer_is_rejected(tmp_path):
    d = _copy(tmp_path, _RDL2)
    _edit(d / wg._DIELUT_FILE, 'Col="2"', 'Col="40"')
    g = wg.camtek_geometry(d)
    assert wg.DIELUT_SOURCE_TAG not in g.source
    assert (g.col_origin, g.row_total) == (2, 6)       # 계산 일치 경로로 돌아간다


def test_dielut_of_another_pitch_is_rejected(tmp_path):
    d = _copy(tmp_path, _RDL2)
    _edit(d / "ProductInfo.ini", "XDieIndex=37247.9", "XDieIndex=37300.0")
    g = wg.camtek_geometry(d)
    assert wg.DIELUT_SOURCE_TAG not in g.source


def test_conflict_with_table_logs_and_keeps_table(monkeypatch, caplog):
    monkeypatch.setattr(wg, "_dielut_origins", lambda *_: (3, 11))
    with caplog.at_level(logging.WARNING, logger="aoi.coords"):
        g = wg.camtek_geometry(_T254)
    assert (g.col_origin, g.row_total) == (2, 10) and wg.EXPORT_SOURCE_TAG in g.source
    assert any(wg._DIELUT_FILE in r.getMessage() and "다릅니다" in r.getMessage()
               for r in caplog.records)


def test_trusted_tags():
    assert wg.is_trusted_source("Params+" + wg.EXPORT_SOURCE_TAG)
    assert wg.is_trusted_source("Params+" + wg.DIELUT_SOURCE_TAG)
    assert wg.is_trusted_source("Params+s_DieLocation.dat+" + wg.AGREE_SOURCE_TAG)
    assert not wg.is_trusted_source("Params+s_DieLocation.dat")
    assert not wg.is_trusted_source("models.py 상수")
