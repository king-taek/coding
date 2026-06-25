"""Camtek LIVE 파일명에서 좌표 추출.

파일명 형식: {prefix}_{...}_{col}_{row}_{DefectName}_{x.xx}_{y.yy}.jpg
예) R_TB500_LIVE_PI4_VLP-PDIS3_W6317098XYB5_4_5_Over Sized Bump_30229.803_1987.994.jpg
    또는 DefectName에 언더스코어가 포함된 형식도 허용.

파일명 끝에서부터: y(소수), x(소수), DefectName(임의), row(정수), col(정수) 순서.
x/y 는 소수점 필수 → col/row 정수와 명확히 구별, R_ 접두어 불필요.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .models import DefectCoord

__all__ = ["resolve"]

# 끝 부분이 _col_row_{DefectName(임의)}_x.xx_y.yy 형식.
# x/y: 소수점 필수 실수(µm 좌표), col/row: 정수, DefectName: 언더스코어 포함 가능.
_PAT = re.compile(
    r'_(\d+)_(\d+)_.+_([\d]+\.[\d]+)_([\d]+\.[\d]+)$'
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
