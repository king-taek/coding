"""좌표 데이터 모델 + TB500 장치별 변환 상수."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DefectCoord", "DefectGeometry", "CAMTEK_PITCH_X", "CAMTEK_PITCH_Y",
           "CAMTEK_COL_OFFSET", "CAMTEK_ROW_TOTAL",
           "KLA_ZERO_X", "KLA_ZERO_Y",
           "SURFACE_AREA_FACTOR", "SURFACE_LEN_FACTOR"]


@dataclass(frozen=True)
class DefectCoord:
    """변환 완료된 defect 좌표 — 세 소스(Camtek INI / LIVE 파일명 / KLA .001) 공통 표현."""
    col: int       # Camtek 표시용 die column
    row: int       # Camtek 표시용 die row
    x: float       # die 내부 local X (µm)
    y: float       # die 내부 local Y (µm)
    source: str    # "camtek_ini" | "camtek_live" | "kla"


@dataclass(frozen=True)
class DefectGeometry:
    """Surface.flt 레코드에서 환산한 결함 측정값(µm 단위)."""
    area_um2: float     # area(px²) × SURFACE_AREA_FACTOR
    width_um: float     # BlobBreadth(px) × SURFACE_LEN_FACTOR
    length_um: float    # BlobFeretMax(px) × SURFACE_LEN_FACTOR
    contrast: float     # Surface.flt Contrast (그대로)


# ── TB500 Camtek INI 변환 상수 ────────────────────────────────────────────
# col = INI_Col - CAMTEK_COL_OFFSET
# row = CAMTEK_ROW_TOTAL - INI_Row
# x   = X - INI_Col × CAMTEK_PITCH_X
# y   = Y - INI_Row × CAMTEK_PITCH_Y
CAMTEK_PITCH_X: float = 37247.7   # µm/die (TB500)
CAMTEK_PITCH_Y: float = 44905.4   # µm/die (TB500)
CAMTEK_COL_OFFSET: int = 2        # TB500
CAMTEK_ROW_TOTAL: int = 7         # TB500

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
