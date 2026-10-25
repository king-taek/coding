"""col/row 번호가 **틀렸을 수 있을 때만** ⚠ 를 붙인다(사용자 결정 2026-10-25).

정상(장비 번호표 ``[ExportData]`` 를 읽음 · KLA 중심 기하)이면 아무 표시도 없다.
번호표가 없어 계산한 Camtek 번호 · 중심 없는 KLA 번호는 사진 정보 · 엑셀 · Wafer map 에
경고가 붙는다.  격자를 못 읽은 폴더(``camtek_abs``)는 col 숫자 자체를 ⚠ 로 바꾼다.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from aoi_verification.app import i18n
from aoi_verification.app.coords import camtek_ini, clear_caches, kla_info, single_info
from aoi_verification.app.coords import wafer_map as wm
from aoi_verification.app.coords.models import (
    NUMBER_WARN_KLA_GUESS, NUMBER_WARN_NO_GRID, NUMBER_WARN_NO_TABLE)

_CAM = Path(__file__).resolve().parents[1] / "좌표 확인" / "장비격자(ExportData)" / "Camtek_CTX"
_PHOTO = "155820.284777.c.-770554234"


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _qorvo(tmp_path: Path, edit=lambda t: t) -> Path:
    d = tmp_path / "LOT" / "SQ67P25-G3"
    shutil.copytree(_CAM / "SQ67P25-G3", d)
    p = d / "ProductInfo.ini"
    p.write_text(edit(p.read_text(encoding="latin-1")), encoding="latin-1")
    return d


def _photo(folder: Path) -> Path:
    name = next(ln.strip() for ln in (folder / "사진목록.txt").read_text(encoding="utf-8")
                .splitlines() if ln.startswith(_PHOTO))
    (folder / name).write_bytes(b"")
    return folder / name


def _has_warn(lines) -> bool:
    return any(l.startswith("⚠") for l in lines)


def test_export_grid_has_no_mark(tmp_path):
    d = _qorvo(tmp_path)
    assert all(c.number_warn == "" for c in camtek_ini.load_folder(d).values())
    assert not _has_warn(single_info.coord_lines(_photo(d)))


def test_without_export_grid_warns(tmp_path):
    d = _qorvo(tmp_path, lambda t: t.replace("[ExportData]", "[Other]"))
    assert {c.number_warn for c in camtek_ini.load_folder(d).values()} == {NUMBER_WARN_NO_TABLE}
    lines = single_info.coord_lines(_photo(d))
    assert lines[1] == i18n.KO.DEFECT_NUMBER_WARN_FMT.format(
        text=i18n.KO.DEFECT_NUMBER_WARN_NO_TABLE)        # 번호 바로 밑


def test_no_grid_folder_hides_the_number(tmp_path):
    d = _qorvo(tmp_path)
    for f in ("ProductInfo.ini", "Params_WaferInfo.ini", "Wafer2Table.ini"):
        (d / f).unlink(missing_ok=True)
    clear_caches()
    cs = camtek_ini.load_folder(d)
    if not cs or {c.source for c in cs.values()} != {"camtek_abs"}:
        pytest.skip("이 샘플은 격자 파일 없이 die 로 쪼개지지 않는다")
    assert {c.number_warn for c in cs.values()} == {NUMBER_WARN_NO_GRID}
    assert single_info.coord_lines(_photo(d))[0] == i18n.KO.DEFECT_COL_UNKNOWN_FMT


def _kla(tmp_path: Path, center: bool) -> Path:
    folder = tmp_path / ("c" if center else "n")
    folder.mkdir()
    head = "FileVersion 1 2;\nDiePitch 5.000000e+03 6.000000e+03;\nSampleSize 1 300;\nWaferID \"W1\";\n"
    if center:
        head += "SampleCenterLocation 1.500000e+05 1.500000e+05;\n"
    body = "TiffFileName a.jpg\n 1 100.0 200.0 1000.0 2000.0 3 4 0\n"
    (folder / "W1.001").write_text(head + body, encoding="utf-8")
    (folder / "a.jpg").write_bytes(b"")
    return folder


@pytest.mark.parametrize("center, want", [(True, ""), (False, NUMBER_WARN_KLA_GUESS)])
def test_kla_warns_only_without_center(tmp_path, center, want):
    cs = kla_info.load_folder(_kla(tmp_path, center))
    assert cs and {c.number_warn for c in cs.values()} == {want}


def test_map_warning_counts_only_guessed_numbers():
    assert (wm.WARN_NUMBER_GUESS, 3) in wm.map_warnings(wm.MapData(None, (), (), guessed_points=3))
    assert wm.map_warnings(wm.MapData(None, (), ())) == []
    assert i18n.KO.WAFER_MAP_WARN_NUMBER_GUESS_FMT.format(n=3) in wm.warning_reasons(
        wm.MapData(None, (), (), guessed_points=3))
