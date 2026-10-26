"""die 맵 인덱스 상한 = 웨이퍼 지름 ÷ die 크기(+1), 기존 200 보다 좁히지 않는다(사용자 결정 2026-10-25, 안 A).

★ 실물(관측, 2026-10-25): QORVO CTX · CTK(pitch 919.8)의 die 맵은 인덱스가 322 까지 가서 고정
상한 200 에 걸려 버려졌고, Wafer map 에 die 칸이 안 그려졌다.  수집한 실물 맵 45개의 최대
인덱스는 전부 ``지름 / pitch`` 이하였다(QORVO 322 < 326, TB500 8 ≤ 8.05).  상한을 올려도
QORVO 번호(ExportData 5/319 · 6/318)는 그대로였다(관측).  실물 맵은 5~10 MB 라 저장소에
두지 않고 합성 맵으로 규칙만 못 박는다(R4: 골든이 아니라 규칙 가드).
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from aoi_verification.app.coords import clear_caches
from aoi_verification.app.coords import wafer_geometry as wg

P = 919.8          # QORVO 실측 pitch


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    wg._dat_cells.cache_clear()
    yield
    clear_caches()
    wg._dat_cells.cache_clear()


def _folder(tmp_path: Path, cells) -> Path:
    d = tmp_path / "w"
    d.mkdir()
    (d / "Params_WaferInfo.ini").write_text(
        f"[Geometry]\nDieStep_X={P}\nDieStep_Y={P}\n[Geometric]\nDiameter=300000.000000\n",
        encoding="utf-8")
    recs = b"".join(bytes(16) + struct.pack("<dd", i * P + 10.0, j * P + 10.0)
                    for i, j in sorted(cells))
    (d / wg._DIE_MAP_FILE).write_bytes(recs)
    (d / (wg._DIE_MAP_FILE + ".md")).write_text(
        '<root><RecordSize Size="32"/><Fields>'
        '<Field Name="x" Id="1" Offset="16" Vartype="5"/>'
        '<Field Name="y" Id="2" Offset="24" Vartype="5"/>'
        '</Fields></root>', encoding="utf-8")
    return d


def _ring(lo: int, hi: int) -> set[tuple[int, int]]:
    return {(i, j) for i in range(lo, hi + 1) for j in (lo, hi)} | \
           {(i, j) for j in range(lo, hi + 1) for i in (lo, hi)}


def test_cap_follows_wafer_size_but_never_narrows():
    assert wg._die_index_cap(300000.0, P, P) == 328              # ceil(326.2) + 1
    assert wg._die_index_cap(300000.0, 37248.0, 44905.0) == wg._MAX_DIE_INDEX   # 큰 die: 기존 200


def test_small_die_map_over_200_is_kept(tmp_path):
    """QORVO 처럼 인덱스가 200 을 넘어도 웨이퍼 안이면 맵을 쓴다(예전엔 버렸다)."""
    cells = _ring(6, 322)
    assert len(cells) >= wg._MIN_DIE_MAP
    assert wg.die_map_cells(_folder(tmp_path, cells), P, P) == frozenset(cells)


def test_map_beyond_the_wafer_is_still_rejected(tmp_path):
    """웨이퍼 지름을 넘는 인덱스(엉뚱한 pitch·깨진 맵)는 여전히 버린다."""
    assert wg.die_map_cells(_folder(tmp_path, _ring(6, 340)), P, P) is None


_CTX = (Path(__file__).resolve().parents[1] / "좌표 확인" / "장비격자(ExportData)" / "Camtek_CTX"
        / "SQ67P25-G3")


def test_export_grid_folder_still_draws_its_die_map(tmp_path):
    """번호를 장비 번호표로 정한 폴더도 Wafer map 은 die 맵 칸을 그린다.

    번호표 채택이 기하 ``source`` 를 바꾸면서 '맵을 채택했을 때만 그린다' 조건이 빠져,
    번호표가 있는 제품 전부가 계산 격자로 그려지고 있었다(관측: 실물 12 LOT 중 TI CCG 만 맵)."""
    import math
    import shutil
    from aoi_verification.app.coords import wafer_map as wm
    d = tmp_path / "LOT" / _CTX.name
    shutil.copytree(_CTX, d)
    g = wg.camtek_geometry(d)
    assert wg.EXPORT_SOURCE_TAG in g.source
    cx, cy = wg._wafer_center(d)
    r = 150000.0 - 3000.0
    cells = {(i, j) for i in range(g.col_origin, 330) for j in range(0, g.row_total + 1)
             if all(math.hypot(x - cx, y - cy) <= r
                    for x in (i * P, (i + 1) * P) for y in (j * P, (j + 1) * P))}
    (d / "s_DieLocation.dat").write_bytes(b"".join(
        bytes(16) + struct.pack("<dd", i * P + 10.0, j * P + 10.0) for i, j in sorted(cells)))
    (d / "s_DieLocation.dat.md").write_text(
        '<root><RecordSize Size="32"/><Fields>'
        '<Field Name="x" Id="1" Offset="16" Vartype="5"/>'
        '<Field Name="y" Id="2" Offset="24" Vartype="5"/>'
        '</Fields></root>', encoding="utf-8")
    clear_caches()
    wm._camtek.cache_clear()
    assert wg.camtek_geometry(d).col_origin == 5                # 번호는 그대로 번호표
    assert wm._camtek(d).cells == frozenset(cells)
    # 부분 맵은 여전히 그리지 않는다
    (d / "s_DieLocation.dat").write_bytes(b"".join(
        bytes(16) + struct.pack("<dd", i * P + 10.0, j * P + 10.0)
        for i, j in sorted(cells)[: len(cells) // 3]))
    clear_caches()
    wg._dat_cells.cache_clear()
    wm._camtek.cache_clear()
    assert wm._camtek(d).cells is None
