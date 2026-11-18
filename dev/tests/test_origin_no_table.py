"""번호표가 없는 웨이퍼의 col·row 원점 — 2026-11-13 규칙 회귀 가드(합성 폴더).

* 정렬 사진(``.wlup``)은 pitch 검산에서 뺀다 — 섞이면 TB500 Multi 31/32 폴더가 절대좌표로
  떨어졌다(사용자 결정 "정렬사진은 무시").
* 다른 웨이퍼(같은 셋업)의 번호표는 **빌리지 않는다**(사용자 결정 2026-11-14 — 배포받는 사람은
  Conder 만 봐서 쓸 수 없는 방법이라 현택 PC 에서도 쓰지 않는다).  형제 번호표가 있어도 ⚠.
* 작은 die(두 축 ≤ 1.6 mm): 그 웨이퍼의 die 맵(70% 이상)을 ±1 클램프 없이 쓴다(61/61,
  GNF7K21 장비 (1, 230)).  맵이 없으면 ⚠.
* '중심식=격자식' 일치는 더 이상 ⚠ 를 빼지 않는다(HORNET CCU 2폴더 반증).

"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from aoi_verification.app.coords import camtek_ini, clear_caches
from aoi_verification.app.coords import wafer_geometry as wg
from aoi_verification.app.coords.models import NUMBER_WARN_NO_TABLE

DIA = 300000.0


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _wafer(folder: Path, px: float, py: float, cx: float, cy: float, entries=(),
           export=None, die_map=None) -> Path:
    """entries: [(이름, X, Y)] · export: (cMin, cMax, rMin, rMax) · die_map: {(i, j)} stage 칸."""
    folder.mkdir(parents=True)
    entries = list(entries) or [("d1.c.1", cx + 0.3 * px, cy + 0.3 * py),
                                ("d2.c.2", cx + 3.4 * px, cy - 2.6 * py)]
    (folder / "ColorImageGrabingInfo.ini").write_text("".join(
        f"[{n}.jpeg]\nX={X}\nY={Y}\nCol={math.floor(X / px) if '.wlup' not in n else 0}\n"
        f"Row={math.floor(Y / py) if '.wlup' not in n else 0}\n" for n, X, Y in entries),
        encoding="utf-8")
    (folder / "Params_WaferInfo.ini").write_text(
        f"[Geometric]\nDieStep_X={px}\nDieStep_Y={py}\nDiameter={DIA}\n"
        f"Center_X={cx}\nCenter_Y={cy}\n", encoding="utf-8")
    prod = f"[Geometric]\nXDieIndex={px}\nYDieIndex={py}\n"
    if export:
        prod += "[ExportData]\n" + "".join(
            f"{k}={v}\n" for k, v in zip(("cMin", "cMax", "rMin", "rMax"), export))
    (folder / "ProductInfo.ini").write_text(prod, encoding="utf-8")
    if die_map:
        (folder / wg._DIE_MAP_FILE).write_bytes(b"".join(
            struct.pack("<dd", i * px + 5.0, j * py + 5.0) for i, j in sorted(die_map)))
        (folder / (wg._DIE_MAP_FILE + ".md")).write_text(
            '<RecordSize Size="16"/><Field Name="x" Offset="0" Vartype="5"/>'
            '<Field Name="y" Offset="8" Vartype="5"/>', encoding="utf-8")
    return folder


def _warns(folder: Path) -> set[str]:
    return {c.number_warn for c in camtek_ini.load_folder(folder).values()}


# ── 정렬 사진 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name, want", [
    ("148988.179602.wlup", True), ("74488.179605.wlup.-12.jpeg", True),
    ("74488.179605.WLUP", True), ("31935.161285.c.-1048307564", False),
    ("wlupx.1.c", False),
])
def test_alignment_photo_name(name, want):
    assert wg.is_alignment_photo(name) is want


def test_alignment_photos_do_not_break_the_pitch_check(tmp_path):
    px, py, cx, cy = 37247.9, 44905.3, 167449.0, 179674.0
    f = _wafer(tmp_path / "S" / "0" / "W", px, py, cx, cy, entries=[
        ("d1.c.1", 150000.0, 100000.0), ("d2.c.2", 200000.0, 250000.0),
        ("148988.179602.wlup", 148988.3, 179602.9)])          # Col=Row=0 — 결함과 다른 규칙
    g = wg.camtek_geometry(f)
    assert g is not None and (g.pitch_x, g.pitch_y) == (px, py)


# ── 큰 die: 같은 셋업 번호표를 빌리지 않는다 ─────────────────────────────

PX, PY = 8627.0, 7820.4
C1 = (156848.9, 155044.1)                     # 번호표 웨이퍼의 중심
T1 = (2, 34, 2, 37)                           # 그 번호표 → 원점 (2, 37)


def test_big_die_never_borrows_from_another_layer_of_the_same_setup(tmp_path):
    setup = tmp_path / "LOT" / "6321"
    _wafer(setup / "BNU" / "P35004-09G5", PX, PY, *C1, export=T1)
    tgt = _wafer(setup / "BNU BUMPTOP" / "P35004-01E2", PX, PY, C1[0] + PX, C1[1] + PY)
    g = wg.camtek_geometry(tgt)
    assert not wg.is_trusted_source(g.source)
    assert _warns(tgt) == {NUMBER_WARN_NO_TABLE}


def test_own_table_comes_first(tmp_path):
    tgt = _wafer(tmp_path / "LOT" / "6321" / "A" / "W1", PX, PY, *C1, export=T1)
    g = wg.camtek_geometry(tgt)
    assert wg.EXPORT_SOURCE_TAG in g.source and (g.col_origin, g.row_total) == (2, 37)
    assert _warns(tgt) == {""}


# ── 작은 die: die 맵 ──────────────────────────────────────────────────────

SPX, SPY = 1500.0, 1450.0
SC = (150692.0, 151161.0)


def _inner_cells(cx, cy, r, px, py):
    return {(i, j) for i in range(math.floor((cx - r) / px), math.ceil((cx + r) / px))
            for j in range(math.floor((cy - r) / py), math.ceil((cy + r) / py))
            if all(math.hypot(x - cx, y - cy) <= r
                   for x in (i * px, (i + 1) * px) for y in (j * py, (j + 1) * py))}


def test_small_die_uses_its_die_map_even_far_from_the_center_formula(tmp_path):
    # 가장자리 8 mm 를 스캔하지 않은 맵 — 원점이 중심 유도와 1 넘게 달라 예전 클램프는 버렸다.
    cells = _inner_cells(*SC, DIA / 2 - 8000.0, SPX, SPY)
    tgt = _wafer(tmp_path / "LOT" / "6322" / "CWM RE" / "W", SPX, SPY, *SC, die_map=cells)
    g = wg.camtek_geometry(tgt)
    want = (min(i for i, _ in cells), max(j for _, j in cells))
    assert (g.col_origin, g.row_total) == want
    derived = (wg._col_origin(SC[0], DIA, SPX), wg._row_total(SC[1], DIA, SPY))
    assert abs(want[0] - derived[0]) > 1 or abs(want[1] - derived[1]) > 1
    assert wg.SMALL_MAP_SOURCE_TAG in g.source and _warns(tgt) == {""}


def test_small_die_row_above_200_is_accepted(tmp_path):
    px, py = 960.0, 665.0
    cells = _inner_cells(*SC, DIA / 2 - 3000.0, px, py)
    assert max(j for _, j in cells) > 200
    tgt = _wafer(tmp_path / "LOT" / "6322" / "L" / "W", px, py, *SC, die_map=cells)
    assert wg.SMALL_MAP_SOURCE_TAG in wg.camtek_geometry(tgt).source


def test_small_die_partial_map_is_not_used(tmp_path):
    cells = {c for c in _inner_cells(*SC, DIA / 2, SPX, SPY)
             if math.hypot((c[0] + .5) * SPX - SC[0], (c[1] + .5) * SPY - SC[1]) > DIA / 2 - 20000}
    tgt = _wafer(tmp_path / "LOT" / "6322" / "EDGE" / "W", SPX, SPY, *SC, die_map=cells)
    g = wg.camtek_geometry(tgt)
    assert wg.SMALL_MAP_SOURCE_TAG not in g.source
    assert _warns(tgt) == {NUMBER_WARN_NO_TABLE}


def test_small_die_does_not_borrow(tmp_path):
    """형제 번호표가 있어도 빌리지 않고 ⚠."""
    setup = tmp_path / "LOT" / "6322"
    _wafer(setup / "SNU" / "W1", SPX, SPY, *SC, export=(5, 190, 3, 200))
    tgt = _wafer(setup / "SNU-EDGE" / "W2", SPX, SPY, *SC)
    assert not wg.is_trusted_source(wg.camtek_geometry(tgt).source)
    assert _warns(tgt) == {NUMBER_WARN_NO_TABLE}


# ── 실물: HORNET CCU (2026-11-13 장비 화면) ───────────────────────────────
# 같은 셋업(6321)의 CCU·CCU RE 웨이퍼 파일(사진 목록 INI 는 앞 12항목만).  CCU P8V798-12B2 는
# 번호표가 없고, 장비 화면이 사진 299227.110236 을 (61, 44) 로 보여 줬다(관측).  빌리기를 쓰지
# 않으므로 앱은 이 번호를 확정하지 못한다 — 대신 반드시 ⚠ 를 띄워야 한다(조용히 틀리면 안 된다).

_HORNET = (Path(__file__).resolve().parents[1] / "좌표 확인" / "빌리기(같은 셋업)"
           / "HORNET 0861404PD-0A" / "6321")


@pytest.mark.skipif(not _HORNET.exists(), reason="dev/좌표 확인/빌리기(같은 셋업) 없음")
def test_hornet_ccu_without_table_is_warned():
    f = _HORNET / "CCU" / "P8V798-12B2"
    assert not wg.is_trusted_source(wg.camtek_geometry(f).source)
    c = camtek_ini.load_folder(f)["299227.110236.c.1956694204"]
    assert c.number_warn == NUMBER_WARN_NO_TABLE


@pytest.mark.skipif(not _HORNET.exists(), reason="dev/좌표 확인/빌리기(같은 셋업) 없음")
@pytest.mark.parametrize("wafer", ["P8V798-04F4", "P8V798-08D0"])
def test_hornet_ccu_hidden_table_is_warned(tmp_path, wafer):
    """번호표를 숨기면 형제 번호표가 있어도 빌리지 않고 ⚠ — 예전 '중심식=격자식' 조용히 틀림 방지."""
    import shutil
    setup = tmp_path / "6321"
    shutil.copytree(_HORNET, setup)
    pi = setup / "CCU" / wafer / "ProductInfo.ini"
    want = wg.camtek_geometry(_HORNET / "CCU" / wafer)
    assert wg.EXPORT_SOURCE_TAG in want.source and (want.col_origin, want.row_total) == (3, 70)
    text = pi.read_text(encoding="latin-1")
    pi.write_text(text.replace("[ExportData]", "[ExportDataHidden]"), encoding="latin-1")
    clear_caches()
    assert not wg.is_trusted_source(wg.camtek_geometry(setup / "CCU" / wafer).source)
