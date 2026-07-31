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
                     CAMTEK_ROW_TOTAL, KLA_ZERO_X, KLA_ZERO_Y)

__all__ = ["CamtekGeometry", "KlaGeometry", "camtek_geometry", "kla_geometry",
           "FALLBACK_CAMTEK", "FALLBACK_KLA"]

_LOG = logging.getLogger("aoi.coords")

# 합리적 die pitch 범위(µm) — 엉뚱한 키를 읽었을 때 채택 방지.
_MIN_PITCH, _MAX_PITCH = 100.0, 500000.0

# Camtek geometry 파일 후보 (상대경로, X키, Y키) 우선순위.
# die map 을 담은 파일이 확인되면 이 튜플 맨 앞에 한 줄 추가하면 된다.
_CAMTEK_SOURCES = (
    ("Params_WaferInfo.ini", "DieStep_X", "DieStep_Y"),
    ("Params_WaferInfo.ini", "DieSize_X", "DieSize_Y"),
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
    ``x = X − INI_Col × pitch_x``,  ``y = Y − INI_Row × pitch_y``
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


# 데이터에서 못 읽을 때 쓰는 TB500 폴백 — 값은 models.py 가 보유한다.
FALLBACK_CAMTEK = CamtekGeometry(
    pitch_x=CAMTEK_PITCH_X, pitch_y=CAMTEK_PITCH_Y,
    col_origin=CAMTEK_COL_OFFSET, row_total=CAMTEK_ROW_TOTAL, source="fallback",
)
FALLBACK_KLA = KlaGeometry(
    pitch_x=CAMTEK_PITCH_X, pitch_y=CAMTEK_PITCH_Y,
    zero_x=KLA_ZERO_X, zero_y=KLA_ZERO_Y, source="fallback",
)


def _read_key(path: Path, key: str) -> Optional[float]:
    """INI 에서 ``key=값`` 을 읽어 float 로. 없거나 범위 밖이면 None."""
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
    return v if _MIN_PITCH <= v <= _MAX_PITCH else None


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


def _read_camtek_pitch(folder: Path) -> Optional[tuple[float, float, str]]:
    """``Params_WaferInfo.ini`` 에서 (pitch_x, pitch_y, 출처). 못 찾으면 None."""
    for base in _search_dirs(folder):
        for rel, kx, ky in _CAMTEK_SOURCES:
            px = _read_key(base / rel, kx)
            py = _read_key(base / rel, ky)
            if px is not None and py is not None:
                return (px, py, f"{rel}:{kx}/{ky}")
    return None


def _pitch_matches_grid(folder: Path, pitch_x: float, pitch_y: float) -> bool:
    """실측 불변식 ``Col == floor(X/pitch_x)`` 로 읽은 pitch 를 검산한다.

    같은 폴더의 ``ColorImageGrabingInfo.ini`` 항목을 쓴다.  INI 가 없거나 항목을 못
    읽으면 **검산을 건너뛴다**(True) — 검산 불가는 불일치가 아니다.
    """
    from . import camtek_ini      # 순환 import 회피 — 검산 시점에만 필요

    try:
        raw = camtek_ini.load_raw_folder(folder)
    except Exception:
        return True
    if not raw:
        return True
    return all(math.floor(X / pitch_x) == col_i
               and math.floor(Y / pitch_y) == row_i
               for X, Y, col_i, row_i in raw.values())


@lru_cache(maxsize=256)
def camtek_geometry(folder: Path) -> CamtekGeometry:
    """폴더의 Camtek die 격자 기하.  못 읽으면 TB500 폴백(+경고).  fail-safe."""
    try:
        found = _read_camtek_pitch(folder)
        if found is None:
            _LOG.warning(
                "Camtek die pitch 를 찾지 못해 기본값(%.1f×%.1f µm)을 씁니다 — "
                "폴더에 Params_WaferInfo.ini 가 없습니다: %s",
                CAMTEK_PITCH_X, CAMTEK_PITCH_Y, folder)
            return FALLBACK_CAMTEK
        px, py, src = found
        if not _pitch_matches_grid(folder, px, py):
            _LOG.warning(
                "%s 에서 읽은 die pitch(%.3f×%.3f µm)가 INI 의 Col/Row 와 맞지 않아 "
                "기본값을 씁니다: %s", src, px, py, folder)
            return FALLBACK_CAMTEK
        # col_origin·row_total 은 die 격자 **범위**라 현재 어떤 파일에도 없다.
        # 두 값은 ref/val 양쪽에 똑같이 걸려 매칭 거리에서 상쇄되고, 표시·엑셀의
        # col/row 에만 일정 오프셋으로 남는다.  die map 파일이 확인되면 여기서 읽는다.
        return CamtekGeometry(pitch_x=px, pitch_y=py,
                              col_origin=CAMTEK_COL_OFFSET,
                              row_total=CAMTEK_ROW_TOTAL, source=src)
    except Exception:
        return FALLBACK_CAMTEK


def parse_kla_header(text: str) -> Optional[KlaGeometry]:
    """KLA ``.001`` 헤더 텍스트 → KlaGeometry.  DiePitch 가 없으면 None.

    ``zero_x/zero_y`` 는 ``SampleTestPlan`` 의 die 인덱스 최솟값에서 나온다
    (TB500: XINDEX −3..3 → zero_x=3).  SampleTestPlan 이 없으면 폴백 값을 쓴다.
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

    zero_x, zero_y, src = KLA_ZERO_X, KLA_ZERO_Y, "DiePitch"
    tm = _TESTPLAN_PAT.search(text)
    if tm:
        pairs = _INDEX_PAIR_PAT.findall(tm.group(1))
        if pairs:
            xs = [int(a) for a, _ in pairs]
            ys = [int(b) for _, b in pairs]
            zero_x, zero_y = -min(xs), -min(ys)
            src = "DiePitch+SampleTestPlan"
    return KlaGeometry(pitch_x=px, pitch_y=py, zero_x=zero_x, zero_y=zero_y,
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
                        "KLA %s 에 SampleTestPlan 이 없어 die 인덱스 원점은 "
                        "기본값(%d,%d)을 씁니다.", info.name, KLA_ZERO_X, KLA_ZERO_Y)
                return geom
        _LOG.warning(
            "KLA die 격자 정보를 찾지 못해 기본값을 씁니다: %s", folder)
        return FALLBACK_KLA
    except Exception:
        return FALLBACK_KLA
