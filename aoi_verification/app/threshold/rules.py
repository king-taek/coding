r"""웨이퍼 폴더의 Zone 기준(``Zones\<Zone>.ini``)과 class 이름(``ManReClassify.ini``)을 읽는다.

Zone ini 의 ``[Surface Feature Filter …]`` 섹션 하나가 기준 한 줄이다
(``Classify`` · ``FeatureType`` · ``FilterType`` · ``LowValue`` · ``HighValue``).
**같은 ``Classify`` 끼리가 한 묶음(AND)이고, 묶음끼리는 OR** 다 — 묶음을 통과하면 그 class(= bin)가 된다.
TB500 RDL: 29 묶음(W · A · C) 또는 28 묶음(C · A), 비교 스캔 4번 3,470/3,470 일치(관측 2026-11-15).
FS UBM: 14 묶음 하나, Export 33/33 의 첫 분류가 14(관측 2026-11-16).

값의 단위는 **픽셀**이다(면적 px², 폭 px).  RTP.txt 의 같은 기준은 µm 라서 숫자가 달라 보인다
(TB500 RDL 면적 63.96 px² × 0.7708² = 38 µm² = RTP.txt, 관측).  화면에 µm 로 보일 때만 환산한다.

``Classify`` 가 음수이거나 ``Enable=0`` 인 섹션은 쓰지 않는다.
전 구간 fail-safe — 못 읽으면 빈 결과.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..coords.ini_text import read_ini_text

__all__ = ["Filter", "Zone", "ClassInfo", "read_zones", "read_classes", "zone_dir_for",
           "FEATURE_AREA", "FEATURE_WIDTH", "FEATURE_CONTRAST"]

# Zone ini FeatureType — 섹션 이름과 짝지어 본 값(수집 자료 Zone ini 전부, 관측 2026-11-16):
# 1 Area · 2 Width · 12 Contrast Average · 14 Polarity · 45 Abs Contrast Average.
# Surface.flt 에 값이 있는 것은 앞의 셋뿐이라 그 셋만 미리보기로 바꿀 수 있다.
FEATURE_AREA, FEATURE_WIDTH, FEATURE_CONTRAST = 1, 2, 12

_SECTION = re.compile(r"(?m)^\s*\[([^\]]+)\]\s*$")
_KEY = re.compile(r"(?im)^\s*(Enable|Classify|FeatureType|FilterType|LowValue|HighValue)\s*=\s*([-+\d.eE]+)")
_ZONE_ID = re.compile(r"(?im)^\s*ZoneID\s*=\s*(\d+)")
_ZONE_NAME = re.compile(r"(?im)^\s*ZoneName\s*=\s*(\S.*?)\s*$")
_RECIPE_DIR = re.compile(r"(?i)^Recipe(\d+)-Zones$")


@dataclass(frozen=True)
class Filter:
    feature: int        # FeatureType
    ftype: int          # FilterType 1~8 (RTP.txt 설명: 1 범위 안 · 2 범위 밖 · 3 = · 4 > · 5 ≥ · 6 < · 7 ≤ · 8 ≠)
    low: float
    high: float

    def passes(self, v: float) -> bool:
        lo, hi = self.low, self.high
        return {1: lo <= v <= hi, 2: not (lo <= v <= hi), 3: v == lo, 4: v > lo,
                5: v >= lo, 6: v < lo, 7: v <= lo, 8: v != lo}.get(self.ftype, True)


@dataclass(frozen=True)
class Zone:
    zone_id: int
    name: str
    bundles: dict       # Classify → tuple[Filter, …]  (묶음 안 AND, 묶음끼리 OR)


@dataclass(frozen=True)
class ClassInfo:
    name: str
    good: bool          # 분류 종류가 'Good'(예: EDC Good) — Defect 로 세지 않는다(현택님 2026-11-16)


def zone_dir_for(recipe) -> str:
    r"""Surface.flt 레코드의 ``recipe`` 값 → 그 Zone 폴더 이름.

    레시피 하나인 스캔은 recipe 0 · ``Zones\``(FS UBM), 여러 개면 1 · ``Zones\`` 와 N · ``RecipeN-Zones\``
    (TB500 No Image 1 · 2)였다 — 관측 2가지뿐이라 **가정**이다.  recipe 칸이 없는 옛 기록(None)은 ``Zones``."""
    return f"Recipe{recipe}-Zones" if isinstance(recipe, int) and recipe >= 2 else "Zones"


def _parse_zone(text: str) -> Zone | None:
    zid = _ZONE_ID.search(text)
    if not zid:
        return None
    name = _ZONE_NAME.search(text)
    bundles: dict[int, list[Filter]] = {}
    heads = list(_SECTION.finditer(text))
    for i, h in enumerate(heads):
        if not h.group(1).strip().lower().startswith("surface feature filter"):
            continue
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        kv = {k.lower(): v for k, v in _KEY.findall(body)}
        try:
            cls = int(float(kv["classify"]))
            f = Filter(int(float(kv["featuretype"])), int(float(kv["filtertype"])),
                       float(kv["lowvalue"]), float(kv.get("highvalue", kv["lowvalue"])))
        except (KeyError, ValueError):
            continue
        if cls >= 0 and kv.get("enable", "1").strip() not in ("0", "0.0"):
            bundles.setdefault(cls, []).append(f)
    return Zone(int(zid.group(1)), name.group(1) if name else "",
                {c: tuple(fs) for c, fs in bundles.items()})


@lru_cache(maxsize=64)
def read_zones(folder: Path) -> dict:
    r"""``{(Zone 폴더 이름, ZoneID): Zone}`` — ``Zones\`` 와 ``RecipeN-Zones\`` 전부."""
    out: dict = {}
    try:
        dirs = [d for d in folder.iterdir() if d.is_dir()
                and (d.name.lower() == "zones" or _RECIPE_DIR.match(d.name))]
    except OSError:
        return out
    for d in dirs:
        key = "Zones" if d.name.lower() == "zones" else f"Recipe{_RECIPE_DIR.match(d.name).group(1)}-Zones"
        try:
            files = sorted(d.glob("*.ini"))
        except OSError:
            continue
        for p in files:
            z = _parse_zone(read_ini_text(p) or "")
            if z is not None:
                out[(key, z.zone_id)] = z
    return out


@lru_cache(maxsize=64)
def read_classes(folder: Path) -> dict:
    """``ManReClassify.ini [General]`` 의 ``번호=이름,종류,…`` → ``{번호: ClassInfo}``.  없으면 빈 dict."""
    text = read_ini_text(folder / "ManReClassify.ini") or ""
    heads = list(_SECTION.finditer(text))           # [General] 만 — 뒤 섹션에도 '20=Cust' 같은 줄이 있다
    gen = next((i for i, h in enumerate(heads) if h.group(1).strip().lower() == "general"), None)
    if gen is None:
        return {}
    text = text[heads[gen].end(): heads[gen + 1].start() if gen + 1 < len(heads) else len(text)]
    out: dict = {}
    for m in re.finditer(r"(?m)^\s*(-?\d+)\s*=\s*([^,\r\n]*),([^,\r\n]*)", text):
        out[int(m.group(1))] = ClassInfo(m.group(2).strip(), m.group(3).strip().lower() == "good")
    return out
