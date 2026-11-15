"""다중 레시피 스캔 — 레시피 N 의 die 이름표 차이(그 폴더 파일에서 읽음) 회귀 가드.

실물(현택님 수집 zip, 2026-11-15) ``dev/좌표 확인/다중레시피(이름표 차이)/``:

* ``E5025779EWD1`` · ``E5025794EWF5`` (AOI-15 KENDALL A0 PI6, 레시피 1 LIVE PI6 · 2 PI BUBBLE) —
  같은 정렬 die 를 레시피 1 은 ``ProductInfo.ini``, 레시피 2 는 ``Recipe2-ProductInfo.ini`` 의
  ``AlignmentDieCol/Row`` 로 서로 다르게 적는다.  두 파일의 ``DieTopLeft`` 는 반 칸 안이고
  ``DieLut.dat`` 레시피 2 범위는 레시피 1 범위를 그 차이만큼 옮긴 것이다.
  장비 화면 5장(관측 2026-11-15): EWF5 (2, 4) · (4, 0) · (5, 5), EWD1 (6, 1) 두 장(같은 Defect 를
  레시피 1 · 2 가 찍음).  고치기 전에는 EWD1 이 레시피 2 사진의 INI ``Col`` 때문에 검산이 깨져
  절대좌표였고, EWF5 는 DieLut 를 그대로 합쳐 끝 줄이 늘어 row 가 1 컸다.
* ``GX57529995`` (AOI-13 T256 ROTATION) — 정렬 die 번호만 레시피마다 다르고 ``DieTopLeft`` ·
  DieLut 범위는 같다 → 이름표 차이가 아니다(INI ``Col`` = 좌표 칸, 43장).  사진이 수집되지 않아
  ``사진목록.txt`` 는 INI 섹션 이름으로 적었다.

★ 기대값의 차이 (1, 1) 은 테스트가 적은 **관측값**이다.  앱 코드에는 상수가 없고 폴더 파일에서
읽는다 — 파일 값을 바꾸면 결과가 따라 바뀌는지도 여기서 본다(사용자 지시 2026-11-15 '하드코딩 금지').
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, clear_caches
from aoi_verification.app.coords import wafer_geometry as wg
from aoi_verification.app.coords.models import NUMBER_WARN_NO_GRID

_GOLD = Path(__file__).resolve().parents[1] / "좌표 확인" / "다중레시피(이름표 차이)"
_EWD1 = _GOLD / "E5025779EWD1(KENDALL PI6 AOI-15)"
_EWF5 = _GOLD / "E5025794EWF5(KENDALL PI6 AOI-15)"
_ROT = _GOLD / "GX57529995(T256 ROTATION AOI-13)"

# 장비 화면에서 읽은 번호(관측 2026-11-15).  Recipe.ini 화면 시프트는 0 이라 화면 번호 = col/row.
_SCREEN = [
    (_EWF5, "105568.101950.c.339984070.1", (2, 4)),
    (_EWF5, "159213.291069.c.920471119.1", (4, 0)),
    (_EWF5, "179454.51684.c.1246425468.1", (5, 5)),
    (_EWD1, "213038.282711.c.1656328571.1", (6, 1)),
    (_EWD1, "213036.282710.c.-1331915394.2", (6, 1)),
]
# 같은 Defect 를 레시피 1 · 2 가 찍은 두 장(EWD1).
_PAIR = ("213038.282711.c.1656328571.1", "213036.282710.c.-1331915394.2")


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _photo_names(folder: Path) -> set[str]:
    txt = (folder / "사진목록.txt").read_text(encoding="utf-8")
    return {ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")}


def _bbox(cells):
    return (min(c for c, _ in cells), max(c for c, _ in cells),
            min(r for _, r in cells), max(r for _, r in cells))


def _copy(tmp_path: Path, src: Path) -> Path:
    d = tmp_path / "LOT" / src.name
    shutil.copytree(src, d)
    return d


def _read(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    enc = "utf-16-le" if raw[:2] == b"\xff\xfe" else "latin-1"   # 바이트를 그대로 되쓴다
    return raw.decode(enc), enc


def _set_key(path: Path, key: str, value) -> None:
    """``key=값`` 한 줄만 바꾼다(줄 머리 고정 — ``NextDieTopLeft_X`` 같은 이웃 키는 그대로)."""
    txt, enc = _read(path)
    new, n = re.subn(rf"(?m)^({re.escape(key)}\s*=\s*)[^\r\n]*",
                     lambda m: m.group(1) + str(value), txt, count=1)
    assert n == 1, f"{key} 가 {path.name} 에 없다"
    path.write_bytes(new.encode(enc))


def _align(path: Path) -> tuple[int, int]:
    """테스트도 정렬 die 번호를 파일에서 읽는다."""
    return (int(wg._read_key(path, "AlignmentDieCol", 0, 1e6)),
            int(wg._read_key(path, "AlignmentDieRow", 0, 1e6)))


def _move_recipe2_labels(d: Path, dc: int, dr: int) -> None:
    """레시피 2 의 이름표를 (dc, dr) 더 옮긴 장비를 흉내 낸다 — 정렬 die 번호 · DieLut · INI 를 함께."""
    a = _align(d / "Recipe2-ProductInfo.ini")
    _set_key(d / "Recipe2-ProductInfo.ini", "AlignmentDieCol", a[0] + dc)
    _set_key(d / "Recipe2-ProductInfo.ini", "AlignmentDieRow", a[1] + dr)

    lut, enc = _read(d / wg._DIELUT_FILE)

    def _lut(m):
        rec = m.group(0)
        if 'RecipeNum="2"' not in rec:
            return rec
        rec = re.sub(r'\bCol="(-?\d+)"', lambda k: f'Col="{int(k.group(1)) + dc}"', rec)
        return re.sub(r'\bRow="(-?\d+)"', lambda k: f'Row="{int(k.group(1)) + dr}"', rec)
    (d / wg._DIELUT_FILE).write_bytes(re.sub(r"<DieLutData\b[^>]*>", _lut, lut).encode(enc))

    ini_path = d / "ColorImageGrabingInfo.ini"
    ini, enc = _read(ini_path)
    parts = re.split(r"(?m)^(?=\[)", ini)
    out = []
    for sec in parts:
        if re.search(r"(?m)^RecipeNumber=2\s*$", sec):
            sec = re.sub(r"(?m)^Col=(-?\d+)", lambda k: f"Col={int(k.group(1)) + dc}", sec)
            sec = re.sub(r"(?m)^Row=(-?\d+)", lambda k: f"Row={int(k.group(1)) + dr}", sec)
        out.append(sec)
    ini_path.write_bytes("".join(out).encode(enc))


@pytest.mark.parametrize("folder", [_EWD1, _EWF5])
def test_shift_is_the_alignment_die_difference_in_the_files(folder):
    a1 = _align(folder / "ProductInfo.ini")
    a2 = _align(folder / "Recipe2-ProductInfo.ini")
    assert (a2[0] - a1[0], a2[1] - a1[1]) == (1, 1)          # 관측
    assert wg.recipe_label_shifts(folder) == {2: (1, 1)}


@pytest.mark.parametrize("folder, stem, want", _SCREEN)
def test_equipment_screen_numbers(folder, stem, want):
    assert f"{stem}.jpeg" in _photo_names(folder)
    c = camtek_ini.load_folder(folder)[stem.lower()]
    assert (c.screen_col, c.screen_row) == want
    assert (c.col, c.row) == want
    assert c.number_warn == ""


def test_same_defect_gets_the_same_die_from_both_recipes():
    a, b = (s.lower() for s in _PAIR)
    rec = camtek_ini.load_recipe_folder(_EWD1)
    assert (rec[a], rec[b]) == (1, 2)
    raw = camtek_ini.load_raw_folder(_EWD1)
    dc, dr = wg.recipe_label_shifts(_EWD1)[2]
    assert (raw[b][2] - raw[a][2], raw[b][3] - raw[a][3]) == (dc, dr)   # INI 는 이름표 차이만큼 다르다
    co = camtek_ini.load_folder(_EWD1)
    assert (co[a].col, co[a].row) == (co[b].col, co[b].row)


@pytest.mark.parametrize("folder", [_EWD1, _EWF5])
def test_dielut_cells_are_in_the_recipe1_frame(folder):
    cells = wg.dielut_cells(folder)
    assert cells == wg._dielut_by_recipe(folder)[1]           # 레시피 2 칸을 되돌리면 레시피 1 과 같다
    assert _bbox(cells) == (1, 10, 1, 6)
    g = wg.camtek_geometry(folder)
    assert wg.DIELUT_SOURCE_TAG in g.source
    assert (g.col_origin, g.row_total) == (1, 6)


def test_shift_follows_the_files_not_a_constant(tmp_path):
    """이름표를 (2, 1) 옮긴 장비가 와도 파일 값대로 되돌린다 — 번호는 장비 화면 그대로."""
    d = _copy(tmp_path, _EWD1)
    _move_recipe2_labels(d, 1, 0)
    assert wg.recipe_label_shifts(d) == {2: (2, 1)}
    co = camtek_ini.load_folder(d)
    for stem in _PAIR:
        assert (co[stem.lower()].col, co[stem.lower()].row) == (6, 1)
    assert wg.dielut_cells(d) == wg._dielut_by_recipe(d)[1]


def test_other_alignment_die_is_not_a_label_shift(tmp_path):
    """① 레시피 2 의 정렬 die 자리가 반 칸 넘게 다르면(다른 die 를 골랐다) 차이를 쓰지 않는다 —
    고치기 전처럼 검산이 깨져 절대좌표로 간다."""
    d = _copy(tmp_path, _EWD1)
    q = d / "Recipe2-ProductInfo.ini"
    x = wg._read_key(q, "DieTopLeft_X", -1e7, 1e7)
    px = wg._read_key(d / "ProductInfo.ini", "XDieIndex")
    _set_key(q, "DieTopLeft_X", f"{x + px:.3f}")
    assert wg.recipe_label_shifts(d) == {}
    assert {c.number_warn for c in camtek_ini.load_folder(d).values()} == {NUMBER_WARN_NO_GRID}


def test_alignment_numbers_that_disagree_with_dielut_are_not_used(tmp_path):
    """② 정렬 die 번호 차이가 DieLut 범위의 이동과 다르면 쓰지 않는다."""
    d = _copy(tmp_path, _EWD1)
    a = _align(d / "Recipe2-ProductInfo.ini")
    _set_key(d / "Recipe2-ProductInfo.ini", "AlignmentDieCol", a[0] + 1)
    assert wg.recipe_label_shifts(d) == {}
    assert {c.number_warn for c in camtek_ini.load_folder(d).values()} == {NUMBER_WARN_NO_GRID}


def test_missing_alignment_number_gives_no_shift(tmp_path):
    d = _copy(tmp_path, _EWF5)
    _set_key(d / "Recipe2-ProductInfo.ini", "AlignmentDieCol", "")
    assert wg.recipe_label_shifts(d) == {}


def test_rotation_recipe_numbers_differ_but_labels_are_not_moved():
    """T256 ROTATION: 정렬 die 번호는 다르지만 DieLut 두 레시피 범위가 같다 → ② 가 막는다.
    INI ``Col`` 은 두 레시피 모두 좌표 칸 그대로다."""
    assert _align(_ROT / "ProductInfo.ini") != _align(_ROT / "Recipe2-ProductInfo.ini")
    assert wg.recipe_label_shifts(_ROT) == {}
    recs = wg._dielut_by_recipe(_ROT)
    assert _bbox(recs[1]) == _bbox(recs[2])
    g = wg.camtek_geometry(_ROT)
    assert g is not None and g.pitch_x > 0
    raw = camtek_ini.load_raw_folder(_ROT)
    assert len(raw) == len(_photo_names(_ROT)) == 43
    assert all(math.floor(X / g.pitch_x) == col for X, _, col, _ in raw.values())
    assert set(camtek_ini.load_recipe_folder(_ROT).values()) == {1, 2}


def test_single_recipe_folder_has_no_shift():
    other = Path(__file__).resolve().parents[1] / "좌표 확인" / "die목록(DieLut)" / "GX57007306(T254)"
    assert not list(other.glob("Recipe*-ProductInfo.ini"))
    assert wg.recipe_label_shifts(other) == {}
