"""Camtek LIVE 파일명에서 좌표 추출.

파일명 형식: R_{장치/레이어}_{WaferID}_{col}_{row}_{DefectName}_{x}_{y}.jpg
예) R_TB500_LIVE_PI4_VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump_30229.803_1987.994.jpg

파일명 끝에서부터: y, x, DefectName, row, col 순서.
DefectName 에 '_' 가 없다고 가정(스페이스는 허용).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .models import DefectCoord

__all__ = ["resolve"]

# R_ 로 시작하고, 끝 부분이 _col_row_defectname_x_y
# col/row: 정수, x/y: 소수점 포함 실수(정수도 허용)
_PAT = re.compile(
    r'_(\d+)_(\d+)_[^_]+_([\d]+(?:\.[\d]+)?)_([\d]+(?:\.[\d]+)?)$'
)


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """LIVE 형식 파일명에서 DefectCoord 추출. 형식이 맞지 않으면 None."""
    stem = image_path.stem
    if not stem.startswith('R_'):
        return None
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
