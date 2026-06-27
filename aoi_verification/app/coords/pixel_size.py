"""Surface.flt geometry 환산용 **2D 스캔 픽셀 크기**(µm/px)를 결과 폴더에서 읽는다.

0.77 은 하드코딩 상수가 아니라 **자재/웨이퍼별로 다른 2D 스캔 픽셀 크기**였다.
실측(LOT 파일 덤프):  PI4(00RMF041XYC7)=0.7708,  PI3(00RV9310XYE5)=0.8452.
따라서 width/length = blob × px, area = area_px × px² 로 환산해야 정확하다.
(예: 사용자 UI Area 32.67 은 0.5929 의 32.61 보다 0.770776² 의 32.675 에 일치.)

출처(우선순위, 모두 결과 폴더 안):
  1. Params_WaferInfo.ini : RefPixelSizeX    (웨이퍼별, 정밀 0.7707764)
  2. TrainData/Die.ini    : PixelSize_X      (가장 정밀 0.770776360179526)
  3. ProductInfo.ini      : Scan2DPixelSize  (반올림 0.7708)
  4. RecipesInfo.ini      : Scan2DPixelSize
못 찾으면 None → 호출부가 0.77 폴백.  전 구간 fail-safe(절대 raise 안 함).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

# (파일 상대경로, 키) 우선순위.
_SOURCES = (
    ("Params_WaferInfo.ini", "RefPixelSizeX"),
    ("TrainData/Die.ini", "PixelSize_X"),
    ("ProductInfo.ini", "Scan2DPixelSize"),
    ("RecipesInfo.ini", "Scan2DPixelSize"),
)
# 합리적 픽셀 크기 범위(µm/px) — 엉뚱한 값 채택 방지.
_MIN, _MAX = 0.05, 5.0


def _read_key(path: Path, key: str) -> Optional[float]:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"(?im)^\s*" + re.escape(key) + r"\s*=\s*([-\d.eE]+)", txt)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if _MIN <= v <= _MAX else None


@lru_cache(maxsize=256)
def scan_pixel_size(folder: Path) -> Optional[float]:
    """결과 폴더의 2D 스캔 픽셀 크기(µm/px).  못 찾으면 None.  fail-safe."""
    try:
        for rel, key in _SOURCES:
            v = _read_key(folder / rel, key)
            if v is not None:
                return v
    except Exception:
        return None
    return None
