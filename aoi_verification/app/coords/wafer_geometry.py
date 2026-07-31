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

from .models import (CAMTEK_COL_OFFSET, CAMTEK_PITCH_X, CAMTEK_PITCH_Y,
                     DEFAULT_WAFER_DIAMETER, KLA_ZERO_X, KLA_ZERO_Y)

__all__ = ["CamtekGeometry", "KlaGeometry", "camtek_geometry", "kla_geometry",
           "has_camtek_entries", "FALLBACK_CAMTEK", "FALLBACK_KLA"]

_LOG = logging.getLogger("aoi.coords")

# 합리적 die pitch 범위(µm) — 엉뚱한 키를 읽었을 때 채택 방지.
_MIN_PITCH, _MAX_PITCH = 100.0, 500000.0
# 합리적 웨이퍼 직경 범위(µm) — 2인치(50 mm) ~ 450 mm.
_MIN_DIAMETER, _MAX_DIAMETER = 50000.0, 450000.0

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


@dataclass(frozen=True)
class CamtekGeometry:
    """Camtek INI 좌표 변환에 필요한 die 격자 기하.

    ``col = INI_Col − col_origin``,  ``row = row_total − INI_Row``,
    ``x = floor(X − INI_Col × pitch_x)``,  ``y = floor(Y − INI_Row × pitch_y)``

    ``row_total`` 은 상수가 아니라 **``ceil(Diameter / pitch_y)`` 유도값**이다 —
    장비 화면 정답이 있는 4개 사례(3개 device)에서 전부 성립함을 확인했다
    (TB500 은 7, pitch_y 31831.4 인 device 는 10).
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


def _row_total(diameter: float, pitch_y: float) -> int:
    """row 번호 기준 = ``ceil(Diameter / pitch_y)`` (4-device 실측으로 확정된 유도식)."""
    return math.ceil(diameter / pitch_y)


# 데이터에서 못 읽을 때 쓰는 TB500 폴백 — 값은 models.py 가 보유한다.
FALLBACK_CAMTEK = CamtekGeometry(
    pitch_x=CAMTEK_PITCH_X, pitch_y=CAMTEK_PITCH_Y,
    col_origin=CAMTEK_COL_OFFSET,
    row_total=_row_total(DEFAULT_WAFER_DIAMETER, CAMTEK_PITCH_Y),   # = 7
    source="fallback",
)
FALLBACK_KLA = KlaGeometry(
    pitch_x=CAMTEK_PITCH_X, pitch_y=CAMTEK_PITCH_Y,
    zero_x=KLA_ZERO_X, zero_y=KLA_ZERO_Y, source="fallback",
)


def _read_key(path: Path, key: str, lo: float = _MIN_PITCH,
              hi: float = _MAX_PITCH) -> Optional[float]:
    """INI 에서 ``key=값`` 을 읽어 float 로. 없거나 [lo, hi] 밖이면 None."""
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
    """실측 불변식 ``Col == floor(X/pitch_x)`` 로 pitch 를 검산한다.

    반환 ``(통과, 의미있음)``.  '의미있음' 은 검산이 실제로 pitch 를 제약했는지다 —
    모든 항목이 ``Col=0`` 이면 어떤 pitch 든 통과하므로 검산이 아무 말도 못 한 것이다.
    파일에서 읽은 값은 '통과' 만으로 채택하지만, 상수 후보는 '의미있음' 까지 요구한다.
    """
    from . import camtek_ini      # 순환 import 회피 — 검산 시점에만 필요

    try:
        raw = camtek_ini.load_raw_folder(folder)
    except Exception:
        return (False, False)
    if not raw:
        return (False, False)
    ok = all(math.floor(X / pitch_x) == col_i
             and math.floor(Y / pitch_y) == row_i
             for X, Y, col_i, row_i in raw.values())
    meaningful = (any(col_i >= 1 for _, _, col_i, _ in raw.values())
                  and any(row_i >= 1 for *_, row_i in raw.values()))
    return (ok, meaningful)


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
            # row 기준은 유도값(ceil(Diameter/pitch_y)) — col 오프셋만 상수로 남는다.
            return CamtekGeometry(pitch_x=px, pitch_y=py,
                                  col_origin=CAMTEK_COL_OFFSET,
                                  row_total=_row_total(_read_diameter(folder), py),
                                  source=src)
        # ★ Camtek INI 자체가 없는 폴더(KLA 슬롯·LIVE 파일명 슬롯)는 **조용히** None.
        #   경고는 '변환할 항목이 있는데 pitch 를 못 정한' 진짜 문제일 때만 낸다 —
        #   안 그러면 KLA 폴더마다 무의미한 경고가 쌓인다.
        if has_camtek_entries(folder):
            _LOG.warning(
                "die pitch 를 확정하지 못해 이 폴더의 좌표를 만들지 않습니다 "
                "(Params_WaferInfo.ini `DieStep_X/Y` 또는 ProductInfo.ini "
                "`XDieIndex/YDieIndex` 가 필요합니다): %s", folder)
        return None
    except Exception:
        return None


def parse_kla_header(text: str) -> Optional[KlaGeometry]:
    """KLA ``.001`` 헤더 텍스트 → KlaGeometry.  DiePitch 가 없으면 None.

    ``zero_x`` 만 ``SampleTestPlan`` 의 XINDEX 최솟값에서 산출한다(XINDEX −3..3 → 3).

    ⚠ ``zero_y`` 는 산출하지 않는다.  ``SampleTestPlan`` 에는 **검사한 die 만** 있어서,
    맵 가장자리 행이 통째로 미사용이면 최솟값이 맵 원점과 어긋난다 — 실측 자재가 정확히
    그 경우다(세로 맵 0..6 중 1..6 만 사용 → 산출값 3, 정답 4).  가로는 0..6 을 전부 써서
    산출값이 맞는다.  자세한 근거는 :mod:`.models` 의 ``KLA_ZERO_Y`` 주석 참조.

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
