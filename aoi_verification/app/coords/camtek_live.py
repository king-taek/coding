"""Camtek LIVE 파일명에서 좌표 추출.

두 가지 파일명 형식을 지원한다:
  A) ..._col_row_x_y[_DefectName]  — x/y 뒤에 DefectName (선택)
     예) R_TB500_LIVE_PI4_PXU-PIDS3_00RMF043XYE0_5_3_21620.211_7230.807_Foreign Material
  B) ..._col_row_DefectName_x_y    — x/y 앞에 DefectName
     예) R_TB500_LIVE_PI4_VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump_30229.803_1987.994

col/row 는 정수, x/y 는 정수 또는 소수점 실수(µm). 형식 A 를 먼저 시도한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .models import DefectCoord

__all__ = ["resolve"]

# 형식 A: ..._col_row_x[.xx]_y[.yy][_DefectName]
_PAT_A = re.compile(
    r'_(\d+)_(\d+)_([\d]+(?:\.[\d]+)?)_([\d]+(?:\.[\d]+)?)(?:_.+)?$'
)

# 형식 B: ..._col_row_DefectName_x[.xx]_y[.yy]  (DefectName 이 x/y 앞)
_PAT_B = re.compile(
    r'_(\d+)_(\d+)_.+_([\d]+(?:\.[\d]+)?)_([\d]+(?:\.[\d]+)?)$'
)


def _extract(m) -> Optional[DefectCoord]:
    try:
        col = int(m.group(1))
        row = int(m.group(2))
        x = float(m.group(3))
        y = float(m.group(4))
    except (ValueError, IndexError):
        return None
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_live")


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """LIVE 형식 파일명에서 DefectCoord 추출. 형식이 맞지 않으면 None."""
    stem = image_path.stem
    for pat in (_PAT_A, _PAT_B):
        m = pat.search(stem)
        if m and '_' in stem[:m.start()]:
            return _extract(m)
    return None
