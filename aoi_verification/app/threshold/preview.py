"""Surface.flt Defect 를 읽어, Zone · bin 기준값을 바꿨을 때 몇 개가 남는지 센다.

현택님 결정(2026-11-13 ~ 11-16):
  · bin = 첫 분류(``orgReclassify``, 0 이면 ``reclassify``) — Zone ini 묶음의 ``Classify`` 와 같은 번호다.
  · 2차 분류(사람이 검토로 바꾼 번호, Accept 포함)는 이 기능에서 의미가 없다 — **전부 Defect 로 센다**.
  · **첫 분류**의 종류가 'Good' 인 것(EDC Good)은 세지 않는다.  Accept(0)도 종류는 Good 이지만 사람이
    검토로 바꾼 지금 분류라 첫 분류는 Defect 다(AOI-27 EDC Good 65 = 첫 분류 20 · AVAGO Accept 151 = 첫 분류 28/29, 관측).
  · Contrast 가 Zone 전체에서 0 이면 그 Zone 은 Contrast 를 계산할 수 없다(:func:`contrast_known`) —
    장비가 Contrast 기준이 없는 Zone 에는 값을 안 적는다(관측 2026-11-16, 웨이퍼 5장).

값은 Zone ini 와 같은 **픽셀 단위**로 비교한다(면적 px² · 폭 px, :mod:`.rules`).
기준을 **올리는** 쪽만 정확하다 — 파일에는 지금 기준을 통과한 Defect 만 있어서, 내리면 새로 생길
Defect 를 모른다.  여기서는 파일 안 Defect 만 센다(내리기는 RichSurface 단계에서 따로).
"""

from __future__ import annotations

import logging
import struct

import numpy as np
from dataclasses import dataclass
from pathlib import Path

from ..coords import surface_flt as _flt
from .rules import FEATURE_AREA, FEATURE_CONTRAST, FEATURE_WIDTH, read_classes, read_zones, zone_dir_for

__all__ = ["Defect", "load_all", "load_defects", "match_photos", "contrast_known", "why_dropped",
           "is_kept", "recipe_misses", "count_by_bin", "recommend"]

_LOG = logging.getLogger("aoi.threshold")

# 사이드카 이름 → Defect 칸.  recipe 는 없어도 된다(옛 기록).
_NEED = {"reclassify": "cls", "orgReclassify": "org", "zone": "zone", "recipe": "recipe",
         "area": "area", "BlobBreadth": "width", "BlobFeretMax": "length", "Contrast": "contrast",
         "ActualX": "x", "ActualY": "y"}


@dataclass(frozen=True)
class Defect:
    index: int          # Surface.flt 안 순서
    zone_dir: str       # "Zones" / "RecipeN-Zones"
    zone: int
    bin: int            # 첫 분류
    cls: int            # 지금 분류(사람이 바꾸면 음수)
    area: float         # px²
    width: float        # px (BlobBreadth)
    length: float       # px (BlobFeretMax)
    contrast: float
    x: float            # µm (ActualX)
    y: float
    wafer: int = 0      # 여러 웨이퍼를 합칠 때 몇 번째 웨이퍼인지 — (wafer, index) 가 Defect 의 키


def _layout(flt: Path):
    """``Surface.flt.md`` → ``(레코드 크기, {Defect 칸: (offset, fmt)})``.  못 읽으면 None.

    레코드 배치는 장비마다 달라 늘 사이드카에서 읽는다(:mod:`coords.surface_flt` 와 같은 규칙)."""
    md = flt.with_name(flt.name + ".md")
    try:
        text = md.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    m = _flt._RE_SIZE.search(text)
    if not m:
        return None
    fields = {}
    for fm in _flt._RE_FIELD.finditer(text):
        a = dict(_flt._RE_ATTR.findall(fm.group(1)))
        key = _NEED.get(a.get("Name", ""))
        fmt = _flt._VARTYPE_FMT.get(int(a["Vartype"])) if a.get("Vartype", "").isdigit() else None
        if key and fmt and a.get("Offset", "").isdigit():
            fields[key] = (int(a["Offset"]), fmt)
    size = int(m.group(1))
    if set(_NEED.values()) - {"recipe"} - set(fields) or any(
            off + struct.calcsize("<" + f) > size for off, f in fields.values()):
        return None
    return size, fields


def load_all(folder: Path) -> tuple[tuple[Defect, ...], tuple[Defect, ...], str]:
    """웨이퍼 폴더 → ``(Defect 들, 첫 분류가 Good 인 것(EDC Good) 들, 문제 설명)``.  절대 raise 안 함.

    Good 쪽은 세지 않고 따로 보여 주기만 한다(현택님 2026-11-15 — 장비 리뷰 화면에는 안 보이지만
    사진은 웨이퍼 폴더에 남아 있다)."""
    try:
        flt = _flt._find_flt(folder)
        if flt is None:
            return (), (), "Surface.flt 없음"
        lay = _layout(flt)
        if lay is None:
            return (), (), "Surface.flt.md 없음 또는 못 읽음"
        size, fields = lay
        data = flt.read_bytes()
        classes = read_classes(folder)
    except OSError as e:
        _LOG.warning("Surface.flt 못 읽음: %s (%s)", folder, e)
        return (), (), f"못 읽음: {e}"
    good = {n for n, c in classes.items() if c.good}
    # 한 번에 읽는다(numpy) — 레코드마다 struct 로 풀면 Defect 7만 개에 0.5초가 걸렸다(측정 2026-11-16).
    names = list(fields)
    dt = np.dtype({"names": names, "formats": ["<" + fields[k][1] for k in names],
                   "offsets": [fields[k][0] for k in names], "itemsize": size})
    arr = np.frombuffer(data, dtype=dt, count=len(data) // size)
    col = {k: arr[k].tolist() for k in names}
    recipe = col.get("recipe") or [None] * len(arr)
    kept, goods = [], []
    for i, (cls, org, zone, rec, area, width, length, contrast, x, y) in enumerate(zip(
            col["cls"], col["org"], col["zone"], recipe, col["area"], col["width"], col["length"],
            col["contrast"], col["x"], col["y"])):
        first = org or cls
        d = Defect(i, zone_dir_for(rec), int(zone), int(first), int(cls), float(area), float(width),
                   float(length), float(contrast), float(x), float(y))
        # 첫 분류가 Good(EDC Good: reclassify 20 · org 0).  Accept 는 지금 분류만 0 이라 Defect 로 남는다.
        (goods if abs(first) in good else kept).append(d)
    return tuple(kept), tuple(goods), ""


def load_defects(folder: Path) -> tuple[tuple[Defect, ...], str]:
    """웨이퍼 폴더 → ``(Defect 들, 문제 설명)``.  문제가 없으면 설명은 빈 문자열.  절대 raise 안 함."""
    kept, _good, note = load_all(folder)
    return kept, note


def match_photos(defects, photos, xy_of, tol: float) -> dict:
    """사진 → Defect 짝짓기: ``{Defect.index: 사진 경로}``.

    ``xy_of(사진)`` 은 그 사진의 절대 좌표(µm) 또는 None(:func:`coords.abs_coord.absolute_xy`).
    거리 ``tol`` 안에서 가장 가까운 Defect 에 붙인다 — 사진 정보 화면(:mod:`coords.geometry`)과 같은
    규칙이다.  한 Defect 에 사진이 여럿이면 처음 것을 쓴다."""
    import bisect
    import math
    order = sorted(range(len(defects)), key=lambda i: defects[i].x)
    xs = [defects[i].x for i in order]
    out: dict = {}
    for photo in photos:
        xy = xy_of(photo)
        if xy is None:
            continue
        x, y = xy
        best, best_d = None, tol
        for j in order[bisect.bisect_left(xs, x - tol): bisect.bisect_right(xs, x + tol)]:
            d = math.hypot(defects[j].x - x, defects[j].y - y)
            if d <= best_d:
                best, best_d = defects[j], d
        if best is not None:
            out.setdefault(best.index, photo)
    return out


def contrast_known(defects, zone_dir: str, zone: int) -> bool:
    """그 Zone 에 Contrast 가 0 아닌 Defect 가 하나라도 있으면 참."""
    return any(d.contrast != 0 for d in defects if d.zone_dir == zone_dir and d.zone == zone)


def _value(d: Defect, feature: int):
    return {FEATURE_AREA: d.area, FEATURE_WIDTH: d.width, FEATURE_CONTRAST: d.contrast}.get(feature)


def why_dropped(d: Defect, zones: dict, overrides: dict | None = None):
    """빠지면 ``(Classify, FeatureType)`` — 이 Defect 를 떨어뜨린 기준 하나, 남으면 None.

    묶음 하나라도 통과하면 남는다.  ``overrides`` = ``{(zone_dir, zone, Classify, FeatureType): 새 LowValue}``.
    여러 묶음에서 떨어졌으면 자기 첫 분류 묶음의 기준을 알려 준다(없으면 처음 묶음).

    파일에 값이 없는 기준(Polarity · Abs Contrast)과 **Contrast 가 0 인 Defect 의 Contrast** 는 통과로 본다 —
    0 은 '안 잼' 이다: Contrast 기준이 있는 Zone 에서도 다른 검출 방식이 잡은 Defect 는 0 이고, 레시피 값으로
    검산하면 그 Defect 만 빠졌다(TB500 PI_Opening 5개 · AOI-27 Scan Area 1개, 관측 2026-11-16)."""
    z = zones.get((d.zone_dir, d.zone))
    if z is None or not z.bundles:
        return None
    overrides = overrides or {}
    first = None
    for cls, filters in z.bundles.items():
        bad = None
        for f in filters:
            v = _value(d, f.feature)
            if v is None or (f.feature == FEATURE_CONTRAST and v == 0):
                continue
            low = overrides.get((d.zone_dir, d.zone, cls, f.feature), f.low)
            if not type(f)(f.feature, f.ftype, low, max(low, f.high)).passes(v):
                bad = (cls, f.feature)
                break
        if bad is None:
            return None
        if first is None or cls == d.bin:
            first = bad
    return first


def is_kept(d: Defect, zones: dict, overrides: dict | None = None) -> bool:
    """묶음 하나라도 통과하면 남는다(:func:`why_dropped`)."""
    return why_dropped(d, zones, overrides) is None


def recipe_misses(defects, zones: dict) -> list:
    """레시피 값 그대로인데 빠지는 Defect — 열 때 검산(0 이 아니면 그 Zone 계산을 믿을 수 없다)."""
    return [d for d in defects if not is_kept(d, zones)]


def count_by_bin(defects, zones: dict, overrides: dict | None = None) -> dict:
    """``{(zone_dir, zone, bin): (전체, 남는 수)}``."""
    out: dict = {}
    for d in defects:
        k = (d.zone_dir, d.zone, d.bin)
        total, kept = out.get(k, (0, 0))
        out[k] = (total + 1, kept + is_kept(d, zones, overrides))
    return out


# 자동 추천이 올릴 수 있는 기준 — 하한이 있는 것(1 범위 안 · 4 > · 5 ≥).  나머지는 레시피 그대로.
_RAISABLE = (1, 4, 5)


def _passes_bundle(d: Defect, z, cls: int) -> bool:
    return why_dropped(d, {(d.zone_dir, d.zone): type(z)(z.zone_id, z.name, {cls: z.bundles[cls]})}) is None


def recommend(picked, zones: dict, snap=None) -> tuple[dict, list]:
    """꼭 잡아야 할 Defect(``picked``)를 전부 남기면서 나머지를 많이 빼는 하한 → ``(overrides, 못 지킨 Defect)``.

    현택님 요청(2026-11-16, 자동 모드): 사용자가 고른 Defect 는 하나도 빠지면 안 되고, 그 안에서
    Overkill 을 줄인다.  해석(PR 에 적음):
      · 고른 Defect 마다 지킬 묶음을 정한다 — **자기 첫 분류(bin) 묶음을 레시피 값에서 통과하면 그 묶음**
        (장비가 그 묶음으로 잡았다), 아니면 레시피 값에서 통과하는 **모든** 묶음(어느 것이 잡았는지 모른다).
      · 묶음 안은 하한끼리 AND 라, 각 기준을 따로 '지킬 Defect 값의 최솟값' 까지 올리면 **그 배정에서**
        가장 많이 빠진다(하한을 올리면 빠지는 수는 늘기만 한다).  묶음끼리는 OR 이라 고른 것이 없는 묶음을
        통과하는 Defect 는 남는다 — 전체 최적은 아니고, 화면은 '새로 빠짐' 숫자를 그대로 보여 준다.
      · 고른 Defect 가 없는 Zone · 묶음은 건드리지 않는다(돌려주는 키에 없다).  레시피보다 내리지 않는다.
      · Contrast 0 은 '안 잼'(:func:`why_dropped`) 이라 최솟값에서 뺀다 — 전부 0 이면 Contrast 는 그대로.
    ``snap(feature, 값 px, 엄격) → px`` 는 화면 표시 자리로 **내림**한 값을 준다 — 화면 숫자를 그대로 장비에
    적어도 고른 Defect 가 빠지지 않게.  없으면 값 그대로(엄격 '>' 이면 바로 아래 실수).
    내림·환산 오차로 고른 것이 빠지면 그 Defect 를 지키는 묶음의 추천을 거둔다(레시피 값으로).
    끝까지 못 지킨 것(레시피 값에서도 이미 빠지는 Defect)을 돌려준다."""
    import math
    if snap is None:
        def snap(_feature, v, strict):
            return math.nextafter(v, -math.inf) if strict else v
    guard: dict = {}                    # (zone_dir, zone, Classify) → 지킬 Defect 들
    owners_of: dict = {}
    for d in picked:
        z = zones.get((d.zone_dir, d.zone))
        if z is None or not z.bundles:
            continue
        own = d.bin in z.bundles and _passes_bundle(d, z, d.bin)
        owners = [d.bin] if own else [c for c in z.bundles if _passes_bundle(d, z, c)]
        owners_of[id(d)] = [(d.zone_dir, d.zone, c) for c in owners]
        for c in owners:
            guard.setdefault((d.zone_dir, d.zone, c), []).append(d)
    out: dict = {}
    for (zdir, zid, cls), ds in guard.items():
        for f in zones[(zdir, zid)].bundles[cls]:
            if f.ftype not in _RAISABLE:
                continue
            vals = [v for v in (_value(d, f.feature) for d in ds)
                    if v is not None and not (f.feature == FEATURE_CONTRAST and v == 0)]
            if not vals:
                continue
            low = snap(f.feature, min(vals), f.ftype == 4)
            if low > f.low:
                out[(zdir, zid, cls, f.feature)] = low
    for d in picked:                    # 내림 오차 방어 — 지키지 못한 Defect 의 묶음은 레시피 값으로
        if not is_kept(d, zones, out):
            for b in owners_of.get(id(d), ()):
                for k in [k for k in out if k[:3] == b]:
                    del out[k]
    return out, [d for d in picked if not is_kept(d, zones, out)]
