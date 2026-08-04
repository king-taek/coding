"""die 격자 기하(pitch·인덱스 원점)를 **스캔 결과 폴더에서 읽는다**.

``models.py`` 의 ``CAMTEK_PITCH_X`` 같은 상수는 TB500 한 대의 값이었다.  die 크기가
다른 디바이스에서는 ``x = X − Col × 37247.7`` 이 통째로 틀린 값을 내 좌표·매칭·엑셀이
전부 어긋난다.  필요한 값은 이미 결과 폴더 안에 있다 — 코드가 안 읽고 있었을 뿐이다.

출처:
  · Camtek : ``Params_WaferInfo.ini``  ``[Geometry] DieStep_X/DieStep_Y``
             (``DieSize_*`` 는 폴백 — 실측상 DieStep 과 미세히 다르다)
  · KLA    : ``.001`` 헤더 ``DiePitch X Y`` 와 ``SampleTestPlan`` 의 die 인덱스 목록

실측 불변식(``dev/좌표 확인`` 63건 전부 일치) — **읽은 pitch 의 검산에 쓴다**::

    Col == floor(X / DieStep_X)      Row == floor(Y / DieStep_Y)

즉 Camtek 의 die 격자는 stage 원점(0,0)에 고정돼 있다.  읽은 pitch 로 이 식이 안 맞으면
엉뚱한 키를 읽은 것이므로 채택하지 않는다.

⚠ 다만 ``Row`` 쪽 등호는 **레시피마다 원점이 1 다를 수 있다**(실측 15호기).  그래서
검산은 X 만 등호를 요구하고 Y 는 '레시피별로 차이가 상수' 만 본다 — :func:`_grid_check`
와 ``docs/디바이스_하드코딩_조사.md`` §6-F 참조.  좌표 변환 자체는 이 필드를 안 쓰고
``floor(좌표/pitch)`` 로 유도한다(:mod:`.camtek_ini`).

``pixel_size.py`` 와 같은 관습: 폴더 단위 ``@lru_cache``, 전 구간 fail-safe(절대 raise 안 함),
합리적 범위 클램프.  못 읽으면 ``models.py`` 의 TB500 폴백을 쓰되 **경고를 남긴다**
(조용히 옛 경로로 떨어지면 아무도 모른다).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .ini_text import read_ini_text
from .models import (CAMTEK_COL_OFFSET, CAMTEK_PITCH_X, CAMTEK_PITCH_Y,
                     DEFAULT_WAFER_DIAMETER, KLA_ZERO_X, KLA_ZERO_Y)

__all__ = ["CamtekGeometry", "KlaGeometry", "camtek_geometry", "kla_geometry",
           "has_camtek_entries", "FALLBACK_CAMTEK", "FALLBACK_KLA"]

_LOG = logging.getLogger("aoi.coords")

# 합리적 die pitch 범위(µm) — 엉뚱한 키를 읽었을 때 채택 방지.
_MIN_PITCH, _MAX_PITCH = 100.0, 500000.0
# 합리적 웨이퍼 직경 범위(µm) — 2인치(50 mm) ~ 450 mm.
_MIN_DIAMETER, _MAX_DIAMETER = 50000.0, 450000.0
# die 인덱스 기준의 상식 범위 — 웨이퍼가 stage 원점에서 이보다 멀리 놓일 수 없다.
_MAX_DIE_INDEX = 200

# Diameter 를 담은 파일 후보 — pitch 후보와 같은 폴더 탐색 순서로 찾는다.
_DIAMETER_SOURCES = ("Params_WaferInfo.ini", "ProductInfo.ini")

# Camtek die pitch 후보 (상대경로, X키, Y키) 우선순위.
# ⚠ **간격(step) 계열만** 넣는다.  die '크기' 는 pitch 가 아니다 — 스크라이브 street 이
# 있는 자재는 둘이 다르다(실측 PGEE48: XDieSize 4147.352 vs XDieIndex 4160.900, 13.5 µm 차).
# 크기를 pitch 로 쓰면 col 70 에서 die 폭의 23% 가 어긋난다.  그래서 `DieSize_*`
# `DieSelectedSize_*` `XDieSize/YDieSize` 는 후보에서 제외한다.
_CAMTEK_SOURCES = (
    ("Params_WaferInfo.ini", "DieStep_X", "DieStep_Y"),
    ("ProductInfo.ini", "XDieIndex", "YDieIndex"),
    ("ProductInfo.ini", "CustomerDiePitch_X", "CustomerDiePitch_Y"),
)
# 사진 폴더가 웨이퍼 폴더의 하위일 수 있어 부모를 몇 단계까지 올라가 찾는다.
_PARENT_LEVELS = 2

_DIEPITCH_PAT = re.compile(r'DiePitch\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)', re.IGNORECASE)
# SampleTestPlan <개수> 뒤에 (XINDEX YINDEX) 쌍이 ';' 까지 이어진다.
_TESTPLAN_PAT = re.compile(r'SampleTestPlan\s+\d+(.*?);', re.IGNORECASE | re.DOTALL)
_INDEX_PAIR_PAT = re.compile(r'(-?\d+)\s+(-?\d+)')
# 웨이퍼 중심의 die 격자 좌표(µm) — Camtek `[Geometric] Center_X/Y` 에 대응한다.
_SAMPLE_CENTER_PAT = re.compile(
    r'SampleCenterLocation\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)', re.IGNORECASE)
# `SampleSize 1 300;` → 웨이퍼 직경 300 **mm**.
_SAMPLE_SIZE_PAT = re.compile(r'SampleSize\s+\d+\s+([\d.eE+\-]+)', re.IGNORECASE)


@dataclass(frozen=True)
class CamtekGeometry:
    """Camtek INI 좌표 변환에 필요한 die 격자 기하.

    ``col = x_index − col_origin``,  ``row = row_total − y_index``,
    ``x = floor(X − x_index × pitch_x)``,  ``y = floor(Y − y_index × pitch_y)``

    ``col_origin``·``row_total`` 은 상수가 아니라 **웨이퍼가 stage 위 어디에 놓였는지**
    (``Center_X``/``Center_Y``)에서 유도한다 — 각각 '온전히 들어오는 첫 열' 과
    '온전히 들어오는 마지막 행' 이다(:func:`_col_origin`·:func:`_row_total`).
    """
    pitch_x: float
    pitch_y: float
    col_origin: int
    row_total: int
    source: str      # 진단용 — 어느 파일에서 왔는지("fallback" 이면 TB500 상수)


@dataclass(frozen=True)
class KlaGeometry:
    """KLA .001 좌표 변환에 필요한 die 격자 기하.

    ``col = XINDEX + zero_x``,  ``row = YINDEX + zero_y``
    """
    pitch_x: float
    pitch_y: float
    zero_x: int
    zero_y: int
    source: str


def _row_total(center_y: Optional[float], diameter: float, pitch_y: float) -> int:
    """row 번호 기준 = **웨이퍼 안에 온전히 들어오는 마지막 die 행**.

    ``row = row_total − y_index`` 가 0 부터 시작하게 하는 값이다(Camtek 은 Y 가 아래로
    커져서 *마지막* 행이 화면 맨 아래 = row 0 이다 — :func:`_col_origin` 이 *첫* 열을
    쓰는 것과 축 방향만 다르고 같은 규칙이다)::

        row_total = floor((Center_Y + Diameter/2) / DieStep_Y) − 1

    실측 근거(``docs/디바이스_하드코딩_조사.md`` §6-J): RDL4 로트에서 **같은 장비가 같은
    사진을 두 형태로** 남겼는데(점표기 파일 + col/row 가 박힌 파일명), 그 파일명이 이
    유도값과 두 축 모두 오차 0 으로 맞는다.  옛 식 ``ceil(Diameter/pitch_y)`` 는 같은
    자재에서 7 을 내 row 가 전 구간 1 컸다.

    ``center_y`` 가 없거나 기록되지 않았으면(``0``) 옛 식으로 폴백한다 — 장비 화면 정답
    4사례(``TestGoldenEquipmentExamples``)가 그 값이고 ``Center_Y`` 를 갖고 있지 않다.
    ⚠ **두 식은 보통 1 다르다.** 폴백은 '아는 게 없을 때의 기존 동작 보존' 이지 같은 값이
    아니다 — 그래서 KLA 쪽 ``zero_y`` 도 같은 조건에서 함께 폴백해야 정렬이 유지된다.
    """
    if not center_y:                       # None 또는 0.0 → 미기록
        return math.ceil(diameter / pitch_y)
    total = math.floor((center_y + diameter / 2.0) / pitch_y) - 1
    if not (0 <= total <= _MAX_DIE_INDEX):
        _LOG.warning("Center_Y %.1f 로 계산한 row 기준 %d 이 비상식적이라 "
                     "ceil(Diameter/pitch_y) 로 폴백합니다.", center_y, total)
        return math.ceil(diameter / pitch_y)
    return total


def _col_origin(center_x: Optional[float], diameter: float, pitch_x: float) -> int:
    """col 번호 기준 = **웨이퍼 안에 온전히 들어오는 첫 die 열**.

    ``col = x_index − col_origin`` 이 0 부터 시작하게 하는 값이다::

        col_origin = ceil((Center_X − Diameter/2) / DieStep_X)

    ``Center_X`` 는 웨이퍼가 stage 위 어디에 놓였는지다.  같은 자재라도 위치가 한 die
    달라지면 ``x_index`` 가 통째로 1 밀리므로, **상수로 둘 수 없다**(실측: RDL4 는 1,
    15호기는 2).  상수 2 를 쓰면 RDL4 에서 ``col = −1`` 이 나온다 — 0-based 웨이퍼 맵에
    없는 값이다.  근거는 사진으로 확인한 LIVE↔Camtek 8쌍
    (``docs/디바이스_하드코딩_조사.md`` §6-H).

    ``center_x`` 가 없거나 기록되지 않았으면(``0``) :data:`CAMTEK_COL_OFFSET` 로 폴백한다 —
    ``Center_X=0.000000`` 인 실물이 있고(같은 파일에 ``MeasuredDiameter=0`` 이 함께 온다)
    그건 값이 0 이라는 뜻이 아니라 **정렬 정보 미기록**이다.
    """
    if not center_x:                       # None 또는 0.0 → 미기록
        return CAMTEK_COL_OFFSET
    origin = math.ceil((center_x - diameter / 2.0) / pitch_x)
    if not (0 <= origin <= _MAX_DIE_INDEX):
        _LOG.warning("Center_X %.1f 로 계산한 col 기준 %d 이 비상식적이라 상수 %d 을 씁니다.",
                     center_x, origin, CAMTEK_COL_OFFSET)
        return CAMTEK_COL_OFFSET
    return origin


# 데이터에서 못 읽을 때 쓰는 TB500 폴백 — 값은 models.py 가 보유한다.
FALLBACK_CAMTEK = CamtekGeometry(
    pitch_x=CAMTEK_PITCH_X, pitch_y=CAMTEK_PITCH_Y,
    col_origin=CAMTEK_COL_OFFSET,
    row_total=_row_total(None, DEFAULT_WAFER_DIAMETER, CAMTEK_PITCH_Y),   # = 7
    source="fallback",
)
FALLBACK_KLA = KlaGeometry(
    pitch_x=CAMTEK_PITCH_X, pitch_y=CAMTEK_PITCH_Y,
    zero_x=KLA_ZERO_X, zero_y=KLA_ZERO_Y, source="fallback",
)


def _read_key(path: Path, key: str, lo: float = _MIN_PITCH,
              hi: float = _MAX_PITCH) -> Optional[float]:
    """INI 에서 ``key=값`` 을 읽어 float 로. 없거나 [lo, hi] 밖이면 None."""
    txt = read_ini_text(path)
    if txt is None:
        return None
    # 값 뒤에 다른 문자가 붙으면(예: 소수점이 ',' 인 로캘) **잘라 읽지 않고 거부**한다.
    # `37247,700000` 을 37247.0 으로 조용히 받아들이면 검산을 통과해 버릴 수 있다.
    m = re.search(r"(?im)^\s*" + re.escape(key) + r"\s*=\s*([-\d.eE]+)\s*$", txt)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if lo <= v <= hi else None


def _search_dirs(folder: Path) -> list[Path]:
    """폴더 자신 + 부모 ``_PARENT_LEVELS`` 단계."""
    dirs = [folder]
    cur = folder
    for _ in range(_PARENT_LEVELS):
        parent = cur.parent
        if parent == cur:
            break
        dirs.append(parent)
        cur = parent
    return dirs


def _read_diameter(folder: Path) -> float:
    """웨이퍼 직경(µm) — ``[Geometric] Diameter``.  못 찾으면 기본 300 mm.

    row 번호 기준 ``ceil(Diameter / pitch_y)`` 계산에 쓴다.  실측 확인상 현재 모든
    device 가 300 mm 라 기본값이 안전하지만, 파일 값이 있으면 그쪽을 쓴다."""
    for base in _search_dirs(folder):
        for rel in _DIAMETER_SOURCES:
            v = _read_key(base / rel, "Diameter", _MIN_DIAMETER, _MAX_DIAMETER)
            if v is not None:
                return v
    return DEFAULT_WAFER_DIAMETER


def _read_center(folder: Path, key: str) -> Optional[float]:
    """웨이퍼 중심의 stage 좌표(µm) — ``[Geometric] Center_X``/``Center_Y``.  미기록이면 None.

    ``0.000000`` 은 **값이 아니라 '기록 안 됨'** 이다(같은 파일에 ``MeasuredDiameter=0``,
    ``FlatNotchVal=-1`` 이 함께 온다).  :func:`_read_key` 의 하한이 그걸 걸러 준다."""
    for base in _search_dirs(folder):
        for rel in _DIAMETER_SOURCES:
            v = _read_key(base / rel, key, _MIN_PITCH, _MAX_DIAMETER)
            if v is not None:
                return v
    return None


def _pitch_candidates(folder: Path):
    """(pitch_x, pitch_y, 출처) 후보를 우선순위대로 내놓는다.

    마지막 후보는 :mod:`.models` 의 상수다 — **다른 후보와 똑같이 검산을 받는다**.
    폴더 자신의 Col/Row 가 확인해 주면 그건 추정이 아니라 검증된 값이고, 다른 자재에서는
    검산이 거부하므로 옛 상수가 조용히 쓰이는 경로가 없다."""
    for base in _search_dirs(folder):
        for rel, kx, ky in _CAMTEK_SOURCES:
            px = _read_key(base / rel, kx)
            py = _read_key(base / rel, ky)
            if px is not None and py is not None:
                yield (px, py, f"{rel}:{kx}/{ky}")
    yield (CAMTEK_PITCH_X, CAMTEK_PITCH_Y, "models.py 상수")


def has_camtek_entries(folder: Path) -> bool:
    """이 폴더가 **Camtek INI 좌표를 가진 폴더**인가(변환 대상이 있는가).

    KLA 슬롯이나 LIVE 파일명 슬롯은 여기서 False — die pitch 를 못 찾아도 문제가 아니다
    (그쪽은 각자 자기 경로로 좌표를 만든다)."""
    from . import camtek_ini

    try:
        return bool(camtek_ini.load_raw_folder(folder))
    except Exception:
        return False


def _grid_check(folder: Path, pitch_x: float, pitch_y: float) -> tuple[bool, bool]:
    """INI 의 ``Col``/``Row`` 필드를 참조값 삼아 pitch 를 검산한다.

    **두 축을 다르게 본다** — 실측 근거는 :mod:`docs.디바이스_하드코딩_조사` §6-F::

        X : Col == floor(X/pitch_x)                       전 항목 **등호**
        Y : {floor(Y/pitch_y) − Row} 가 **레시피별로 상수**  (0 일 필요 없음)

    Y 를 완화하는 이유는 레시피마다 ``Row`` 필드의 원점이 1 다를 수 있어서다(같은 결함을
    두 레시피가 찍으면 ``Col`` 은 같은데 ``Row`` 만 1 어긋난다).  **X 는 절대 완화하지
    않는다** — 항목이 1건뿐인 폴더에서 '상수' 조건은 공허하게 참이 돼, 격자가 다른 자재에
    TB500 상수가 채택되는 길이 열린다(``x = −1,652,266 µm`` 참사의 재현).  실제로 기존
    거부 경로는 전부 X 축에서 걸린다.

    반환 ``(통과, 의미있음)``.  '의미있음' 은 검산이 실제로 pitch 를 제약했는지다 —
    모든 항목이 ``Col=0`` 이면 어떤 pitch 든 통과하므로 검산이 아무 말도 못 한 것이다.
    파일에서 읽은 값은 '통과' 만으로 채택하지만, 상수 후보는 '의미있음' 까지 요구한다.
    """
    from . import camtek_ini      # 순환 import 회피 — 검산 시점에만 필요

    try:
        raw = camtek_ini.load_raw_folder(folder)
        recipes = camtek_ini.load_recipe_folder(folder)
    except Exception:
        return (False, False)
    if not raw:
        return (False, False)

    # X: 등호 그대로.
    if not all(math.floor(X / pitch_x) == col_i for X, _, col_i, _ in raw.values()):
        return (False, False)

    # Y: 레시피별로 차이가 일정하기만 하면 된다.
    by_recipe: dict[int, set[int]] = {}
    for stem, (_X, Y, _col_i, row_i) in raw.items():
        by_recipe.setdefault(recipes.get(stem, 0), set()).add(
            math.floor(Y / pitch_y) - row_i)
    if any(len(diffs) != 1 for diffs in by_recipe.values()):
        return (False, False)

    # 조용한 폴백 금지 — INI 필드가 die 인덱스와 어긋난 채 통과했으면 남긴다.
    off = {r: next(iter(d)) for r, d in by_recipe.items() if next(iter(d)) != 0}
    if off:
        _LOG.info("INI Row 가 die 인덱스와 어긋난다(레시피별 상수라 채택). "
                  "좌표 산출은 좌표에서 유도하므로 영향 없음 — recipe별 차이 %s: %s",
                  off, folder)

    meaningful = (any(col_i >= 1 for _, _, col_i, _ in raw.values())
                  and any(row_i >= 1 for *_, row_i in raw.values()))
    return (True, meaningful)


@lru_cache(maxsize=256)
def camtek_geometry(folder: Path) -> Optional[CamtekGeometry]:
    """폴더의 Camtek die 격자 기하.

    후보를 우선순위대로 돌며 **검산을 통과한 첫 값**을 채택한다.  통과한 후보가 하나도
    없으면 ``None`` — 호출부는 좌표를 만들지 않는다.  **틀린 좌표를 내느니 안 내는 편이
    낫다**(die 가 4 mm 인 자재에 37 mm pitch 를 쓰면 x 가 −1,652,266 µm 같은 값이 된다).
    전 구간 fail-safe.
    """
    try:
        for px, py, src in _pitch_candidates(folder):
            ok, meaningful = _grid_check(folder, px, py)
            if not ok:
                continue
            if src == "models.py 상수" and not meaningful:
                continue      # 검산이 pitch 를 제약하지 못했다 — 상수를 추정으로 쓰지 않는다
            # col·row 기준 **모두** 웨이퍼의 stage 위치(Center_X/Center_Y)에서 유도한다 —
            # 같은 규칙의 두 축이다(§6-H·§6-J).  못 읽으면 각각 상수/옛 식으로 폴백한다.
            dia = _read_diameter(folder)
            return CamtekGeometry(pitch_x=px, pitch_y=py,
                                  col_origin=_col_origin(
                                      _read_center(folder, "Center_X"), dia, px),
                                  row_total=_row_total(
                                      _read_center(folder, "Center_Y"), dia, py),
                                  source=src)
        # ★ Camtek INI 자체가 없는 폴더(KLA 슬롯·LIVE 파일명 슬롯)는 **조용히** None.
        #   경고는 '변환할 항목이 있는데 pitch 를 못 정한' 진짜 문제일 때만 낸다 —
        #   안 그러면 KLA 폴더마다 무의미한 경고가 쌓인다.
        if has_camtek_entries(folder):
            _LOG.warning(
                "die pitch 를 확정하지 못해 **절대 wafer 좌표**로 매칭합니다(매칭은 정상). "
                "die 내부 좌표·row 표기를 보려면 Params_WaferInfo.ini `DieStep_X/Y` 또는 "
                "ProductInfo.ini `XDieIndex/YDieIndex` 가 필요합니다: %s", folder)
        return None
    except Exception:
        return None


def _kla_zero(center: float, diameter: float, pitch: float) -> Optional[int]:
    """die 인덱스 원점 = **−(웨이퍼에 온전히 들어오는 첫 인덱스)**.  비상식적이면 None.

    ``col = XINDEX + zero_x`` 가 0 부터 시작하게 하는 값이다.  Camtek
    :func:`_col_origin` 과 **같은 규칙**이고, KLA 는 Y 가 위로 커져서 두 축 모두 '첫' 을
    쓴다(Camtek 은 Y 가 아래로 커져 row 만 '마지막' 을 쓴다 — :func:`_row_total`)."""
    zero = -math.ceil((center - diameter / 2.0) / pitch)
    return zero if 0 <= zero <= _MAX_DIE_INDEX else None


def _kla_zeros_from_center(text: str, px: float, py: float
                           ) -> Optional[tuple[int, int]]:
    """``SampleCenterLocation`` + ``SampleSize`` 로 ``(zero_x, zero_y)`` 산출.

    실측 검증(``docs/디바이스_하드코딩_조사.md`` §6-K) — 사용자가 장비로 확인한 KLA↔Camtek
    대응 **7건이 두 device 에서 전부 일치**한다(T254 `zero=(9,4)`, TB500 `zero=(3,3)`).
    ``SampleTestPlan`` 범위와도 5웨이퍼 × 2축 전부 맞는다."""
    cm = _SAMPLE_CENTER_PAT.search(text)
    sm = _SAMPLE_SIZE_PAT.search(text)
    if cm is None or sm is None:
        return None
    try:
        cx, cy = float(cm.group(1)), float(cm.group(2))
        dia = float(sm.group(1)) * 1000.0        # SampleSize 는 mm 단위
    except ValueError:
        return None
    if not (_MIN_DIAMETER <= dia <= _MAX_DIAMETER):
        return None
    zx, zy = _kla_zero(cx, dia, px), _kla_zero(cy, dia, py)
    return None if zx is None or zy is None else (zx, zy)


def parse_kla_header(text: str) -> Optional[KlaGeometry]:
    """KLA ``.001`` 헤더 텍스트 → KlaGeometry.  DiePitch 가 없으면 None.

    ``zero_x``/``zero_y`` 는 **``SampleCenterLocation`` 기하**로 구한다 — Camtek 의
    ``col_origin``/``row_total`` 과 같은 규칙이라 두 장비가 자동으로 정렬된다.
    ⚠ ``KLA_ZERO_Y=4`` 를 상수로 쓰던 시절엔 **TB500 자재에서 row 가 1 컸다**(실측:
    `08235234EWA1` 은 3 이 맞다).  device 마다 다른 값이므로 상수로 되돌리지 말 것.

    그게 없을 때만 옛 경로로 폴백한다: ``zero_x`` 는 ``SampleTestPlan`` 의 XINDEX
    최솟값, ``zero_y`` 는 상수.  ⚠ 그 목록에는 **검사한 die 만** 있어서 맵 가장자리
    행/열이 통째로 미사용이면 최솟값이 맵 원점과 어긋난다 — 그래서 폴백이다.

    순수 함수 — 파일 없이 헤드리스 테스트할 수 있다.
    """
    pm = _DIEPITCH_PAT.search(text)
    if pm is None:
        return None
    try:
        px, py = float(pm.group(1)), float(pm.group(2))
    except ValueError:
        return None
    if not (_MIN_PITCH <= px <= _MAX_PITCH and _MIN_PITCH <= py <= _MAX_PITCH):
        return None

    zeros = _kla_zeros_from_center(text, px, py)
    if zeros is not None:
        return KlaGeometry(pitch_x=px, pitch_y=py, zero_x=zeros[0], zero_y=zeros[1],
                           source="DiePitch+SampleCenterLocation")

    zero_x, src = KLA_ZERO_X, "DiePitch"
    tm = _TESTPLAN_PAT.search(text)
    if tm:
        pairs = _INDEX_PAIR_PAT.findall(tm.group(1))
        if pairs:
            zero_x = -min(int(a) for a, _ in pairs)
            src = "DiePitch+SampleTestPlan"
    return KlaGeometry(pitch_x=px, pitch_y=py, zero_x=zero_x, zero_y=KLA_ZERO_Y,
                       source=src)


@lru_cache(maxsize=256)
def kla_geometry(folder: Path) -> KlaGeometry:
    """폴더의 KLA die 격자 기하.  못 읽으면 TB500 폴백(+경고).  fail-safe."""
    from . import kla_info        # 순환 import 회피

    try:
        info = kla_info._find_info_file(folder)
        if info is not None:
            with info.open("rb") as fh:
                head = fh.read(kla_info._HEAD_BYTES)
            geom = parse_kla_header(head.decode("utf-8", errors="replace"))
            if geom is not None:
                if geom.source == "DiePitch":
                    _LOG.warning(
                        "KLA %s 에 SampleTestPlan 이 없어 die 인덱스 원점 X 도 "
                        "기본값(%d)을 씁니다.", info.name, KLA_ZERO_X)
                return geom
        _LOG.warning(
            "KLA die 격자 정보를 찾지 못해 기본값을 씁니다: %s", folder)
        return FALLBACK_KLA
    except Exception:
        return FALLBACK_KLA
