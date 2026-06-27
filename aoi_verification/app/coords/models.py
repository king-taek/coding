"""좌표 데이터 모델 + TB500 장치별 변환 상수."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DefectCoord", "CAMTEK_PITCH_X", "CAMTEK_PITCH_Y",
           "CAMTEK_COL_OFFSET", "CAMTEK_ROW_TOTAL",
           "KLA_ZERO_X", "KLA_ZERO_Y"]


@dataclass(frozen=True)
class DefectCoord:
    """변환 완료된 defect 좌표 — 세 소스(Camtek INI / LIVE 파일명 / KLA .001) 공통 표현."""
    col: int       # Camtek 표시용 die column
    row: int       # Camtek 표시용 die row
    x: float       # die 내부 local X (µm)
    y: float       # die 내부 local Y (µm)
    source: str    # "camtek_ini" | "camtek_live" | "kla"


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
