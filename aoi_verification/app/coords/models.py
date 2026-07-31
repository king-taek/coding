"""좌표 데이터 모델 + die 격자 변환 **폴백** 상수.

아래 상수들은 TB500 한 대의 실측값이다.  **평상시에는 쓰이지 않는다** —
:mod:`~.wafer_geometry` 가 결과 폴더의 ``Params_WaferInfo.ini`` / KLA ``.001`` 에서
읽은 값을 쓰고, 그게 없을 때만 여기로 떨어진다(그때 경고를 남긴다)."""

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


# ── Camtek INI 변환 폴백 상수 (TB500 실측) ────────────────────────────────
# col = INI_Col - CAMTEK_COL_OFFSET
# row = CAMTEK_ROW_TOTAL - INI_Row
# x   = X - INI_Col × CAMTEK_PITCH_X
# y   = Y - INI_Row × CAMTEK_PITCH_Y
# pitch 는 평상시 Params_WaferInfo.ini `[Geometry] DieStep_X/Y` 에서 읽는다.
CAMTEK_PITCH_X: float = 37247.7   # µm/die (TB500 폴백)
CAMTEK_PITCH_Y: float = 44905.4   # µm/die (TB500 폴백)
# ⚠ 아래 두 값은 die 격자 **맵 원점**이라 어떤 결과 파일에서도 읽을 수 없다.
# 결과 파일에는 '검사한 die' 만 기록돼 맵 가장자리의 미사용 행/열을 알 방법이 없다.
# ── 정답 기준: 장비(AOI Data Viewer) 웨이퍼 맵.  왼쪽 맨 아래가 (0,0), 오른쪽·위로 +1.
# 실측 판독 1건: INI `Col=8, Row=5` → 장비 화면 `col=6, row=2`.
#   col = Col − 2       → 8−2 = 6 ✓
#   row = 7 − Row       → 7−5 = 2 ✓   (`Row−3` 은 Row 1..6 에서 음수가 나와 불가)
CAMTEK_COL_OFFSET: int = 2
# ⚠ 이전 세션이 이 값을 7→6 으로 바꿨다가 전 구간 row 가 1 작아졌다.  그때 맞춘 대상은
# '현물 웨이퍼 맵' 이 아니라 KLA 변환값이었고(Camtek 만 움직여 KLA 에 맞춤), KLA 쪽
# 원점이 틀렸다는 걸 못 봤다.  근거는 위 장비 화면 직접 판독이다 — 되돌리지 말 것.
# (맵 세로 눈금은 0..6 의 7칸이고 이 자재는 그중 1..6 만 쓴다 → "col 7개 / row 6개".)
CAMTEK_ROW_TOTAL: int = 7

# ── KLA .001 변환 폴백 상수 (TB500 실측) ──────────────────────────────────
# col = XINDEX + KLA_ZERO_X
# row = YINDEX + KLA_ZERO_Y
# x   = round(XREL)
# y   = round(DiePitchY - YREL)
# ⚠ SampleTestPlan 최솟값(−min(XINDEX))으로 자동 산출할 수 있는 건 **X 뿐**이다.
# 그 목록에는 '검사한 die' 만 있어서, 맵 가장자리 행/열이 통째로 미사용이면 최솟값이
# 맵 원점과 어긋난다.  이 자재가 정확히 그 경우다:
#   X: 맵 0..6 을 전부 사용 → −min(XINDEX) = 3 이 맞다        → wafer_geometry 가 자동 산출
#   Y: 맵 0..6 중 1..6 만 사용 → −min(YINDEX) = 3 이지만 4 가 맞다 → 아래 상수를 쓴다
# 근거: 거리 121 µm 로 동일 결함임이 확인된 쌍 Camtek(Col=7,Row=3) ↔ KLA(XINDEX=2,YINDEX=0).
#   Camtek row = 7−3 = 4 이므로 KLA 도 0 + KLA_ZERO_Y = 4 여야 정렬이 맞는다.
KLA_ZERO_X: int = 3   # 맵 가장자리 열을 전부 쓰므로 SampleTestPlan 산출값과 같다
KLA_ZERO_Y: int = 4   # 맵 아래 1행이 미사용 → SampleTestPlan 산출값(3)보다 1 크다

# ── Surface.flt geometry 환산 상수 (보고서: 1 px = 0.77 µm) ────────────────
# area_um2  = area(px²)        × SURFACE_AREA_FACTOR (= 0.77²)
# width_um  = BlobBreadth(px)  × SURFACE_LEN_FACTOR
# length_um = BlobFeretMax(px) × SURFACE_LEN_FACTOR
SURFACE_LEN_FACTOR: float = 0.77      # px → µm (선형)
SURFACE_AREA_FACTOR: float = 0.5929   # px² → µm² (= 0.77²)
