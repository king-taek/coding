"""이미지 → 결함 geometry(area/width/length/contrast) best-effort 리졸버.

이미지의 절대 좌표(:mod:`abs_coord`)를 Surface.flt(:mod:`surface_flt`)의
ActualX/ActualY 와 nearest-match 해 해당 레코드의 geometry 를 환산해 돌려준다.

모든 자재에 적용되지 않으므로(KLA·Surface.flt 없음·좌표/recipe 불일치) 단순 None 이
아니라 **사유(status)** 를 함께 돌려준다 — 엑셀에서 '미지원 자재' vs '데이터 없음' 을
명시적으로 구분해 표기하기 위함.

    status = "ok"       geometry 있음
    status = "disabled" Surface.flt 스키마 미충전 → 기능 비활성(마커도 표시 안 함)
    status = "no_flt"   폴더에 Surface.flt 자체가 없음 → 측정정보 미지원 자재
    status = "no_data"  Surface.flt 는 있으나 좌표 매칭 실패/데이터 없음 → 측정정보 없음

전 구간 fail-safe — 절대 raise 하지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import abs_coord, surface_flt
from .models import DefectGeometry, SURFACE_AREA_FACTOR, SURFACE_LEN_FACTOR
from .surface_flt import RawRecord

__all__ = ["resolve", "GeometryResult", "GEOMETRY_MATCH_TOL"]

# 보고서 기준: 좌표 거리 ≤ 5 µm 면 동일 defect 로 강하게 판단.
GEOMETRY_MATCH_TOL: float = 5.0


@dataclass(frozen=True)
class GeometryResult:
    status: str                          # "ok" | "disabled" | "no_flt" | "no_data"
    geometry: Optional[DefectGeometry]   # status == "ok" 일 때만 채워짐


def _nearest(records: tuple[RawRecord, ...],
             xy: tuple[float, float],
             tol: float) -> Optional[RawRecord]:
    """xy 에 가장 가까운 레코드를 반환(거리 ≤ tol).  없으면 None."""
    x, y = xy
    best: Optional[RawRecord] = None
    best_d = tol
    for rec in records:
        d = math.hypot(rec.actual_x - x, rec.actual_y - y)
        if d <= best_d:
            best_d = d
            best = rec
    return best


def resolve(image_path: Path) -> GeometryResult:
    """이미지 → GeometryResult.  실패 사유를 status 로 구분."""
    try:
        if not surface_flt._SCHEMA_READY:
            return GeometryResult("disabled", None)
        folder = Path(image_path).parent
        if not surface_flt.has_flt(folder):
            return GeometryResult("no_flt", None)
        records = surface_flt.load_folder(folder)
        xy = abs_coord.absolute_xy(Path(image_path))
        rec = _nearest(records, xy, GEOMETRY_MATCH_TOL) if (records and xy) else None
        if rec is None:
            return GeometryResult("no_data", None)
        geom = DefectGeometry(
            area_um2=rec.area * SURFACE_AREA_FACTOR,
            width_um=rec.blob_breadth * SURFACE_LEN_FACTOR,
            length_um=rec.blob_feret_max * SURFACE_LEN_FACTOR,
            contrast=rec.contrast,
            zone=int(rec.zone),
            recipe=int(rec.recipe),
        )
        return GeometryResult("ok", geom)
    except Exception:
        return GeometryResult("no_data", None)
