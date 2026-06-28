"""recipe 코드 → 이름 매핑을 결과 폴더의 recipe 파일에서 읽는다.

recipe 이름도 zone 이름처럼 **자재/제품별로 다르다** — 같은 recipe 코드가 제품마다
``PI_Bubble``/``PI`` 등으로 다르게 매핑된다. 하드코딩하지 말고 결과 폴더에서 읽는다.

실측 형식(주 출처): ``RecipesInfo.ini`` 의 섹션 ``[Recipe-<N>]`` 안 ``Name=`` 값.
  예) ``[Recipe-1] Name=PI_Bubble`` → recipe 코드 1 = PI_Bubble,  ``[Recipe-2] Name=PI``.
Surface.flt 의 recipe 코드가 곧 ``[Recipe-<N>]`` 의 N 이다(실측 대조 일치).
대안 형식(폴백): ``RecipeName``/``RecipeNumber`` 키 쌍.

못 찾으면 빈 매핑(이름 없이 코드만).  전 구간 fail-safe.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_SECTION = re.compile(r"\[[^\]]*\]")
_NAME = re.compile(r"(?im)^\s*RecipeName\s*=\s*(.+?)\s*$")
_ID = re.compile(r"(?im)^\s*RecipeNumber\s*=\s*(\d+)")
# RecipesInfo.ini 의 [Recipe-<N>] 섹션 + 그 안의 Name=
_RECIPE_SEC = re.compile(r"\[Recipe-(\d+)\]([^\[]*)", re.IGNORECASE)
_SEC_NAME = re.compile(r"(?im)^\s*Name\s*=\s*(.+?)\s*$")
# 우선 탐색 파일(빠른 경로). 못 찾으면 폴더 안 모든 *.ini 로 확대.
_PREFERRED = ("RecipesInfo.ini", "ProductInfo.ini", "Recipe2-ProductInfo.ini")


def _scan_sectioned(folder: Path) -> dict:
    """RecipesInfo.ini 의 [Recipe-<N>] Name= → {N: name} (실측 주 형식)."""
    out: dict = {}
    try:
        txt = (folder / "RecipesInfo.ini").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for m in _RECIPE_SEC.finditer(txt):
        nm = _SEC_NAME.search(m.group(2))
        if nm:
            out.setdefault(int(m.group(1)), nm.group(1).strip())
    return out


def _scan(paths) -> dict:
    """주어진 ini 파일들에서 (RecipeName, RecipeNumber) 쌍 → {id: name} (폴백 형식)."""
    out: dict = {}
    for p in paths:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for body in _SECTION.split(txt):
            nm = _NAME.search(body)
            rid = _ID.search(body)
            if nm and rid:
                out.setdefault(int(rid.group(1)), nm.group(1).strip())
    return out


@lru_cache(maxsize=256)
def recipe_map(folder: Path) -> tuple:
    """{recipe_number: recipe_name} 을 (id, name) 튜플들로 반환(hashable).  fail-safe.

    주 형식([Recipe-<N>] Name=) → 폴백(RecipeName/RecipeNumber 키) → 폴더 전체 *.ini 순."""
    try:
        out = _scan_sectioned(folder)
        if not out:
            out = _scan(folder / fn for fn in _PREFERRED)
        if not out:
            try:
                out = _scan(sorted(folder.glob("*.ini")))
            except OSError:
                pass
    except Exception:
        return ()
    return tuple(sorted(out.items()))


def name_for(folder: Path, recipe: int) -> Optional[str]:
    """결과 폴더 기준 recipe 코드의 이름.  없으면 None."""
    try:
        for rid, nm in recipe_map(folder):
            if rid == recipe:
                return nm
    except Exception:
        return None
    return None
