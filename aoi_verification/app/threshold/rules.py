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

import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import logging

from ..coords.ini_text import read_ini_text
from ..coords.pixel_size import recipe_pixel_pair, scan_pixel_size_xy, scan_pixel_size_xy_recipe

_LOG = logging.getLogger("aoi.threshold")

__all__ = ["Filter", "Zone", "ClassInfo", "read_zones", "read_classes", "zone_dir_for",
           "surface_result_types", "event_mask", "zones_in_flt_px", "stage_masks",
           "DETECT_KEYS", "detect_polarity",
           "recipe_scale", "read_recipe_names", "read_detect_values", "recipe_identity", "same_recipe",
           "read_scan_optics",
           "FEATURE_AREA", "FEATURE_WIDTH", "FEATURE_LENGTH", "FEATURE_CONTRAST", "condition_matters", "same_value"]

# Zone ini FeatureType — 섹션 이름과 짝지어 본 값(수집 자료 Zone ini 전부, 관측 2026-11-16):
# 1 Area · 2 Width · 3 Length · 12 Contrast Average · 14 Polarity · 45 Abs Contrast Average.
# Surface.flt 에 값이 있는 것은 앞의 넷뿐이라 그 넷만 미리보기로 바꿀 수 있다.
# Length = flt ``BlobLength``(BlobFeretMax 아님): Test_TB500_RDL2 - Multi x5 Scan Area 42장 Event Defect 232/232 가
# 묶음 판정과 맞았다(FeretMax 면 224/232, 관측 2026-11-23).  수집 Zone ini 중 Length 기준은 이 레시피뿐이다.
FEATURE_AREA, FEATURE_WIDTH, FEATURE_LENGTH, FEATURE_CONTRAST = 1, 2, 3, 12

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
    name: str = field(default="", compare=False)    # ini 섹션 제목('Surface Feature Filter Width') — 화면 이름표용

    def passes(self, v: float) -> bool:
        lo, hi = self.low, self.high
        lo_eq, hi_eq = same_value(v, lo), same_value(v, hi)
        inside = (lo <= v or lo_eq) and (v <= hi or hi_eq)
        return {1: inside, 2: not inside, 3: lo_eq, 4: v > lo,
                5: v >= lo or lo_eq, 6: v < lo, 7: v <= lo or lo_eq, 8: v != lo}.get(self.ftype, True)


# 레시피 N 기준을 flt 픽셀로 옮기면(× 배율) 장비가 계산한 같은 값과 끝자리만 달라진다 — 레시피 2 DarkArea 35 px ×
# (0.7698/0.3852)² = 139.78201638182864, 장비 flt 값 139.7820163818286(관측 2026-11-23, 같은 값).  그 부동소수 반올림만
# 같은 값으로 본다(상대 1e-9 — 보정값이 아니다: 값을 옮기지 않고, 그보다 큰 차이는 그대로 다르다).  같음을 포함하는
# 비교(≥ · ≤ · 범위 · =)에만 쓴다 — 엄격 비교(> · <)는 그대로 둔다(자동 추천이 '바로 아래 실수' 로 내려 놓는다).
_SAME_REL = 1e-9


def same_value(a: float, b: float) -> bool:
    if a == b:
        return True
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= _SAME_REL * max(abs(a), abs(b))


@dataclass(frozen=True)
class Zone:
    zone_id: int
    name: str
    bundles: dict       # Classify → tuple[Filter, …]  (묶음 안 AND, 묶음끼리 OR)
    # 검출 단계 하한(``[Surface]`` DarkArea · DarkDiameter · BrightArea · BrightDiameter) → ``{(극성, 종류): 값}``.
    # 극성 'dark'/'bright', 종류 'area'(px²)/'width'(px).  0 이하·없음은 안 적는다(꺼짐).  레시피 자기 픽셀 단위.
    detect: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ClassInfo:
    name: str
    good: bool          # 분류 종류가 'Good'(예: EDC Good) — Defect 로 세지 않는다(현택님 2026-11-16)


def zone_dir_for(recipe) -> str:
    r"""Surface.flt 레코드의 ``recipe`` 값 → 그 Zone 폴더 이름.

    레시피 하나인 스캔은 recipe 0 · ``Zones\``(FS UBM), 여러 개면 1 · ``Zones\`` 와 N · ``RecipeN-Zones\``
    (TB500 No Image 1 · 2)였다 — 관측 2가지뿐이라 **가정**이다.  recipe 칸이 없는 옛 기록(None)은 ``Zones``."""
    return f"Recipe{recipe}-Zones" if isinstance(recipe, int) and recipe >= 2 else "Zones"


# [Surface] 키 → (극성, 종류).  Area 는 flt ``area`` · Diameter 는 flt ``BlobBreadth``(폭)와 비교한다 —
# TB500_RDL3 Multi x5: DarkArea 38 · DarkDiameter 3 → Type 8 677개 최소 area 38.1 · 폭 3.01, Space BrightArea 30 ·
# BrightDiameter 3 → Type 16 최소 31.1 · 3.17(관측 2026-11-21).  비교는 이상(≥)으로 **가정**(표본 최소값이 기준 바로 위).
DETECT_KEYS = {"darkarea": ("dark", "area"), "darkdiameter": ("dark", "width"), "darklength": ("dark", "length"),
               "brightarea": ("bright", "area"), "brightdiameter": ("bright", "width"), "brightlength": ("bright", "length")}
# 이 값 이상이면 그 조건을 끈 것(장비가 '9999999' 를 적는다) — 그 갈래로는 통과하지 못한다(inf 로 둔다).
DETECT_OFF = 9_999_999.0


def _parse_detect(text: str, heads) -> dict:
    """첫 ``[Surface]``(AlgorithmName=Surface · Enable=1)의 검출 단계 하한 → ``{(극성, 종류): 값}``.
    종류 = 'area' · 'width'(Diameter) · 'length'.  0 = 하한 없음(늘 통과), 9999999 이상 = 끈 조건(inf)."""
    for i, h in enumerate(heads):
        if h.group(1).strip().lower() != "surface":
            continue
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        kv = {k.lower(): v.strip() for k, v in re.findall(r"(?m)^\s*([^=\s;#][^=\r\n]*?)\s*=\s*([^\r\n]*)", body)}
        if kv.get("algorithmname", "").lower() != "surface" or kv.get("enable", "1") not in ("1", "1.0"):
            continue
        out = {}
        for k, key in DETECT_KEYS.items():
            try:
                v = float(kv[k])
            except (KeyError, ValueError):
                continue
            # 끈 조건(9999999)은 inf — 그 갈래로는 통과 못 함.  0 은 하한 없음(늘 통과)이지만 키가 있다는 사실은
            # 남긴다: Area 를 못 넘어도 Length 가 0 이면 남는다(키가 아예 없을 때만 그 갈래를 안 본다).
            out[key] = math.inf if v >= DETECT_OFF else max(v, 0.0)
        return out
    return {}


def condition_matters(f: Filter) -> bool:
    """이 조건에 걸려 빠질 수 있는 값(0 이상 · :data:`DETECT_OFF` 미만)이 하나라도 있나.

    현택님(2026-11-23): Length 기준은 파라미터에 있으면 넣고, **무의미하게** 적혀 있으면 뺀다.  무의미 =
    늘 통과하는 조건 — ``≥ 0 이하`` · ``> 음수`` · ``≤ / < 9999999 이상`` · ``범위 [0 이하, 9999999 이상]``.
    크기 값은 음수가 없고 장비는 9999999 를 '끈 값' 으로 적는다(:data:`DETECT_OFF`).  빼도 판정은 같다(늘 통과)."""
    lo, hi = f.low, f.high
    return {5: lo > 0, 4: lo >= 0, 7: lo < DETECT_OFF, 6: lo < DETECT_OFF,
            1: not (lo <= 0 and hi >= DETECT_OFF)}.get(f.ftype, True)


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
                       float(kv["lowvalue"]), float(kv.get("highvalue", kv["lowvalue"])), h.group(1).strip())
        except (KeyError, ValueError):
            continue
        if cls >= 0 and kv.get("enable", "1").strip() not in ("0", "0.0"):
            fs = bundles.setdefault(cls, [])        # 묶음은 남긴다 — Length 하나뿐인 묶음이면 '늘 통과' 묶음이다(OR)
            if f.feature == FEATURE_LENGTH and not condition_matters(f):
                continue                # 무의미한 Length 기준 — 슬라이더로 내놓지 않는다(판정은 같다)
            fs.append(f)
    return Zone(int(zid.group(1)), name.group(1) if name else "",
                {c: tuple(fs) for c, fs in bundles.items()}, _parse_detect(text, heads))


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
    mags = read_recipe_values(folder, "ScanMag")
    n = int(m.group(1))
    if mags.get(n) is not None and mags.get(n) == mags.get(1):
        # 같은 배율(예: 5x 와 5x)이면 같은 픽셀이다 — 1.538 vs 1.536 은 보정 차이라 바꾸면 오히려 틀렸다:
        # x5 Scan Area DarkArea 38 인데 Dark Defect 최솟값 38.099, 0.27% 늘린 38.10 이면 14개가 빠졌다(관측 2026-11-21).
        # 배율이 다를 때(5x → 20x)만 바꾼다.  배율을 모르면 픽셀 크기로만 판단한다.
        return 1.0, 1.0
    # 두 값은 같은 파일 짝(표기값끼리)에서 읽는다 — :func:`coords.pixel_size.recipe_pixel_pair`.  짝이 없을 때만 옛 방식.
    pair = recipe_pixel_pair(folder, n)
    ref, rec = pair if pair else (scan_pixel_size_xy(folder), scan_pixel_size_xy_recipe(folder, n))
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
    FeatureType 1(면적) × (레시피 px/폴더 px)² · 2(폭) · 3(길이) × 비 — 두 픽셀 크기는 표기값 짝(:func:`recipe_scale`).  RichSurface 값도 같은 폴더 px 라고 **가정**한다(미확인).
    장비에 적을 값은 같은 배율로 되돌린다(화면 :mod:`threshold_preview_dialog`)."""
    out: dict = {}
    for key, z in zones.items():
        lin, area = recipe_scale(folder, key[0])
        if lin == 1.0 and area == 1.0:
            out[key] = z
            continue
        k = {FEATURE_AREA: area, FEATURE_WIDTH: lin, FEATURE_LENGTH: lin}
        # 검출 단계 값도 같은 배율 — 레시피 자기 픽셀 기준이라고 **가정**한다(Zone 기준과 같은 ini · 같은 규약, 미확인).
        out[key] = Zone(z.zone_id, z.name, {c: tuple(
            Filter(f.feature, f.ftype, f.low * k.get(f.feature, 1.0), f.high * k.get(f.feature, 1.0), f.name) for f in fs)
            for c, fs in z.bundles.items()},
            {(pol, kind): v * (area if kind == "area" else lin) for (pol, kind), v in z.detect.items()})   # inf × k = inf
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


_GENERAL_RECIPE_NAME = re.compile(r"(?im)^\s*Recipe\s+Name[ \t]*=[ \t]*(\S.*?)\s*$")   # coords.recipe_name._GENERAL_NAME 과 같은 식
_SETUP_PATH = re.compile(r"(?im)^\s*SetupPath\s*=[ \t]*(.*?)\s*$")


def recipe_identity(folder: Path) -> tuple:
    r"""웨이퍼 폴더가 어느 레시피로 스캔됐나 → ``(SetupPath, ((N, 이름), …))``.  모르는 칸은 ``""`` · ``()``.

    ``Params_WaferInfo.ini SetupPath`` = ``c:\job\<Job>\<Setup>\Recipes\<레시피 1 이름>\`` (수집 526 폴더 전부 이 꼴,
    관측 2026-11-23), 다중 레시피면 ``RecipesInfo.ini`` 의 레시피 이름들.  Zone ini 내용이 같아도 Job · 레시피가 다르면
    다른 스캔이라 따로 튜닝한다(현택님 2026-11-23) — Zone 내용만으로 '같은 레시피' 라고 보지 않으려고 파일에서 읽는다.
    경로는 대소문자 · 끝 ``\`` 를 맞춘다(윈도 경로)."""
    m = _SETUP_PATH.search(read_ini_text(Path(folder) / "Params_WaferInfo.ini") or "")
    setup = m.group(1).strip().rstrip("\\/").lower() if m else ""
    # SetupPath 가 이미 단일 레시피의 이름을 담으므로, 여기서는 RecipesInfo.ini 이름만 쓴다(read_recipe_names 의 폴백은 뺀다)
    return setup, tuple(sorted(read_recipe_values(Path(folder), "Name").items()))


# 웨이퍼 · 장비마다 달라지는 광학 칸 — 레시피 차이가 아니다: Id(GUID), 초점 높이(Zwafer · FocusPosAboveChuck)
_OPTIC_SKIP = frozenset({"id", "zwafer", "focusposabovechuck"})
_ACTIVE_OPTICS = re.compile(r"(?i)^(?:Recipe(\d+)-)?ActiveScenarioOptics\.ini$")


def _ini_sections(text: str) -> dict:
    """INI 글 → ``{섹션 이름: {키(소문자): 값}}``."""
    out: dict = {}
    cur = None
    for line in text.splitlines():
        m = _SECTION.match(line)
        if m:
            cur = out.setdefault(m.group(1).strip(), {})
        elif cur is not None and "=" in line:
            k, v = line.split("=", 1)
            cur[k.strip().lower()] = v.strip()
    return out


def _optic_value(v: str):
    """숫자는 숫자로(``2`` = ``2.0``), 나머지는 글자 그대로."""
    try:
        return float(v)
    except ValueError:
        return v


def read_scan_optics(folder: Path) -> dict:
    r"""레시피별 **스캔 광학 값**(밝기 · 색 필터 · 게인 · 배율 …) → ``{Zone 폴더: ((키, 값), …)}``.

    ``ActiveScenarioOptics.ini`` 에서 ``ScenarioName=Scan2d`` 의 ``OpticsName`` 을 찾고, ``OpticPreset.ini`` 의 그 섹션을
    읽는다(현택님 2026-11-23: 'optic 밝기는 이 파일들로').  레시피 N(≥2)은 ``RecipeN-`` 이 붙은 두 파일 → ``RecipeN-Zones``.
    YDD-PIDS3 RE: Scan2d → ``[New]`` (Mag 10 · TDI · LightSrcRef_NominalGL 784.2 · CSI3, 관측).  웨이퍼마다 달라지는
    ``Id`` · ``Zwafer`` · ``FocusPosAboveChuck`` 는 뺀다.  파일이 없거나 Scan2d 가 없는 레시피(3D ``ScanCTS``)는 적지 않는다
    — 모르는 것은 비교하지 않는다."""
    folder = Path(folder)
    out: dict = {}
    try:
        names = [p.name for p in folder.iterdir()]
    except OSError:
        return out
    for name in names:
        m = _ACTIVE_OPTICS.match(name)
        if not m:
            continue
        n = int(m.group(1)) if m.group(1) else 1
        pre = f"Recipe{n}-" if m.group(1) else ""
        active = _ini_sections(read_ini_text(folder / name) or "")
        optics = [s.get("opticsname") for s in active.values() if (s.get("scenarioname") or "").lower() == "scan2d"]
        if len(optics) != 1 or not optics[0]:
            continue
        sect = _ini_sections(read_ini_text(folder / f"{pre}OpticPreset.ini") or "").get(optics[0])
        if sect:
            out[zone_dir_for(n)] = tuple(sorted((k, _optic_value(v)) for k, v in sect.items() if k not in _OPTIC_SKIP))
    return out


def same_recipe(a: tuple, b: tuple) -> bool:
    """:func:`recipe_identity` 둘이 같은 레시피인가 — 한쪽이라도 모르는 칸(빈 값)은 비교하지 않는다(증거가 아니다)."""
    return all(not x or not y or x == y for x, y in zip(a, b))


@lru_cache(maxsize=64)
def read_recipe_names(folder: Path) -> dict:
    r"""레시피 이름 → ``{N: 이름}``.  ``RecipesInfo.ini [Recipe-N] Name=`` 이 1순위(다중 레시피 스캔에만 있다).
    없으면(레시피 하나뿐인 폴더) ``{1: 이름}`` — ``Recipe.ini [General] Recipe Name=``, 그것도 없으면
    ``Params_WaferInfo.ini SetupPath`` 의 마지막 폴더 이름.  Zones 폴더가 레시피 1 이라 번호는 1.  모두 없으면 빈 dict."""
    names = read_recipe_values(folder, "Name")
    if names:
        return names
    m = _GENERAL_RECIPE_NAME.search(read_ini_text(Path(folder) / "Recipe.ini") or "")
    name = m.group(1).strip() if m else ""
    if not name:
        m = _SETUP_PATH.search(read_ini_text(Path(folder) / "Params_WaferInfo.ini") or "")
        parts = [t for t in re.split(r"[\\/]", m.group(1)) if t.strip()] if m else []
        name = parts[-1].strip() if parts else ""
    return {1: name} if name else {}


def read_recipe_values(folder: Path, key: str) -> dict:
    """``RecipesInfo.ini [Recipe-N] <key>=`` → ``{N: 값(글자)}``.  없으면 빈 dict."""
    text = read_ini_text(Path(folder) / "RecipesInfo.ini") or ""
    heads = list(_SECTION.finditer(text))
    out: dict = {}
    for i, h in enumerate(heads):
        m = re.fullmatch(r"(?i)recipe-(\d+)", h.group(1).strip())
        if not m:
            continue
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        nm = re.search(r"(?im)^\s*" + re.escape(key) + r"\s*=\s*(\S.*?)\s*$", body)
        if nm:
            out[int(m.group(1))] = nm.group(1)
    return out


_RT_LINE = re.compile(r"(?m)^\s*(-?\d+)\s*=\s*([^;\r\n]*);\s*(RT2D_[A-Z_]+)")


@lru_cache(maxsize=64)
def surface_result_types(folder: Path) -> dict:
    r"""``EquipmentClassifications.ini [SURFACE]`` → ``{비트 값: (이름, 'RT2D_…')}``.  없으면 빈 dict.

    Surface.flt 의 ``Type`` 은 이 비트다 — 어느 검출 단계가 Defect 를 잡았는지.  ``RT2D_EVENT`` ·
    ``RT2D_WL_EVENT``(Event Defect)만 Zone 의 Feature Filter 묶음을 거쳐 그 ``Classify`` 를 받고, 나머지
    (``RT2D_AREA_DARK`` 8 · ``RT2D_AREA_BRIGHT`` 16 · ``RT2D_LENGTH_*`` …)는 Zone ini ``[Surface]`` 의
    DarkArea · BrightArea 등 검출 단계가 잡아 ``ClassificationConvertTable.ini [Surface_CONVERT]`` 로 bin 을 받는다
    (TB500_RDL3 Multi 16장: Type 536870912 → 29 21,047개 · 8 → 29 680개(Contrast 전부 0) · 16 → 54 3개, 관측 2026-11-21).
    비트 값은 늘 이 파일에서 읽는다 — 코드에 박지 않는다."""
    text = read_ini_text(folder / "EquipmentClassifications.ini") or ""
    heads = list(_SECTION.finditer(text))
    sec = next((i for i, h in enumerate(heads) if h.group(1).strip().upper() == "SURFACE"), None)
    if sec is None:
        return {}
    body = text[heads[sec].end(): heads[sec + 1].start() if sec + 1 < len(heads) else len(text)]
    out: dict = {}
    for m in _RT_LINE.finditer(body):
        try:
            out[int(m.group(1))] = (m.group(2).strip(), m.group(3))
        except ValueError:
            continue
    return out


def event_mask(folder: Path):
    """Feature Filter 를 거치는 결과 비트(Event Defect)의 합.  파일이 없거나 Event 줄이 없으면 None — 그러면
    Type 으로 가르지 않는다(:func:`surface_result_types`)."""
    bits = [v for v, (_n, tok) in surface_result_types(folder).items() if v > 0 and tok in ("RT2D_EVENT", "RT2D_WL_EVENT")]
    return sum(bits) if bits else None


def stage_masks(folder: Path) -> dict:
    """``{'dark': 비트 합, 'bright': 비트 합}`` — 결과 이름(RT2D_…)에 DARK · BRIGHT 가 든 비트.  파일에서 읽고 코드에 박지 않는다.
    Event 비트는 뺀다.  없으면 빈 dict."""
    out: dict = {}
    for v, (_n, tok) in surface_result_types(folder).items():
        if v <= 0 or tok in ("RT2D_EVENT", "RT2D_WL_EVENT"):
            continue
        for pol in ("dark", "bright"):
            if pol.upper() in tok:
                out[pol] = out.get(pol, 0) | v
    return out


def detect_polarity(masks: dict, rtype) -> str | None:
    """결과 비트 → 'dark'/'bright'(둘 다 걸리거나 어느 쪽도 아니면 None — 그 Defect 는 안 건드린다)."""
    if rtype is None:
        return None
    hit = [p for p, m in masks.items() if int(rtype) & m]
    return hit[0] if len(hit) == 1 else None
