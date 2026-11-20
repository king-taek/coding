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

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import logging

from ..coords.ini_text import read_ini_text
from ..coords.pixel_size import scan_pixel_size_xy, scan_pixel_size_xy_recipe

_LOG = logging.getLogger("aoi.threshold")

__all__ = ["Filter", "Zone", "ClassInfo", "read_zones", "read_classes", "zone_dir_for", "zones_in_flt_px",
           "recipe_scale", "read_recipe_names", "read_detect_values",
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
        # DirEntry.is_dir() 는 열거 때 받은 값을 쓴다 — Path.is_dir() 는 항목마다 stat 이라 사진이 수천 장인
        # 웨이퍼 폴더에서 NAS 왕복이 수천 번 났다(측정: Scan 3000장 폴더 stat 3006회).
        with os.scandir(folder) as it:
            dirs = [folder / e.name for e in it if e.is_dir()
                    and (e.name.lower() == "zones" or _RECIPE_DIR.match(e.name))]
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


def recipe_scale(folder: Path, zone_dir: str):
    """``RecipeN-Zones`` 기준 값(레시피 N 의 픽셀) → Surface.flt 값의 픽셀(폴더 기준)로 바꾸는 ``(선형 배율, 면적 배율)``.

    ``Zones``(레시피 1 · 하나뿐인 스캔)이거나, 두 픽셀 크기를 하나라도 모르거나, 같으면 ``(1.0, 1.0)``.
    폭 · 길이는 X 픽셀 크기 비, 면적은 X·Y 픽셀 크기 비다.  모르는 값을 짐작해 채우지 않는다(모르면 경고만)."""
    m = _RECIPE_DIR.match(zone_dir)
    if not m or int(m.group(1)) < 2:
        return 1.0, 1.0
    ref = scan_pixel_size_xy(folder)
    rec = scan_pixel_size_xy_recipe(folder, int(m.group(1)))
    if not ref or not rec:
        _LOG.warning("레시피 %s 픽셀 크기를 몰라 Zone 기준을 폴더 기준으로 못 바꿈: %s (폴더 %s · 레시피 %s)",
                     m.group(1), folder, ref, rec)
        return 1.0, 1.0
    lin, area = rec[0] / ref[0], (rec[0] * rec[1]) / (ref[0] * ref[1])
    return (lin, area) if abs(lin - 1.0) > 1e-9 or abs(area - 1.0) > 1e-9 else (1.0, 1.0)


def zones_in_flt_px(folder: Path, zones: dict) -> dict:
    r"""``read_zones`` 결과를 **Surface.flt 와 같은 픽셀 단위**로 바꾼 사본.  ``Zones`` 는 그대로.

    왜: Surface.flt 의 면적 · BlobBreadth · BlobFeretMax 는 **레시피와 상관없이 폴더 픽셀 크기**
    (``Params_WaferInfo RefPixelSizeX``)의 px 다(Export Defects.csv µm = flt × 폴더 픽셀, 정확히 일치).
    그런데 ``RecipeN-Zones`` 의 면적(px²) · 폭(px) 기준은 **그 레시피 자기 픽셀 크기**
    (``RecipeN-ProductInfo.ini Scan2DPixelSize``)의 px 다.  AOI-20 TB500_RDL3 Multi(11장, 관측 2026-11-20):
    레시피 3(x20, 0.3859) Surface_RDL 폭 5.1832932 px × 0.3859 = 2.000 µm · 면적 26.8665 px² × 0.3859² = 4.00 µm²
    — 장비 기준은 µm 로 2 · 4.  숫자를 그대로 flt 와 비교하면 레시피 3 Defect 가 0/190 통과, µm 로 맞추면 190/190
    통과(= 장비).  레시피 2(x5, 1.538)는 폴더 1.536 과 거의 같아 어느 쪽이든 같았다.  Contrast 는 단위 없음.
    FeatureType 1(면적) × (레시피 px/폴더 px)² · 2(폭)× 비.  RichSurface 값도 같은 폴더 px 라고 **가정**한다(미확인).
    장비에 적을 값은 :func:`to_recipe_px` 로 되돌린다."""
    out: dict = {}
    for key, z in zones.items():
        lin, area = recipe_scale(folder, key[0])
        if lin == 1.0 and area == 1.0:
            out[key] = z
            continue
        k = {FEATURE_AREA: area, FEATURE_WIDTH: lin}
        out[key] = Zone(z.zone_id, z.name, {c: tuple(
            Filter(f.feature, f.ftype, f.low * k.get(f.feature, 1.0), f.high * k.get(f.feature, 1.0)) for f in fs)
            for c, fs in z.bundles.items()})
    return out


_SURF = ("Low_Delta", "High_Delta", "Bright_Thr", "DarkArea", "BrightArea", "DarkDiameter", "BrightDiameter")
_GEN = ("AdaptDetect_Dark_Contrast_Seed", "AdaptDetect_Dark_Contrast_Body", "AdaptDetect_Bright_Contrast_Seed",
        "AdaptDetect_Bright_Contrast_Body", "FixedDetect_Dark_Contrast_Seed", "FixedDetect_Bright_Contrast_Seed")


def read_detect_values(folder: Path, zone_dir: str, zone_id: int) -> list:
    """그 Zone ini 의 검출 값(앱에서 못 바꿈) → ``[(이름, 값 글자)]``.  ``[Surface]`` 알고리즘 Zone 과
    ``[GenesisV12]`` Zone 의 정해진 키 중 파일에 있는 것만(값은 쓰인 그대로 — 단위 환산 안 함).  못 읽으면 빈 목록."""
    try:
        for p in sorted((Path(folder) / zone_dir).glob("*.ini")):
            text = read_ini_text(p) or ""
            zid = _ZONE_ID.search(text)
            if not zid or int(zid.group(1)) != zone_id:
                continue
            heads = list(_SECTION.finditer(text))
            out = []
            for sect, keys in (("surface", _SURF), ("genesisv12", _GEN)):
                for i, h in enumerate(heads):
                    if h.group(1).strip().lower() != sect:
                        continue
                    body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
                    kv = {k.lower(): v.strip() for k, v in re.findall(r"(?m)^\s*([^=\s;#][^=\r\n]*?)\s*=\s*([^\r\n]*)", body)}
                    out += [(k, kv[k.lower()]) for k in keys if kv.get(k.lower(), "") != ""]
            return out
    except OSError:
        pass
    return []


@lru_cache(maxsize=64)
def read_recipe_names(folder: Path) -> dict:
    """``RecipesInfo.ini [Recipe-N] Name=`` → ``{N: 이름}``.  없으면 빈 dict."""
    text = read_ini_text(Path(folder) / "RecipesInfo.ini") or ""
    heads = list(_SECTION.finditer(text))
    out: dict = {}
    for i, h in enumerate(heads):
        m = re.fullmatch(r"(?i)recipe-(\d+)", h.group(1).strip())
        if not m:
            continue
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        nm = re.search(r"(?im)^\s*Name\s*=\s*(\S.*?)\s*$", body)
        if nm:
            out[int(m.group(1))] = nm.group(1)
    return out
