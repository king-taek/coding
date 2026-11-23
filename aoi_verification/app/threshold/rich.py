r"""``s_RichSurface.dat`` — 기준을 **내렸을 때** 새로 생기는 Defect 수를 센다.

Surface.flt 에는 지금 기준을 통과한 Defect 만 있어 내리면 무엇이 생길지 모른다.  RichSurface 는
검출된 후보 **전부**를 Zone 기준을 걸기 전 값(면적 · 폭 · Contrast 평균)과 함께 적는다.
TB500 YDD-No Image 20234838EWG1(레시피 1 · Zone 1 PI_Opening): 후보 1,091,821 개에 Zone ini 기준을
걸면 장비 Defect 9,385 / 9,385 가 오차 0 으로 나왔다(관측 2026-11-15).  열 이름 대응:
``area`` = Area(px²) · ``Breadth`` = Width(px) · ``AbsDeltaFromRef_Average`` = Contrast Average.

경계(사실 그대로 둔다):
  · 검출 단계(Zone ini ``[Surface]`` Low_Delta 등)가 처음부터 못 잡은 후보는 이 파일에도 없다 —
    그 아래로 내린 값은 이 파일로 알 수 없다.  그래서 화면의 하한은 후보 값의 최솟값이다.
  · 장비가 ``Recipe.ini RichEvent_SaveInScanResults=1`` 일 때만 저장한다(관측: TB500 No Image 하나).
    파일이 없으면 내리기는 없다 — 다른 값으로 메우지 않는다.
  · 후보를 Defect 수로 바로 센다.  Surface.flt Defect 수(같은 Zone 8,982)와 RichSurface 통과 수(9,385)가
    다르다(관측, 이유 미확인) — '새로 생김' 은 RichSurface 안에서만 비교한다(통과(새 값) − 통과(레시피 값)).

열 위치는 늘 옆의 ``.md`` 사이드카에서 읽는다(장비마다 다르다).  전 구간 fail-safe — 못 읽으면 빈 결과.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ..coords import surface_flt as _flt
from .rules import _SAME_REL, FEATURE_AREA, FEATURE_CONTRAST, FEATURE_WIDTH, Filter, Zone, zone_dir_for

__all__ = ["RICH_FILE", "load_rich", "supports", "passes", "count_new"]

_LOG = logging.getLogger("aoi.threshold")

RICH_FILE = "s_RichSurface.dat"
CHUNK = 100_000                     # 한 번에 읽는 후보 수(456 B × 10만 ≈ 46 MB)

_FEATURE_COL = {FEATURE_AREA: "area", FEATURE_WIDTH: "Breadth", FEATURE_CONTRAST: "AbsDeltaFromRef_Average"}
_NEED = ("zone", "recipe", *_FEATURE_COL.values())


def _dtype(md: Path):
    """사이드카 → numpy 레코드 형식.  필요한 열이 하나라도 없으면 None."""
    try:
        text = md.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    m = _flt._RE_SIZE.search(text)
    if not m:
        return None
    size = int(m.group(1))
    cols = {}
    for fm in _flt._RE_FIELD.finditer(text):
        a = dict(_flt._RE_ATTR.findall(fm.group(1)))
        name = a.get("Name", "")
        fmt = _flt._VARTYPE_FMT.get(int(a["Vartype"])) if a.get("Vartype", "").isdigit() else None
        if name in _NEED and fmt and a.get("Offset", "").isdigit():
            cols[name] = (int(a["Offset"]), np.dtype("<" + fmt))
    if set(_NEED) - set(cols) or any(off + dt.itemsize > size for off, dt in cols.values()):
        return None
    names = list(cols)
    return np.dtype({"names": names, "formats": [cols[n][1] for n in names],
                     "offsets": [cols[n][0] for n in names], "itemsize": size})


def load_rich(folder: Path, progress=lambda done, total: None, stop=lambda: False) -> dict:
    """웨이퍼 폴더 → ``{(zone_dir, zone): {FeatureType: float32 배열(px)}}``.  파일이 없거나 못 읽으면 ``{}``."""
    dat = Path(folder) / RICH_FILE
    dt = _dtype(dat.with_name(dat.name + ".md"))
    if dt is None or not dat.is_file():
        return {}
    try:
        size = dat.stat().st_size
        total = size // dt.itemsize
        if size % dt.itemsize:
            _LOG.warning("RichSurface 크기가 레코드의 배수가 아님(%d / %d) — 끝 조각은 버림: %s",
                         size, dt.itemsize, dat)
        parts: dict = {}
        done = 0
        with dat.open("rb") as fh:
            while done < total:
                if stop():
                    return {}
                rec = np.fromfile(fh, dtype=dt, count=min(CHUNK, total - done))
                if not len(rec):
                    break
                done += len(rec)
                code = rec["zone"].astype(np.int64) * 65536 + rec["recipe"].astype(np.int64)
                for c in np.unique(code):
                    zone, recipe = int(c) // 65536, int(c) % 65536
                    sel = code == c
                    lst = parts.setdefault((zone_dir_for(recipe), zone), {f: [] for f in _FEATURE_COL})
                    for f, col in _FEATURE_COL.items():
                        lst[f].append(rec[col][sel].astype(np.float32))
                progress(done, total)
    except (OSError, ValueError) as exc:
        _LOG.warning("RichSurface 못 읽음: %s  %s", dat, exc)
        return {}
    return {k: {f: np.concatenate(v) for f, v in cols.items()} for k, cols in parts.items()}


def supports(zone: Zone | None) -> bool:
    """이 Zone 의 기준을 전부 RichSurface 열로 계산할 수 있나(Polarity 같은 다른 기준이 있으면 못 한다)."""
    return zone is not None and bool(zone.bundles) and all(
        f.feature in _FEATURE_COL for fs in zone.bundles.values() for f in fs)


def _same(v: np.ndarray, x: float) -> np.ndarray:
    """:func:`rules.same_value` 의 배열판 — 레시피 N 기준을 옮긴 부동소수 끝자리만 같은 값으로 본다."""
    if not np.isfinite(x):
        return v == x
    return (v == x) | (np.abs(v - x) <= _SAME_REL * np.maximum(np.abs(v), abs(x)))


def _mask(f: Filter, v: np.ndarray, low: float) -> np.ndarray:
    hi = f.high
    lo_eq, hi_eq = _same(v, low), _same(v, hi)
    inside = ((low <= v) | lo_eq) & ((v <= hi) | hi_eq)
    return {1: inside, 2: ~inside, 3: lo_eq, 4: v > low,
            5: (v >= low) | lo_eq, 6: v < low, 7: (v <= low) | lo_eq, 8: v != low}.get(f.ftype, np.ones(v.shape, bool))


def passes(cand: dict, zone: Zone, key: tuple, overrides: dict) -> np.ndarray:
    """후보마다 Zone 기준 통과 여부 — 묶음 안 AND, 묶음끼리 OR(:mod:`.rules`).  ``overrides`` 는 미리보기와 같은 키."""
    n = len(next(iter(cand.values()))) if cand else 0
    out = np.zeros(n, bool)
    for cls, filters in zone.bundles.items():
        m = np.ones(n, bool)
        for f in filters:
            m &= _mask(f, cand[f.feature], overrides.get((*key, cls, f.feature), f.low))
        out |= m
    return out


def count_new(cand: dict, zone: Zone, key: tuple, overrides: dict, base=None) -> int:
    """레시피 값으로는 안 잡히고 바꾼 값으로 잡히는 후보 수.  ``base`` = 레시피 통과 배열(캐시)."""
    if base is None:
        base = passes(cand, zone, key, {})
    return int(np.count_nonzero(passes(cand, zone, key, overrides) & ~base))
