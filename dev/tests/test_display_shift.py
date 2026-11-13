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


# ── 화면 방향(WaferDisplayOrientation)에 따라 장비가 시프트를 더하는지가 갈린다(관측 2026-11-14) ──
# 실물 [Display] 섹션을 그대로 옮겼다.  KENDALL 은 장비 화면(현택님 확인)·Conder 이름 11장이
# 번호표 번호 그대로였고, T254 H-M1 은 번호표가 있는데도 Conder 이름 29/30 이 시프트를 더했다.
_KENDALL_A0_PI3 = ("[Display]\nWaferDisplayOrientation=1 ; System Default\n"
                   "DisplayRowShift=-3\t\nDisplayColShift=-1\t\n")
_T254_H_M1 = ("[Display]\nWaferDisplayOrientation=2 ; Left Bottom\n"
              "DisplayRowShift=0 ; Row Offset\nDisplayColShift=-1 ; Col Offset\n")
_TB500_R_ETCH = ("[Display]\nWaferDisplayOrientation=1 ; System Default\n"
                 "DisplayRowShift=-6 ; Row Offset\nDisplayColShift=1 ; Col Offset\n")


@pytest.mark.parametrize("text, want", [
    (_KENDALL_A0_PI3, (0, 0)),          # System Default — 장비가 안 더함(관측)
    (_T254_H_M1, (-1, 0)),              # Left Bottom — 더함(관측, 번호표 있음)
    (_TB500_R_ETCH, (0, 0)),            # 장비 화면 (4,2) 확인(2026-11-14) — 더했다면 (5,−4)
    ("[Display]\nWaferDisplayOrientation=3\nDisplayColShift=-1\n", (-1, 0)),   # 미확인 값: 예전처럼
])
def test_shift_only_when_display_orientation_is_left_bottom(tmp_path, text, want):
    (tmp_path / "Recipe.ini").write_text(text, encoding="utf-8")
    assert camtek_ini.display_shift(tmp_path) == want


def test_golden_recipes_are_left_bottom():
    """골든(T254 FS UBM 42장)이 시프트를 더하는 건 화면 방향이 2 이기 때문이다."""
    for wafer in _CAM.iterdir():
        text = (wafer / "Recipe.ini").read_text(encoding="utf-8", errors="ignore")
        assert "WaferDisplayOrientation=2" in text, wafer.name


# ── Conder scan(LIVE 파일명) 폴더: Recipe.ini 가 없어 LOT 맵 txt 로 시프트를 추정한다 ──

def _materialize_conder(tmp_path: Path) -> Path:
    """사진목록의 이름으로 빈 파일을 만든 Conder LOT 폴더(사진 실물은 저장소에 없다)."""
    lot = tmp_path / "FS UBM"
    lot.mkdir()
    for t in _CON.glob("*.txt"):
        (lot / t.name).write_bytes(t.read_bytes())
    for wafer in (p for p in _CON.iterdir() if p.is_dir()):
        (lot / wafer.name).mkdir()
        for n in _names(wafer / "사진목록.txt"):
            (lot / wafer.name / n).touch()
    return lot


def test_conder_lot_shift_is_inferred_from_the_lot_map(tmp_path):
    lot = _materialize_conder(tmp_path)
    assert camtek_live.lot_display_shift(lot) == (-1, 0)


def test_conder_names_resolve_to_the_same_die_as_the_camtek_ini(tmp_path):
    """같은 결함을 Camtek INI 로 보든 Conder 파일명으로 보든 실제 die·화면 번호가 같다."""
    from aoi_verification.app.coords import resolve
    lot = _materialize_conder(tmp_path)
    for wafer, live, ini in _pairs():
        name = next(n for n in _names(_CON / wafer / "사진목록.txt")
                    if camtek_live.parse_live_name(Path(n).stem) == live)
        c = resolve(lot / wafer / name)
        assert (c.col, c.row) == (ini.col, ini.row), name
        assert (c.screen_col, c.screen_row) == (live.col, live.row), name


def _synthetic_lot(tmp_path: Path, bad_cols, names) -> Path:
    """1행 5열 맵(불량 칸 ``bad_cols``) + LIVE 이름들로 된 LOT 폴더."""
    lot = tmp_path / "LOT"
    (lot / "W1").mkdir(parents=True)
    row = " ".join("009" if c in bad_cols else "000" for c in range(5))
    (lot / "W1.txt").write_text(
        f"WAFER:W1\nFNLOC:180\nROWCT:1\nCOLCT:5\nBCEQU:000\nRowData:{row}\n",
        encoding="utf-8")
    for n in names:
        (lot / "W1" / n).touch()
    return lot


def _live(col, x=10.0):
    return f"R_DEV_REC_LOT_W1_{col}_0_Foreign_{x}_5.0.jpg"


def test_unshifted_lot_stays_zero(tmp_path):
    lot = _synthetic_lot(tmp_path, {1, 2, 3}, [_live(1), _live(2), _live(3)])
    assert camtek_live.lot_display_shift(lot) == (0, 0)


def test_shift_needs_several_distinct_cells(tmp_path):
    """한 칸에 사진이 몰려 있으면(근거 1칸) 시프트를 채택하지 않는다."""
    lot = _synthetic_lot(tmp_path, {3}, [_live(2, x) for x in (1.0, 2.0, 3.0, 4.0)])
    assert camtek_live.lot_display_shift(lot) == (0, 0)


def test_shift_rejected_when_zero_also_fits(tmp_path):
    """시프트 0 으로도 상당수가 맞으면(섞임) 0 을 유지한다."""
    lot = _synthetic_lot(tmp_path, {1, 2, 3, 4},
                         [_live(0), _live(1), _live(2), _live(3)])
    assert camtek_live.lot_display_shift(lot) == (0, 0)


def test_no_map_means_no_shift(tmp_path):
    (tmp_path / "W1").mkdir()
    (tmp_path / "W1" / _live(2)).touch()
    assert camtek_live.lot_display_shift(tmp_path) == (0, 0)


# ---------------------------------------------------------------------------
# 시프트를 확인하지 못한 '시프트 쓴 적 있는 Job' — 번호는 그대로, 노란 경고만(사용자 결정 2026-11-14)
# ---------------------------------------------------------------------------
_RISK = "R_T254 LIVE_0858711PD_FS UBM"


def _risk_live(col, x=10.0):
    return f"{_RISK}_UFF-FS_W1_{col}_0_Foreign_{x}_5.0.jpg"


def test_risk_job_is_read_from_the_file_name_prefix():
    assert camtek_live.shift_risk_job(Path(_risk_live(2)).stem)
    assert not camtek_live.shift_risk_job(Path(_live(2)).stem)
    assert not camtek_live.shift_risk_job("R_T254 LIVE_0858711PD_FS UBMX_A_W1_2_0_F_1_2")


def test_shift_confirmed_only_with_clear_map_evidence(tmp_path):
    (tmp_path / "a").mkdir()
    assert camtek_live.lot_shift_confirmed(_materialize_conder(tmp_path / "a"))          # −1 확인
    zero = _synthetic_lot(tmp_path / "b", {0, 2, 4}, [_live(0), _live(2), _live(4)])
    assert camtek_live.lot_display_shift(zero) == (0, 0) and camtek_live.lot_shift_confirmed(zero)
    blank = _synthetic_lot(tmp_path / "c", set(), [_live(1), _live(2)])
    assert not camtek_live.lot_shift_confirmed(blank)                                    # 불량 표시 없음


def test_wafer_map_warns_for_a_risk_job_without_map_evidence(tmp_path):
    from aoi_verification.app.coords import resolve_batch
    from aoi_verification.app.coords import wafer_map as wm
    lot = _synthetic_lot(tmp_path / "risk", set(), [_risk_live(1), _risk_live(3)])
    data = wm.build_map(resolve_batch(sorted((lot / "W1").iterdir())))
    warns = dict(wm.map_warnings(data))
    assert warns.get(wm.WARN_SHIFT_UNKNOWN) == 2
    assert wm.WARN_SHIFT_UNKNOWN not in wm.DANGER_CODES
    assert all(p.col in (1, 3) for p in data.points)                 # 번호는 고치지 않는다
    plain = _synthetic_lot(tmp_path / "plain", set(), [_live(1), _live(3)])
    data = wm.build_map(resolve_batch(sorted((plain / "W1").iterdir())))
    assert wm.WARN_SHIFT_UNKNOWN not in dict(wm.map_warnings(data))
