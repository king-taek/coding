r"""zone 코드 → 이름 매핑을 결과 폴더의 zone 정의 파일에서 읽는다.

zone 이름은 **자재/제품(recipe)별로 다르다** — 실측: 같은 zone 1 이 어떤 제품에선
``PI_Opening``, 다른 제품에선 ``VIA`` 또는 ``RDL``. 따라서 하드코딩하지 말고 결과 폴더에서 읽는다.

실측 형식(주 출처): 결과 폴더 아래 ``Zones\<이름>.ini`` (제품마다 zone 1개당 1파일) 의
``[General]`` 섹션에 ``ZoneName`` + ``ZoneID`` 가 있다.  이 **ZoneID 가 Surface.flt 의 zone
코드와 일치**한다(예: Scan Area=63, PI_Opening=1, RDL=2, PostProcess=255).
``Recipe2-Zones\`` · ``Recipe3-Zones\`` … 서브폴더도 같은 형식.  대표 ini(폴더 루트) 에도 같은 키가 있으면 쓴다.

못 찾으면 빈 매핑(이름 없이 코드만).  전 구간 fail-safe.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional
from .ini_text import read_ini_text

_SECTION = re.compile(r"\[[^\]]*\]")
_NAME = re.compile(r"(?im)^\s*ZoneName[ \t]*=[ \t]*(\S.*?)\s*$")
_ID = re.compile(r"(?im)^\s*ZoneID[ \t]*=[ \t]*(\d+)")
# 우선 탐색 파일(빠른 경로). 못 찾으면 폴더 안 모든 *.ini 로 확대.
_PREFERRED = ("ProductInfo.ini", "RecipesInfo.ini", "Recipe2-ProductInfo.ini")


def _scan(paths) -> dict:
    """주어진 ini 파일들에서 (ZoneName, ZoneID) 쌍 → {id: name}."""
    out: dict = {}
    for p in paths:
        try:
            txt = read_ini_text(p) or ""
        except OSError:
            continue
        # 각 zone 섹션에 ZoneName + ZoneID 가 함께 있다.
        for body in _SECTION.split(txt):
            nm = _NAME.search(body)
            zid = _ID.search(body)
            if nm and zid:
                out.setdefault(int(zid.group(1)), nm.group(1).strip())
    return out


_ZONE_SUBDIRS = ("Zones", "Recipe2-Zones")
_RECIPE_ZONES = re.compile(r"^Recipe(\d+)-Zones$", re.IGNORECASE)


def _zone_dirs(folder: Path) -> list[str]:
    """``Zones`` · ``Recipe2-Zones`` 다음에 ``Recipe3-Zones`` 이상(멀티 레시피 4개, AOI-16 T254 관측 2026-11-23)."""
    extra = []
    try:
        extra = sorted((d.name for d in folder.iterdir()
                        if d.is_dir() and _RECIPE_ZONES.match(d.name)
                        and d.name.lower() not in {x.lower() for x in _ZONE_SUBDIRS}),
                       key=lambda n: int(_RECIPE_ZONES.match(n).group(1)))
    except OSError:
        pass
    return [*_ZONE_SUBDIRS, *extra]


def _recipe_dir(recipe: int) -> str:
    """레시피 N 의 Zone 폴더 이름 — 1 은 ``Zones``, N ≥ 2 는 ``RecipeN-Zones``.

    가정(미구분): 수집한 멀티 레시피 웨이퍼(AOI-16 T254 4 레시피)는 폴더마다 같은 ZoneID 에 같은
    이름이라, 이 짝짓기와 '폴더를 합쳐 앞 것 우선' 을 표본이 가르지 못한다.  이름이 다른 실물이
    오면 장비 Export 의 Type(``<검사>-<Zone>``)으로 확인한다."""
    return "Zones" if recipe <= 1 else f"Recipe{recipe}-Zones"


@lru_cache(maxsize=256)
def zone_map(folder: Path) -> tuple:
    """{zone_id: zone_name} 을 (id, name) 튜플들로 반환(캐시용 hashable).  fail-safe.

    주 출처는 ``Zones\\*.ini`` / ``Recipe2-Zones\\*.ini`` 서브폴더(각 파일=1 zone,
    ZoneID 가 Surface.flt 코드와 일치).  그다음 폴더 루트 대표 ini, 끝으로 루트 전체
    *.ini.  (실측 '(매핑없음)' 은 정의가 Zones\\ 서브폴더에 있었기 때문)."""
    out: dict = {}
    try:
        for sub in _zone_dirs(folder):
            try:
                files = sorted((folder / sub).glob("*.ini"))
            except OSError:
                files = []
            for zid, nm in _scan(files).items():
                out.setdefault(zid, nm)
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


@lru_cache(maxsize=256)
def _recipe_zone_map(folder: Path, recipe: int) -> tuple:
    try:
        return tuple(sorted(_scan(sorted((folder / _recipe_dir(recipe)).glob("*.ini"))).items()))
    except Exception:
        return ()


def name_for(folder: Path, zone: int, recipe: Optional[int] = None) -> Optional[str]:
    """결과 폴더 기준 zone 코드의 이름.  없으면 None.

    ``recipe`` 를 주면 그 레시피의 Zone 폴더를 먼저 본다(없으면 예전처럼 모든 폴더를 합친 표)."""
    try:
        if recipe is not None:
            for zid, nm in _recipe_zone_map(folder, int(recipe)):
                if zid == zone:
                    return nm
        for zid, nm in zone_map(folder):
            if zid == zone:
                return nm
    except Exception:
        return None
    return None
