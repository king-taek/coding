"""Camtek 레시피의 화면 번호 시프트(``Recipe.ini [Display] DisplayColShift``) 회귀 가드.

★ 실물(2026-10-25, T254 FS UBM · AOI-3 · S/M ``TSC-FS``, 현택님 수집):
이 레시피는 ``DisplayColShift=-1`` 이다.  앱 col 9 인 결함을 장비 화면과 Conder LIVE 파일명은
8 로 적었고(관측, 현택님 화면 확인), 장비 맵에서도 같은 칸(왼쪽 10번째 열)이었다.
같은 웨이퍼의 LOT 맵 txt 는 시프트 없는 9 에 불량 bin 을 찍는다(관측, 42장 전부).

규칙(사용자 결정 2026-10-25 '표시만 화면 번호로'):
  · ``DefectCoord.col/row`` = 실제 die — 매칭·Wafer map 위치·KLA·LOT 맵 txt 와 같은 번호.
  · ``screen_col/screen_row`` = 화면 번호 — 사진 정보·엑셀·맵 툴팁 표기에만 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, camtek_live, clear_caches
from aoi_verification.app.coords import single_info

_GOLD = Path(__file__).resolve().parents[1] / "좌표 확인" / "화면시프트(T254 FS UBM)"
_CAM = _GOLD / "Camtek_TSC-FS"
_CON = _GOLD / "Conder_FS UBM"


def _names(f: Path) -> list[str]:
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def _map_bad_cells(f: Path) -> set[tuple[int, int]]:
    """LOT 맵 txt 의 불량 bin 칸 ``(열, 아래부터 행)``."""
    rows = [ln[8:].split() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.startswith("RowData:")]
    n = len(rows)
    return {(c, n - 1 - r) for r, row in enumerate(rows) for c, v in enumerate(row)
            if v not in ("___", "000")}


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _pairs():
    """(웨이퍼, LIVE 이름 해석, 최근접 INI 좌표) — die 안 x/y 2 µm 안에서 짝짓는다."""
    out = []
    for wafer in sorted(p for p in _CON.iterdir() if p.is_dir()):
        ini = list(camtek_ini.load_folder(_CAM / wafer.name).values())
        for n in _names(wafer / "사진목록.txt"):
            live = camtek_live.parse_live_name(Path(n).stem)
            best = min(ini, key=lambda c: (c.x - live.x) ** 2 + (c.y - live.y) ** 2)
            assert abs(best.x - live.x) <= 2 and abs(best.y - live.y) <= 2, n
            out.append((wafer.name, live, best))
    return out


def test_golden_sample_size():
    assert len(_pairs()) == 42
    assert len({w for w, _l, _c in _pairs()}) == 18


def test_recipe_shift_is_read():
    for wafer in _CAM.iterdir():
        assert camtek_ini.display_shift(wafer) == (-1, 0), wafer.name


def test_screen_number_matches_equipment_and_live_name():
    for wafer, live, c in _pairs():
        assert (c.screen_col, c.screen_row) == (live.col, live.row), wafer


def test_die_number_matches_lot_map_txt():
    """실제 die 번호(col/row)는 LOT 맵 txt 의 불량 칸과 같다 — 시프트를 섞으면 안 된다."""
    for wafer, _live, c in _pairs():
        assert (c.col, c.row) in _map_bad_cells(_CON / f"{wafer}.txt"), wafer
        assert c.col == c.screen_col + 1


def test_equipment_screen_example():
    """현택님이 장비 화면에서 확인한 결함 — 27125631EWE7 · 163866.201295.c.-1708372688."""
    c = camtek_ini.resolve(_CAM / "27125631EWE7" / "163866.201295.c.-1708372688.jpeg")
    assert (c.col, c.row) == (9, 4)
    assert (c.screen_col, c.screen_row) == (8, 4)


def test_info_and_excel_show_the_screen_number():
    """사진 정보 창과 엑셀(같은 생산자 ``single_info``)은 화면 번호를 쓴다."""
    m = single_info.coord_measures(
        _CAM / "27125631EWE7" / "163866.201295.c.-1708372688.jpeg")
    assert m[0].value == "8 / 4"


@pytest.mark.parametrize("text, want", [
    ("[Display]\nDisplayRowShift=0 ; Row Offset\nDisplayColShift=-1\n", (-1, 0)),
    ("[Display]\nDisplayColShift=0 ; Col Offset\t\t\nDisplayRowShift=2\n", (0, 2)),
    ("[Other]\nDisplayColShift=-1\n[Display]\n", (0, 0)),      # 다른 섹션 값은 무시
    ("[Display]\nDisplayColShift=abc\n", (0, 0)),             # 못 읽으면 0(경고)
    ("", (0, 0)),
])
def test_display_shift_parsing(tmp_path, text, want):
    (tmp_path / "Recipe.ini").write_text(text, encoding="utf-8")
    assert camtek_ini.display_shift(tmp_path) == want


def test_no_recipe_means_no_shift(tmp_path):
    assert camtek_ini.display_shift(tmp_path) == (0, 0)
