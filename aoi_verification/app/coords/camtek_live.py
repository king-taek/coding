"""Camtek LIVE 파일명에서 좌표 추출.

실제 파일명 형식: R_{장치}_{WaferID}_{col}_{row}_{x.xx}_{y.yy}[_{DefectName}]
예) R_TB500_LIVE_PI4_PXU-PIDS3_00RMF043XYE0_5_3_21620.2113348411_7230.80771621759_Foreign Material

col/row 는 정수, x/y 는 소수점 포함 실수(µm).
DefectName 은 x/y 뒤에 오며 언더스코어·스페이스 모두 허용, 없어도 됨.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .models import DefectCoord

__all__ = ["resolve"]

# 형식: ..._col_row_x.xx_y.yy[_DefectName]
# col/row: 정수, x/y: 소수점 필수 실수(µm) → 정수 col/row와 명확히 구별.
# DefectName: x/y 뒤에 오며 선택적(없을 수도 있음), 언더스코어 포함 가능.
_PAT = re.compile(
    r'_(\d+)_(\d+)_([\d]+\.[\d]+)_([\d]+\.[\d]+)(?:_.+)?$'
)


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """LIVE 형식 파일명에서 DefectCoord 추출. 형식이 맞지 않으면 None."""
    stem = image_path.stem
    m = _PAT.search(stem)
    if not m:
        return None
    try:
        col = int(m.group(1))
        row = int(m.group(2))
        x = float(m.group(3))
        y = float(m.group(4))
    except ValueError:
        return None
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_live")
