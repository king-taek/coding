"""Height.flt(3D 범프 높이 검사 결과) → Defect 레코드.

멀티 레시피 스캔의 3D 레시피(예: T254 ``BS_3D``)가 찍은 사진은 Surface.flt 가 아니라 이 파일의
Defect 다.  사진 정보 파일(``ColorImageGrabingInfo.ini``)의 ``Type=2`` 가 그 표시다
(:func:`~.camtek_ini.load_type_folder`).

레코드 배치는 **늘 옆의 ``Height.flt.md`` 에서 읽는다** — 레코드 크기가 장비마다 160 · 208 로
달랐다(수집 자료 2026-11-14).  사이드카가 없으면 빈 결과다(틀린 수치보다 빈칸이 낫다,
:mod:`surface_flt` 와 같은 원칙).

좌표 칸이 Surface 와 다르다(관측 2026-11-23, AOI-16 T254 6324 웨이퍼 10곳, 장비 Export 대조):
사진의 X/Y 는 레코드의 ``x`` · ``y`` 와 5 µm 안에서 1,368/1,368 장 딱 한 줄씩 맞았고,
``ActualX`` · ``ActualY`` 와는 약 69 µm 어긋났다.  Export 의 Height 값 = ``height`` 칸(1,368/1,368).

전 구간 fail-safe — 절대 raise 하지 않는다.
"""

from __future__ import annotations

import logging
import struct
from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .surface_flt import _RE_ATTR, _RE_FIELD, _RE_SIZE, _VARTYPE_FMT

__all__ = ["HeightRecord", "load_folder", "has_flt", "nearest", "HEIGHT_PHOTO_TYPE"]

_LOG = logging.getLogger("aoi.coords")

# 사진 정보 파일의 Type 값 — EquipmentClassifications.ini [Height] 절의 ``-1 =2`` 와 같다(파일).
HEIGHT_PHOTO_TYPE = 2

_FLT_NAMES = ("Height.flt", "height.flt")
# 사이드카 필드 이름 → 레코드 칸.  recipe · Type · reclassify 는 없어도 된다.
_NAMES = {"x": "x", "y": "y", "zone": "zone", "height": "height",
          "recipe": "recipe", "Type": "type", "reclassify": "reclassify"}
_REQUIRED = ("x", "y", "zone", "height")

HeightRecord = namedtuple("HeightRecord", ["x", "y", "zone", "height", "recipe", "type", "reclassify"])


def _find(folder: Path) -> Optional[Path]:
    for name in _FLT_NAMES:
        p = folder / name
        if p.exists():
            return p
    return None


def has_flt(folder: Path) -> bool:
    try:
        return _find(folder) is not None
    except Exception:
        return False


def _layout(flt: Path) -> Optional[tuple[int, dict]]:
    md = flt.with_name(flt.name + ".md")
    if not md.exists():
        return None
    text = md.read_bytes().decode("utf-8", errors="replace")
    m = _RE_SIZE.search(text)
    if not m or int(m.group(1)) <= 0:
        return None
    size = int(m.group(1))
    fields: dict[str, tuple[int, str]] = {}
    for fm in _RE_FIELD.finditer(text):
        a = dict(_RE_ATTR.findall(fm.group(1)))
        key = _NAMES.get(a.get("Name", ""))
        if key is None:
            continue
        fmt = _VARTYPE_FMT.get(int(a["Vartype"])) if a.get("Vartype", "").isdigit() else None
        if fmt is None or not a.get("Offset", "").isdigit():
            continue
        off = int(a["Offset"])
        if off + struct.calcsize("<" + fmt) > size:
            return None
        fields[key] = (off, fmt)
    if any(k not in fields for k in _REQUIRED):
        return None
    return size, fields


@lru_cache(maxsize=256)
def load_folder(folder: Path) -> tuple[HeightRecord, ...]:
    """폴더의 Height.flt → 레코드 튜플.  없거나 사이드카가 없거나 손상이면 빈 튜플."""
    try:
        flt = _find(folder)
        if flt is None:
            return ()
        lay = _layout(flt)
        if lay is None:
            _LOG.warning("Height.flt.md 가 없거나 읽지 못해 Height 정보 없음으로 처리: %s", flt)
            return ()
        size, fields = lay
        data = flt.read_bytes()
        out = []
        for base in range(0, len(data) - size + 1, size):
            vals = dict.fromkeys(HeightRecord._fields)
            for k, (o, f) in fields.items():
                vals[k] = struct.unpack_from("<" + f, data, base + o)[0]
            out.append(HeightRecord(**vals))
        return tuple(out)
    except Exception:
        _LOG.warning("Height.flt 파싱 실패 — Height 정보 없음으로 처리: %s", folder, exc_info=True)
        return ()


def nearest(records, xy: tuple[float, float], tol: float) -> Optional[HeightRecord]:
    """사진 X/Y 에 가장 가까운 레코드(``x`` · ``y`` 거리 ≤ tol).  없으면 None."""
    best, best_d = None, tol
    for r in records:
        d = ((r.x - xy[0]) ** 2 + (r.y - xy[1]) ** 2) ** 0.5
        if d <= best_d:
            best, best_d = r, d
    return best
