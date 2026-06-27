"""이미지 → 절대 wafer 좌표(X, Y).  Surface.flt 매칭 전용.

변환된 :class:`DefectCoord`(die-local) 가 아니라 Surface.flt 의 ActualX/ActualY 와
직접 비교할 **원시 절대 좌표** 를 돌려준다.  best-effort, fail-safe(실패 시 None).

소스 우선순위:
    1. ColorImageGrabingInfo.ini 의 X/Y (또는 FaultX/FaultY) — 가장 정확.
    2. 점표기 JPEG 파일명 "X.Y.c.HASH.RECIPE.jpeg" 의 앞 두 토큰(절대 X.Y).

주의: LIVE 파일명(..._col_row_x_y)의 x/y 는 die-local 이라 절대 좌표가 아니므로
여기서는 쓰지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import camtek_ini

__all__ = ["absolute_xy"]

# "147206.243725.c.2104939970.2.jpeg" → 앞 두 토큰이 절대 X.Y.
_DOTTED_PAT = re.compile(r'^(\d+(?:\.\d+)?)\.(\d+(?:\.\d+)?)\.')


def absolute_xy(image_path: Path) -> Optional[tuple[float, float]]:
    """이미지 경로 → 절대 (X, Y) µm.  알 수 없으면 None."""
    try:
        # 1) INI 의 원시 X/Y.
        abs_map = camtek_ini.load_abs_folder(image_path.parent)
        xy = abs_map.get(image_path.stem.lower())
        if xy is not None:
            return xy
        # 2) 점표기 파일명에서 절대 X.Y.
        m = _DOTTED_PAT.match(image_path.name)
        if m:
            return (float(m.group(1)), float(m.group(2)))
    except Exception:
        return None
    return None
