"""좌표 데이터 모델 + TB500 장치별 변환 상수."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["DefectCoord", "DefectGeometry", "CAMTEK_PITCH_X", "CAMTEK_PITCH_Y",
           "CAMTEK_COL_OFFSET", "CAMTEK_ROW_TOTAL",
           "KLA_ZERO_X", "KLA_ZERO_Y",
           "SURFACE_AREA_FACTOR", "SURFACE_LEN_FACTOR"]


@dataclass(frozen=True)
class DefectCoord:
    """변환 완료된 defect 좌표 — 세 소스(Camtek INI / LIVE 파일명 / KLA .001) 공통 표현."""
    col: int       # 0-based 표준 die column (Camtek/KLA 통일)
    row: int       # 0-based 표준 die row (아래에서 위, Camtek/KLA·현물 웨이퍼 맵 정렬)
    x: float       # die 내부 local X (µm)
    y: float       # die 내부 local Y (µm)
    source: str    # "camtek_ini" | "camtek_live" | "kla"
    # KLA 원본 die-내부 좌표(XREL/YREL, µm) — source="kla" 일 때만 채운다.
    # 엑셀에 'Camtek 좌표계 변환값'과 'KLA 자체 좌표값'을 함께 표기하기 위함.
    native_x: Optional[float] = None
    native_y: Optional[float] = None


@dataclass(frozen=True)
class DefectGeometry:
    """Surface.flt 레코드에서 뽑은 결함 핵심 정보(예시 기준 6개 항목).

    area/width/length 는 µm 환산값, contrast 는 원값, zone/recipe 는 분류 코드."""
    area_um2: float     # area(px²) × pixel_um²
    width_um: float     # BlobBreadth(px) × pixel_um
    length_um: float    # BlobFeretMax(px) × pixel_um
    contrast: float     # Surface.flt Contrast (그대로)
    zone: int           # Surface.flt zone 코드 (예: 1=PI Opening, 63=Scan Area)
    recipe: int         # Surface.flt recipe 코드
    pixel_um: float     # 환산에 쓴 2D 스캔 픽셀 크기(µm/px). 결과 폴더에서 읽음(없으면 0.77)
    zone_name: str = ""    # recipe 파일의 ZoneName(자재별). 없으면 빈 문자열(코드만 표시)
    recipe_name: str = ""  # recipe 파일의 RecipeName(자재별). 없으면 빈 문자열(코드만 표시)


# ── TB500 Camtek INI 변환 상수 ────────────────────────────────────────────
# col = INI_Col - CAMTEK_COL_OFFSET
# row = CAMTEK_ROW_TOTAL - INI_Row
# x   = X - INI_Col × CAMTEK_PITCH_X
# y   = Y - INI_Row × CAMTEK_PITCH_Y
CAMTEK_PITCH_X: float = 37247.7   # µm/die (TB500)
CAMTEK_PITCH_Y: float = 44905.4   # µm/die (TB500)
CAMTEK_COL_OFFSET: int = 2        # TB500
# TB500: Y die 수 6, INI 원본 Row 는 1-based 1..6 (dev/좌표 확인 실측 분포).
# 1-based 위→아래를 0-based 아래→위(row 0..5)로 뒤집는 값 — 7 이면 전 구간 +1 로 어긋난다
# (현물 웨이퍼 맵·KLA 변환과 1 차이 나던 버그의 원인).
CAMTEK_ROW_TOTAL: int = 6         # TB500

# ── TB500 KLA .001 변환 상수 ─────────────────────────────────────────────
# col = XINDEX + KLA_ZERO_X
# row = YINDEX + KLA_ZERO_Y
# x   = round(XREL)
# y   = round(DiePitchY - YREL)
KLA_ZERO_X: int = 3   # TB500: package X count 7, 7 // 2 = 3
KLA_ZERO_Y: int = 3   # TB500: package Y count 6, 6 // 2 = 3

# ── Surface.flt geometry 환산 상수 (보고서: 1 px = 0.77 µm) ────────────────
# area_um2  = area(px²)        × SURFACE_AREA_FACTOR (= 0.77²)
# width_um  = BlobBreadth(px)  × SURFACE_LEN_FACTOR
# length_um = BlobFeretMax(px) × SURFACE_LEN_FACTOR
SURFACE_LEN_FACTOR: float = 0.77      # px → µm (선형)
SURFACE_AREA_FACTOR: float = 0.5929   # px² → µm² (= 0.77²)
