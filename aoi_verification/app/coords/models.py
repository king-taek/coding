"""좌표 데이터 모델 + die 격자 변환 **폴백** 상수.

아래 상수들은 TB500 한 대의 실측값이다.  **평상시에는 쓰이지 않는다** —
:mod:`~.wafer_geometry` 가 결과 폴더의 ``Params_WaferInfo.ini`` / KLA ``.001`` 에서
읽은 값을 쓰고, 그게 없을 때만 여기로 떨어진다(그때 경고를 남긴다)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

__all__ = ["DefectCoord", "DefectGeometry", "CAMTEK_PITCH_X", "CAMTEK_PITCH_Y",
           "CAMTEK_COL_OFFSET", "DEFAULT_WAFER_DIAMETER",
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


# ── Camtek INI 변환 폴백 상수 ────────────────────────────────────────────
# 변환식 (장비 화면 정답 4-device 실측으로 확정 — docs/디바이스_하드코딩_조사.md):
#   col = x_index - col_origin      col_origin = ceil((Center_X − D/2) / pitch_x)
#   row = row_total - y_index       row_total  = floor((Center_Y + D/2) / pitch_y) − 1
#   x   = floor(X - x_index × pitch_x)            ← 장비 표기는 반올림이 아니라 버림
#   y   = floor(Y - y_index × pitch_y)
# col·row 기준은 상수가 아니라 **웨이퍼의 stage 위치**에서 나온 유도값이다.  아래
# CAMTEK_COL_OFFSET / DEFAULT_WAFER_DIAMETER 는 Center_* 가 미기록일 때의 폴백 재료다.
# pitch 는 평상시 Params_WaferInfo.ini `[Geometry] DieStep_X/Y` 에서 읽는다.
CAMTEK_PITCH_X: float = 37247.7   # µm/die (TB500 폴백)
CAMTEK_PITCH_Y: float = 44905.4   # µm/die (TB500 폴백)
# col 오프셋 2 는 pitch 가 전혀 다른 3개 device(25022.9/37247.7/27474.5)에서 모두 성립 —
# 자재별 값이 아니라 고정 시스템 오프셋이라는 실측 증거가 있다(물리적 의미는 미확정).
# 장비 판독: INI `Col=8, Row=5` → 화면 `col=6, row=2` (맵 왼쪽 맨 아래가 (0,0)).
CAMTEK_COL_OFFSET: int = 2
# 웨이퍼 직경(µm) — die 격자 원점 계산용.  평상시에는 Params_WaferInfo.ini
# `[Geometric] Diameter` 에서 읽고, 없을 때만 이 값을 쓴다
# (실측 확인: 현재 모든 device 가 300 mm).
DEFAULT_WAFER_DIAMETER: float = 300000.0
# ⚠ row 기준을 상수(7)로 박았다가 pitch_y 가 다른 device(31831.4)에서 row=−2 가 나온
# 적이 있다.  지금은 `Center_Y` 에서 유도하고, 미기록일 때만 ceil(Diameter/pitch_y) 로
# 폴백한다(장비 화면 정답 4사례가 그 값).  **상수로 되돌리지 말 것** — 근거는
# docs/디바이스_하드코딩_조사.md §6-A(옛 식)·§6-J(유도식).

# ── KLA .001 변환 폴백 상수 (TB500 실측) ──────────────────────────────────
# col = XINDEX + KLA_ZERO_X
# row = YINDEX + KLA_ZERO_Y
# x   = round(XREL)
# y   = round(DiePitchY - YREL)
# ⚠ 아래 둘은 이제 **폴백 전용**이다.  평상시에는 `.001` 헤더의 SampleCenterLocation
# 으로 유도한다(wafer_geometry._kla_zeros_from_center) — Camtek col_origin/row_total 과
# 같은 '웨이퍼에 온전히 들어오는 첫 die' 규칙이라 두 장비가 자동으로 정렬된다.
# ⚠ **KLA_ZERO_Y 는 device 마다 다르다** — 상수로 되돌리지 말 것.  사용자가 장비로 확인한
# 7건(2개 device)에서 T254 는 4, TB500 은 **3** 이었다(조사 §6-K).  상수 4 를 쓰던 동안
# TB500 자재의 KLA row 가 전 구간 1 컸다.
KLA_ZERO_X: int = 3   # 폴백 — SampleTestPlan 의 −min(XINDEX) 도 못 구할 때
KLA_ZERO_Y: int = 4   # 폴백 — SampleCenterLocation 이 없을 때(TB500 실측 상수)

# ── Surface.flt geometry 환산 상수 (보고서: 1 px = 0.77 µm) ────────────────
# area_um2  = area(px²)        × SURFACE_AREA_FACTOR (= 0.77²)
# width_um  = BlobBreadth(px)  × SURFACE_LEN_FACTOR
# length_um = BlobFeretMax(px) × SURFACE_LEN_FACTOR
SURFACE_LEN_FACTOR: float = 0.77      # px → µm (선형)
SURFACE_AREA_FACTOR: float = 0.5929   # px² → µm² (= 0.77²)
