"""Camtek INI 파일(ColorImageGrabingInfo.ini) 파싱 → DefectCoord.

변환식 — 상수가 아니라 폴더에서 읽은 :class:`~.wafer_geometry.CamtekGeometry` 를 쓴다
(die 크기가 다른 디바이스 지원)::

    col = Col - geom.col_origin
    row = geom.row_total - Row              # row_total = ceil(Diameter / pitch_y)
    x   = floor(X - Col × geom.pitch_x)     # 장비 표기 = 버림 (반올림 아님 — 실측:
    y   = floor(Y - Row × geom.pitch_y)     #   나머지 43180.809 를 장비는 43180 으로 표기)
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import CAMTEK_COL_OFFSET, DefectCoord
from .wafer_geometry import CamtekGeometry, FALLBACK_CAMTEK, camtek_geometry
from .ini_text import read_ini_text

__all__ = ["resolve", "load_folder", "load_abs_folder", "load_raw_folder"]

# INI 파일 이름 후보 — 대소문자 두 가지
_INI_CANDIDATES = ("ColorImageGrabingInfo.ini", "ColorImageGrabinginfo.ini")

_KEY_PAT = re.compile(r'^(\w+)\s*=\s*(.+)$', re.MULTILINE)
_SECTION_PAT = re.compile(r'\[([^\]]+)\]')


def _find_ini(folder: Path) -> Optional[Path]:
    for name in _INI_CANDIDATES:
        p = folder / name
        if p.exists():
            return p
    return None


@lru_cache(maxsize=256)
def load_folder(folder: Path) -> dict[str, DefectCoord]:
    """폴더의 INI 파일을 파싱해 {stem(소문자) → DefectCoord} 맵 반환.

    같은 폴더의 두 번째 이미지부터는 캐시에서 즉시 반환된다.
    """
    ini = _find_ini(folder)
    if ini is None:
        return {}
    try:
        # die 격자 기하는 폴더당 한 번만 구한다(자체 캐시).
        geom = camtek_geometry(folder)
        if geom is None:
            # ★ pitch 를 몰라도 **매칭은 된다** — INI 의 절대 X/Y 를 그대로 쓰면 된다.
            #   die 로 쪼개는 건 (a) 화면·엑셀의 die-내부 좌표 표기와 (b) KLA 교차 매칭에만
            #   필요하다.  Camtek↔Camtek 은 ref/val 이 같은 규칙이면 그만이다.
            return _parse_ini_abs(ini)
        return _parse_ini(ini, geom)
    except Exception:
        return {}


def _parse_ini(path: Path, geom: CamtekGeometry) -> dict[str, DefectCoord]:
    text = read_ini_text(path) or ""

    # 섹션 단위로 분리: [filename.jpeg] → 내용 반복
    parts = _SECTION_PAT.split(text)
    # parts[0] = 섹션 전 텍스트(무시), 이후 [이름, 내용, 이름, 내용, ...] 교대
    result: dict[str, DefectCoord] = {}
    it = iter(parts[1:])
    for name, content in zip(it, it):
        stem = Path(name.strip()).stem   # "foo.jpeg" → "foo"
        coord = _extract_coord(content, geom)
        if coord is not None:
            result[stem.lower()] = coord
    return result


def _extract_coord(content: str,
                   geom: CamtekGeometry = FALLBACK_CAMTEK) -> Optional[DefectCoord]:
    """INI 섹션 내용 → DefectCoord. 필수 키가 없으면 None.

    ``geom`` 은 폴더에서 읽은 die 격자 기하 — 생략하면 TB500 폴백."""
    raw = _extract_raw(content)
    if raw is None:
        return None
    X, Y, col_i, row_i = raw
    col = col_i - geom.col_origin
    row = geom.row_total - row_i
    # 버림(floor) — 장비 화면·정답 데이터와 표기를 일치시킨다.  거리 계산 변화는
    # ref/val 양쪽 동일 규칙으로 2 µm 미만이라 매칭 판정(20/500 µm)에 영향 없다.
    x = float(math.floor(X - col_i * geom.pitch_x))
    y = float(math.floor(Y - row_i * geom.pitch_y))
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_ini")


def _parse_ini_abs(path: Path) -> dict[str, DefectCoord]:
    """die pitch 를 모를 때의 폴백 — **절대 wafer 좌표**로 DefectCoord 를 만든다.

    · ``col`` 은 pitch 없이도 정확하다(``Col − CAMTEK_COL_OFFSET``).
    · ``row`` 은 기준(``ceil(Diameter/pitch_y)``)을 모르므로 **장비 화면 값을 낼 수 없다**.
      추측하지 않고 ``−Row`` 를 버킷 키로만 쓴다(음수 → 장비 값이 아님이 드러난다).
      ref/val 이 같은 규칙이라 ``(col,row) ±1`` 이웃 게이트는 정상 동작한다.
    · ``x/y`` 는 절대 좌표.  같은·이웃 die 안에서 **실제 물리 거리**로 재므로 die-내부
      비교보다 오히려 엄격하다(인접 die 의 같은 상대위치 결함이 거리 0 으로 보이던 문제가 없다).

    프레임이 다른 좌표(die-내부 x≈1만 vs 절대 x≈10만)와는 거리가 tol×3 을 한참 넘어
    **자동으로 기각**되므로 별도 가드가 필요 없다.
    """
    text = read_ini_text(path) or ""
    parts = _SECTION_PAT.split(text)
    result: dict[str, DefectCoord] = {}
    it = iter(parts[1:])
    for name, content in zip(it, it):
        raw = _extract_raw(content)
        if raw is None:
            continue
        X, Y, col_i, row_i = raw
        result[Path(name.strip()).stem.lower()] = DefectCoord(
            col=col_i - CAMTEK_COL_OFFSET, row=-row_i,
            x=float(math.floor(X)), y=float(math.floor(Y)),
            source="camtek_abs",
        )
    return result


def _extract_raw(content: str) -> Optional[tuple[float, float, int, int]]:
    """INI 섹션 내용 → 원시 ``(X, Y, Col, Row)``. 필수 키가 없으면 None."""
    kv: dict[str, str] = {}
    for m in _KEY_PAT.finditer(content):
        kv[m.group(1).upper()] = m.group(2).strip()

    def fget(key: str) -> Optional[float]:
        v = kv.get(key)
        try:
            return float(v) if v is not None else None
        except ValueError:
            return None

    X = fget("X") if fget("X") is not None else fget("FAULTX")
    Y = fget("Y") if fget("Y") is not None else fget("FAULTY")
    Col = fget("COL")
    Row = fget("ROW")

    if None in (X, Y, Col, Row):
        return None
    return (X, Y, int(Col), int(Row))   # type: ignore[arg-type]


@lru_cache(maxsize=256)
def load_raw_folder(folder: Path) -> dict[str, tuple[float, float, int, int]]:
    """폴더의 INI → ``{stem(소문자) → (X, Y, Col, Row)}`` 원시값.

    :func:`~.wafer_geometry.camtek_geometry` 가 읽은 die pitch 를 검산할 때 쓴다
    (``Col == floor(X/pitch_x)``).  변환 상수를 안 쓰므로 geometry 와 순환하지 않는다.
    """
    ini = _find_ini(folder)
    if ini is None:
        return {}
    try:
        text = read_ini_text(ini) or ""
        parts = _SECTION_PAT.split(text)
        result: dict[str, tuple[float, float, int, int]] = {}
        it = iter(parts[1:])
        for name, content in zip(it, it):
            raw = _extract_raw(content)
            if raw is not None:
                result[Path(name.strip()).stem.lower()] = raw
        return result
    except Exception:
        return {}


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """이미지 1장 → DefectCoord. INI 가 없거나 섹션이 없으면 None."""
    coords = load_folder(image_path.parent)
    return coords.get(image_path.stem.lower())


# ── 절대 좌표(원시 X/Y) — Surface.flt 매칭용 ──────────────────────────────
# 변환된 DefectCoord 와 달리, Surface.flt 의 ActualX/ActualY 와 직접 비교할
# **원시 절대 wafer 좌표** 를 그대로 보존한다.  기존 resolve/_parse_ini 경로는
# 손대지 않고, 같은 섹션 분리를 재사용해 X/Y(또는 FaultX/FaultY)만 뽑아 캐시한다.

@lru_cache(maxsize=256)
def load_abs_folder(folder: Path) -> dict[str, tuple[float, float]]:
    """폴더의 INI → {stem(소문자) → (절대 X, 절대 Y)}.  없으면 빈 dict."""
    ini = _find_ini(folder)
    if ini is None:
        return {}
    try:
        text = read_ini_text(ini) or ""
        parts = _SECTION_PAT.split(text)
        result: dict[str, tuple[float, float]] = {}
        it = iter(parts[1:])
        for name, content in zip(it, it):
            stem = Path(name.strip()).stem
            xy = _extract_abs(content)
            if xy is not None:
                result[stem.lower()] = xy
        return result
    except Exception:
        return {}


def _extract_abs(content: str) -> Optional[tuple[float, float]]:
    """INI 섹션 내용 → (절대 X, 절대 Y).  X/Y(또는 FaultX/FaultY) 없으면 None."""
    kv: dict[str, str] = {}
    for m in _KEY_PAT.finditer(content):
        kv[m.group(1).upper()] = m.group(2).strip()

    def fget(key: str) -> Optional[float]:
        v = kv.get(key)
        try:
            return float(v) if v is not None else None
        except ValueError:
            return None

    X = fget("X") if fget("X") is not None else fget("FAULTX")
    Y = fget("Y") if fget("Y") is not None else fget("FAULTY")
    if X is None or Y is None:
        return None
    return (X, Y)
