"""AVAGO 재리뷰 **실물 골든** — PH3Q42 (15966PA0-BW2, AOI-11) 2장.

자료(`관측`/`파일`, 2026-10-02 사용자 수집 — 사진 없이 파일명·좌표 파일만):
``dev/좌표 확인/AVAGO 재리뷰(PH3Q42)/<WaferID>/``
  * ``map_OR/<WaferID>.txt``          1차 리뷰 Map(``…\\2. FVI\\1. OR``)
  * ``scanresult/``                   Camtek INI · Params_WaferInfo.ini · s_DieLocation.dat(.md)
                                      · 사진목록.txt(Scanresult 사진 파일명)
  * ``사진목록_#11재저장.txt``          ``…\\2. FVI\\2. #11 재 저장`` 사진 파일명 —
                                      이름에 장비 화면 (col, row)·die 내부 x·y(µm)가 있다

이 골든이 못 박는 것(서로 독립인 세 소스가 같은 답):
1. 앱의 Camtek INI 변환(die (col,row)·x·y)이 장비가 이름에 쓴 값과 **같다** —
   #11 사진 45장 전부 같은 die, x·y 차이 ≤ 10 µm (42장은 0 µm, 'gr' 재방문 사진 3장이 1~9 µm).
2. 앱의 정렬(장비 die 목록 ↔ Map, 평행이동 (2,2))로 구한 Map 칸이 #11 의
   ``(col, 아래부터 row)`` 와 **45장 전부 같고**, 그 칸은 전부 Map Reject bin 이다.
   경쟁 가설 'row 를 위부터' 는 45장 전부 양품 bin(000)이나 die 없는 칸에 떨어진다 → 반증(R1).
3. 03G6 은 예전에 'Map 2962 ≠ 장비 2961' 로 막혔던 웨이퍼다 — 장비 미검사 die 1칸
   (3,17)이 있어도 정렬되고 Reject die 사진 18장이 빠진다.

'#11 재 저장' 은 1차 리뷰의 Reject die 사진으로 보인다(`유도` — 05F4 는 Map Reject die
11칸 = #11 die 11칸).  이 해석은 골든이 아니라 정황이다.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, camtek_live, rereview as rr, wafer_geometry

ROOT = Path(__file__).resolve().parents[1] / "좌표 확인" / "AVAGO 재리뷰(PH3Q42)"

# (웨이퍼, #11 사진 수, 장비 미검사 die(맵 칸), 앱이 뺄 사진 수)
CASES = [("PH3Q42-05F4", 27, [], 28),
         ("PH3Q42-03G6", 18, [(3, 17)], 18)]


@pytest.fixture(autouse=True)
def _clear_caches():
    for fn in (camtek_ini.load_folder, camtek_ini.load_raw_folder,
               camtek_ini.load_abs_folder, camtek_ini.load_recipe_folder,
               wafer_geometry.camtek_geometry):
        fn.cache_clear()
    yield


def _names(p: Path) -> list[str]:
    return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def _plan(w: str):
    sdir = ROOT / w / "scanresult"
    imgs = [sdir / n for n in _names(sdir / "사진목록.txt")]
    return rr.plan_wafer(w, sdir, imgs, ROOT / w / "map_OR", wafer=w), sdir, imgs


@pytest.mark.parametrize("wafer,n11,unscanned,n_excl", CASES)
def test_alignment_and_exclusion(wafer, n11, unscanned, n_excl):
    plan, _sdir, _imgs = _plan(wafer)
    assert plan.offset == (2, 2) and not plan.warnings
    assert plan.unscanned == unscanned
    assert len(plan.excluded) == n_excl


@pytest.mark.parametrize("wafer,n11,unscanned,n_excl", CASES)
def test_filename_coords_match_app_and_map(wafer, n11, unscanned, n_excl):
    plan, sdir, imgs = _plan(wafer)
    rm = plan.reject_map
    coords = camtek_ini.load_folder(sdir)
    by_stem = {p.stem.lower(): p for p in imgs}
    grid = [ln.split(":", 1)[1].split()
            for ln in (ROOT / wafer / "map_OR" / f"{wafer}.txt").read_text().splitlines()
            if ln.lower().startswith("rowdata")]
    names = _names(ROOT / wafer / "사진목록_#11재저장.txt")
    assert len(names) == n11
    for name in names:
        ln = camtek_live.parse_live_name(Path(name).stem)
        assert ln is not None, name
        # 1) 같은 die 의 INI 결함 중 가장 가까운 것 — x·y 가 10 µm 안.
        same_die = [(s, c) for s, c in coords.items() if (c.col, c.row) == (ln.col, ln.row)]
        assert same_die, f"{name}: 앱이 계산한 die 에 결함이 없다"
        stem, c = min(same_die, key=lambda t: math.hypot(t[1].x - ln.x, t[1].y - ln.y))
        assert math.hypot(c.x - ln.x, c.y - ln.y) <= 10.0, name
        # 2) 앱 정렬의 Map 칸 == (col, 아래부터 row), 그 칸은 Reject 이고 사진은 빠졌다.
        path = by_stem[stem]
        cell = (ln.col, rm.rows - 1 - ln.row)
        assert plan.cell_of[path] == cell, name
        assert cell in rm.rejects and path in plan.excluded, name
        # 경쟁 가설 'row 를 위부터' — 양품 bin 이거나 die 없는 칸이다(반증).
        assert ln.row >= rm.rows or grid[ln.row][ln.col] in (rm.good_bin, "___"), name
