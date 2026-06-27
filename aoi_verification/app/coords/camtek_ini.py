"""Camtek INI 파일(ColorImageGrabingInfo.ini) 파싱 → DefectCoord.

변환식 (TB500 기준):
    col = Col - 2
    row = 7 - Row
    x   = X - Col × 37247.7
    y   = Y - Row × 44905.4
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import (DefectCoord, CAMTEK_COL_OFFSET, CAMTEK_ROW_TOTAL,
                     CAMTEK_PITCH_X, CAMTEK_PITCH_Y)

__all__ = ["resolve", "load_folder"]

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
        return _parse_ini(ini)
    except Exception:
        return {}


def _parse_ini(path: Path) -> dict[str, DefectCoord]:
    text = path.read_text(encoding="utf-8", errors="replace")

    # 섹션 단위로 분리: [filename.jpeg] → 내용 반복
    parts = _SECTION_PAT.split(text)
    # parts[0] = 섹션 전 텍스트(무시), 이후 [이름, 내용, 이름, 내용, ...] 교대
    result: dict[str, DefectCoord] = {}
    it = iter(parts[1:])
    for name, content in zip(it, it):
        stem = Path(name.strip()).stem   # "foo.jpeg" → "foo"
        coord = _extract_coord(content)
        if coord is not None:
            result[stem.lower()] = coord
    return result


def _extract_coord(content: str) -> Optional[DefectCoord]:
    """INI 섹션 내용 → DefectCoord. 필수 키가 없으면 None."""
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

    col_i = int(Col)   # type: ignore[arg-type]
    row_i = int(Row)   # type: ignore[arg-type]
    col = col_i - CAMTEK_COL_OFFSET
    row = CAMTEK_ROW_TOTAL - row_i
    x = X - col_i * CAMTEK_PITCH_X   # type: ignore[operator]
    y = Y - row_i * CAMTEK_PITCH_Y   # type: ignore[operator]
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_ini")


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """이미지 1장 → DefectCoord. INI 가 없거나 섹션이 없으면 None."""
    coords = load_folder(image_path.parent)
    return coords.get(image_path.stem.lower())
