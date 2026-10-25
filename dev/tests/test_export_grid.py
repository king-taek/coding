"""장비 번호 격자(``ProductInfo.ini [ExportData] cMin/cMax/rMin/rMax``) 회귀 가드.

★ 실물(2026-10-25, QORVO W97218SGZ4BK16 UBM · 11호기 · S/M ``CTX``, 현택님 수집):
die 가 작아(pitch 919.8 µm) 중심 유도(2, 326 → 상한 200 때문에 327)·die 맵(6, 317)·
3 mm 격자(5, 323) 어느 것도 장비 번호가 아니었다.  장비 화면(현택님 확인)과 Conder LIVE
파일명 55장은 전부 ``col = x_index − cMin``, ``row = rMax − y_index`` 였다(관측).

같은 키가 이미 맞던 제품(TB500 · T254 · TI CCB · AVAGO, 웨이퍼 165개)에서도 기존 결과와
전부 같다(관측) — 여기에는 1차원 유도가 틀리던 두 사례(AVAGO NELA26-01C1 맵 3 → 장비 2,
T254 GX57007306 부분 맵)를 실물로 둔다.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, clear_caches
from aoi_verification.app.coords import wafer_geometry as wg

_GOLD = Path(__file__).resolve().parents[1] / "좌표 확인" / "장비격자(ExportData)"
_CAM = _GOLD / "Camtek_CTX"
_CON = _GOLD / "Conder_UBM"
_KNOWN = _GOLD / "확인된제품"


def _names(f: Path) -> list[str]:
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _live(name: str) -> tuple[int, int, float, float]:
    """Conder 파일명 ``…_<WaferID>_col_row_<분류>_x_y`` — 분류에 공백이 있어 끝에서 읽는다."""
    t = Path(name).stem.split("_")
    return int(t[4]), int(t[5]), float(t[-2]), float(t[-1])


def test_qorvo_uses_the_export_grid():
    for wafer in sorted(p for p in _CAM.iterdir() if p.is_dir()):
        g = wg.camtek_geometry(wafer)
        assert (g.col_origin, g.row_total) == (5, 319), wafer.name
        assert "[ExportData]" in g.source


def test_qorvo_numbers_match_conder_live_names():
    """Conder 55장 전부 — 같은 die 번호 · die 안 x/y 3 µm 이내(파일명은 정수로 반올림)의 INI 결함이 있어야 한다."""
    n = 0
    for wafer in sorted(p for p in _CON.iterdir() if p.is_dir()):
        ini = list(camtek_ini.load_folder(_CAM / wafer.name).values())
        for name in _names(wafer / "사진목록.txt"):
            col, row, x, y = _live(name)
            assert any(c.col == col and c.row == row and abs(c.x - x) <= 3 and abs(c.y - y) <= 3
                       for c in ini), name
            n += 1
    assert n == 55


def test_equipment_screen_example():
    """현택님 장비 화면 확인(2026-10-25): SQ67P25-G3 이 사진은 164 / 10 (앱 옛 값 167 / 18)."""
    ini = camtek_ini.load_folder(_CAM / "SQ67P25-G3")
    c = next(v for k, v in ini.items() if str(k).startswith("155820.284777.c.-770554234"))
    assert (c.col, c.row) == (164, 10)


@pytest.mark.parametrize("wafer, want", [
    ("NELA26-01C1", (2, 55)),       # die 맵은 3 — 장비는 2(LIVE 20장, TestLotDieMapGolden)
    ("NELA26-07F6", (2, 55)),
    ("GX57007306", (2, 10)),        # 부분 맵이 1 작게 냈다 — 장비는 10(TestPartialDieMap)
])
def test_confirmed_products_agree(wafer, want):
    g = wg.camtek_geometry(_KNOWN / wafer)
    assert (g.col_origin, g.row_total) == want
    assert "[ExportData]" in g.source


def _copy(tmp_path: Path, edit) -> Path:
    d = tmp_path / "LOT" / "SQ67P01-D1"
    shutil.copytree(_CAM / "SQ67P01-D1", d)
    p = d / "ProductInfo.ini"
    p.write_text(edit(p.read_text(encoding="latin-1")), encoding="latin-1")
    return d


@pytest.mark.parametrize("edit", [
    pytest.param(lambda t: re.sub(r"rMax=\d+", "rMax=400", t), id="outside-wafer"),
    pytest.param(lambda t: re.sub(r"cMin=\d+", "cMin=400", t), id="min-over-max"),
    pytest.param(lambda t: re.sub(r"(?m)^rMax=.*\n", "", t), id="missing-key"),
    pytest.param(lambda t: re.sub(r"XDieIndex=[\d.]+", "XDieIndex=920.500", t), id="other-pitch"),
    pytest.param(lambda t: t.replace("[ExportData]", "[Other]"), id="no-section"),
])
def test_bad_export_falls_back(tmp_path, edit):
    g = wg.camtek_geometry(_copy(tmp_path, edit))
    assert "[ExportData]" not in g.source
    assert g.col_origin == 2                  # 기존 중심 유도로 돌아간다


def test_unknown_center_falls_back(tmp_path):
    d = _copy(tmp_path, lambda t: t)
    p = d / "Params_WaferInfo.ini"
    p.write_text(re.sub(r"Center_([XY])=[\d.]+", r"Center_\1=0.000000",
                        p.read_text(encoding="latin-1")), encoding="latin-1")
    (d / "Wafer2Table.ini").unlink()
    assert "[ExportData]" not in wg.camtek_geometry(d).source


def test_die_map_outside_range_falls_back(tmp_path, monkeypatch):
    d = _copy(tmp_path, lambda t: t)
    monkeypatch.setattr(wg, "die_map_cells", lambda *_: frozenset({(4, 100), (100, 100)}))
    assert "[ExportData]" not in wg.camtek_geometry(d).source
