"""LIVE 파일명 슬롯의 **웨이퍼 맵 텍스트**(LOT 폴더의 ``<WaferID>.txt``)를 읽는다.

LIVE 파일명 사진만 든 LOT 에는 ``Params_WaferInfo.ini``·``s_DieLocation.dat`` 이 없다.
대신 LOT 폴더에 슬롯(웨이퍼)마다 이런 파일이 있다(관측: 제품 8종 · 9 LOT · 63/63 슬롯)::

    DEVICE:AST254-AG6S2-6CAA
    WAFER:GX57004924
    FNLOC:180                ← 노치 위치(도). 관측은 전부 180 = 아래
    ROWCT:9
    COLCT:19
    BCEQU:000                ← 양품 bin
    DUTMS:mm
    XDIES:14.28              ← 비어 있거나 0.0 인 파일이 33개 중 18개
    YDIES:30.99
    RowData:___ ___ 000 @@@ 003 …   ← 첫 줄이 맨 위 행, ``___`` = die 없음

규약(관측 — 결함 사진 칸이 불량 bin 에 떨어지는지로 대조, 185칸 중 184칸):

* LIVE 파일명 ``col`` = RowData 열 번호(왼쪽부터 0), ``row`` = **아래부터 0**
  (= ``ROWCT − 1 − 위에서부터의 줄 번호``).  위부터·좌우 뒤집기·1-based·축 교환은
  같은 표본에서 전부 반증됐다.  나머지 1칸은 die 는 있으나 bin 이 양품이었다.

확인하지 못한 것 — 그래서 **쓰지 않거나 조건부로만** 쓴다:

* ``FNLOC`` 이 180 이 아닌 파일은 실물이 없다(방향 미확인) → 쓰지 않는다(경고).
* ``XDIES/YDIES`` 는 같은 웨이퍼라도 파일마다 다른 값이 있었다(BS CUP 14.28×30.99 vs
  ASSY 14.797×27.3815 — 사진 y 최댓값 30,944 µm 가 후자를 넘는다).  die 간격인지 die
  크기인지도 미구분 → 호출부가 **사진 좌표가 그 안에 다 들어갈 때만** 쓴다.
* ``REFPX/REFPY`` 의 뜻은 모른다 — 쓰지 않는다.

순수 파싱(:func:`parse`)은 Qt·파일 없이 헤드리스 테스트한다(``dev/tests/test_wafer_txt.py``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .ini_text import read_ini_text

__all__ = ["WaferTxt", "parse", "load"]

_LOG = logging.getLogger("aoi.coords")

_EMPTY = re.compile(r"^[_.\-]+$")            # die 없음 표기(관측: '___')
_KEY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
_UNIT_UM = {"mm": 1000.0, "um": 1.0, "µm": 1.0}
# 방향을 실물로 확인한 노치 위치 — 이 밖의 값은 맵을 뒤집어 그릴 수 있어 쓰지 않는다.
_VERIFIED_FNLOC = {"180"}
# 이보다 큰 파일은 웨이퍼 맵이 아니다(관측 파일은 1 KB 안팎).
_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WaferTxt:
    """웨이퍼 맵 한 장.  ``cells`` 는 die 가 있는 칸 ``(col, j)`` — ``j`` 는 **위에서부터**
    줄 번호(= Camtek stage y 인덱스와 같은 방향).  LIVE 파일명 ``row`` 는 ``rows − 1 − j``."""
    path: Path
    rows: int
    cols: int
    cells: frozenset
    die_x: Optional[float]      # µm — 파일 값(뜻 미구분, 호출부가 사진으로 검증)
    die_y: Optional[float]
    fnloc: str


def _die_um(head: dict, key: str) -> Optional[float]:
    mul = _UNIT_UM.get(head.get("DUTMS", "").strip().lower())
    try:
        v = float(head.get(key, ""))
    except ValueError:
        return None
    return v * mul if mul and v > 0 else None


def parse(text: str, path: Path, wafer: str = "") -> Optional[WaferTxt]:
    """맵 텍스트 → :class:`WaferTxt`.  형식이 아니거나 못 믿으면 ``None``(경고).

    ``wafer`` 를 주면 머리의 ``WAFER:`` 와 같아야 한다(다른 웨이퍼 맵 오용 방지)."""
    head: dict[str, str] = {}
    grid: list[list[str]] = []
    for ln in text.splitlines():
        if ln.strip().lower().startswith("rowdata"):
            grid.append(ln.split(":", 1)[1].split() if ":" in ln else [])
            continue
        m = _KEY.match(ln)
        if m and m.group(1).upper() not in head:
            head[m.group(1).upper()] = m.group(2)
    if not grid:
        return None
    rows, cols = len(grid), len(grid[0])
    try:
        rowct, colct = int(head.get("ROWCT", rows)), int(head.get("COLCT", cols))
    except ValueError:
        rowct = colct = -1
    if any(len(r) != cols for r in grid) or (rowct, colct) != (rows, cols):
        _LOG.warning("웨이퍼 맵 %s 의 모양이 머리(ROWCT/COLCT=%s/%s)와 다르다 — 쓰지 않는다",
                     path, head.get("ROWCT"), head.get("COLCT"))
        return None
    name = head.get("WAFER", "").strip()
    if wafer and name and name.lower() != wafer.lower():
        _LOG.warning("웨이퍼 맵 %s 의 WAFER(%s) 가 슬롯(%s)과 다르다 — 쓰지 않는다",
                     path, name, wafer)
        return None
    fnloc = head.get("FNLOC", "").strip()
    if fnloc not in _VERIFIED_FNLOC:
        _LOG.warning("웨이퍼 맵 %s 의 노치 위치 FNLOC=%s 는 방향을 확인한 적이 없다 — "
                     "쓰지 않는다(확인된 값: %s)", path, fnloc or "없음",
                     ", ".join(sorted(_VERIFIED_FNLOC)))
        return None
    cells = frozenset((ci, j) for j, r in enumerate(grid)
                      for ci, tok in enumerate(r) if not _EMPTY.match(tok))
    if not cells:
        return None
    return WaferTxt(path=path, rows=rows, cols=cols, cells=cells,
                    die_x=_die_um(head, "XDIES"), die_y=_die_um(head, "YDIES"),
                    fnloc=fnloc)


@lru_cache(maxsize=256)
def load(slot_folder: Path) -> Optional[WaferTxt]:
    """슬롯 폴더의 웨이퍼 맵 — **LOT 폴더(부모)의 ``<슬롯명>.txt``** 만 본다.

    다른 자리(예: ``0. ASSY\\…\\Mirroring_Converted``)에도 같은 이름 파일이 있지만
    대조로 확인한 것은 LOT 폴더의 파일뿐이다.  전 구간 fail-safe."""
    try:
        p = slot_folder.parent / f"{slot_folder.name}.txt"
        if not p.is_file() or p.stat().st_size > _MAX_BYTES:
            return None
        text = read_ini_text(p)
        return parse(text, p, slot_folder.name) if text else None
    except Exception:
        return None
