"""Defect 이름 — 장비가 결과 폴더에 남긴 이름 사전에서 읽는다(하드코딩 금지).

두 가지 이름이 있다(현택님 결정 2026-11-23: 둘 다 보인다):

- **종류 이름**(장비 화면의 'Missing Bump' · 'Long bright defect'): 레코드의 ``Type`` 비트를
  ``EquipmentClassifications.ini`` 에서 찾는다.  절은 검사 종류마다 있고([SURFACE] · [Height] …),
  각 절의 ``-1`` 값이 사진 정보 파일의 ``Type``(0 Surface · 2 Height · 7 Coplanarity)과 같다 —
  그래서 절 이름을 코드에 적지 않고 그 값으로 고른다.
- **분류 이름**(Export 의 Classify, 'B R Height LSL'): 레코드의 ``reclassify`` 번호를
  ``ManReClassify.ini [General]`` 의 ``번호=이름,…`` 에서 찾는다.

근거(관측 2026-11-23, AOI-16 T254 6324): 예시 Defect col14 row6 x1379 y1906 의 Height 레코드
Type 2097152 = [Height] 'Missing Bump', reclassify 51 = 'B R Height LSL' = Export Classify.

못 찾으면 빈 문자열.  전 구간 fail-safe.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .ini_text import read_ini_text

__all__ = ["type_name", "class_name"]

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$", re.M)
_ENTRY = re.compile(r"^[ \t]*(-?\d+)[ \t]*=[ \t]*([^;\r\n]*)", re.M)   # 줄을 넘지 않는다(빈 값이 다음 줄을 삼키지 않게)


def _sections(text: str) -> list[tuple[str, dict[int, str]]]:
    marks = list(_SECTION.finditer(text))
    out = []
    for i, m in enumerate(marks):
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        out.append((m.group(1).strip(), {int(k): v.strip() for k, v in _ENTRY.findall(body)}))
    return out


@lru_cache(maxsize=128)
def _kind_tables(folder: Path) -> tuple:
    """``EquipmentClassifications.ini`` → ``((사진 Type, {비트: 이름}), …)``."""
    try:
        text = read_ini_text(folder / "EquipmentClassifications.ini") or ""
    except OSError:
        return ()
    out = []
    for _name, table in _sections(text):
        kind = table.pop(-1, "")
        if kind.lstrip("-").isdigit():
            out.append((int(kind), {k: v for k, v in table.items() if v}))
    return tuple(out)


def type_name(folder: Path, photo_type: Optional[int], mask: Optional[int]) -> str:
    """검사 종류(사진 Type) · Type 비트 → 이름.  사전에 그 값이 없으면 빈 문자열."""
    try:
        if photo_type is None or mask is None:
            return ""
        table = next((t for k, t in _kind_tables(Path(folder)) if k == photo_type), None)
        if not table:
            return ""
        return table.get(mask, "")      # 비트가 여럿 켜진 값은 실물에 없었다(관측) — 지어내지 않는다
    except Exception:
        return ""


@lru_cache(maxsize=128)
def _class_table(folder: Path) -> tuple:
    try:
        text = read_ini_text(folder / "ManReClassify.ini") or ""
    except OSError:
        return ()
    for name, table in _sections(text):
        if name.lower() == "general":
            return tuple((k, v.split(",")[0].strip()) for k, v in table.items() if v)
    return ()


def class_name(folder: Path, reclassify: Optional[int]) -> str:
    """분류 번호 → 이름(Export 의 Classify).  없으면 빈 문자열."""
    try:
        if reclassify is None:
            return ""
        # 음수는 사람이 다시 분류한 표시이고 이름은 절댓값의 것이다(−22 = Scratch, −1 = Undersized Bump:
        # Export Classify 43/43, 관측 2026-11-23 · 2026-11-16 Threshold 자료의 −2 Missing Bump 와 같은 규칙).
        return dict(_class_table(Path(folder))).get(abs(int(reclassify)), "")
    except Exception:
        return ""
