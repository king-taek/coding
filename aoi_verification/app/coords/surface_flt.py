"""Surface.flt 바이너리 파서 → 결함 raw geometry 레코드.

각 결과 폴더의 ``Surface.flt`` 는 고정 크기 레코드의 연속이다 — 크기는 장비마다 다르다(아래 ★).
한 레코드에서 절대 좌표(ActualX/ActualY)와 geometry(area/BlobBreadth/BlobFeretMax/
Contrast)를 읽어 :class:`geometry.resolve` 가 이미지 좌표와 nearest-match 하는 데 쓴다.

──────────────────────────────────────────────────────────────────────────
스키마(바이트 오프셋)는 TB500 실측 예시 84건(각 예시에 매칭 record 의 raw 152byte
HEX + 디코딩 정답값 동봉)을 brute-force 해 확정했다.  레이아웃은 ``_FIELDS`` 한 곳에
격리한다 — 장비/포맷이 바뀌면 여기만 고치면 된다.

확정 결과(레코드 시작 0, 152byte 보폭, 헤더 없음):
    float64(``<d``): actual_x=24, actual_y=32, area=80, blob_breadth=88,
                     blob_feret_max=104, contrast=136
    uint8 (``<B``): zone=61, recipe=62
  · actual_x 는 offset 8 에도 거의 같은 값이 있으나 84건 중 1건에서 어긋나므로 24 가
    정본(문서 "Surface ActualX" 와 84/84 일치).  contrast 는 비0 값들이 offset 136 을
    유일하게 고정.  zone/recipe 는 82건 전수 일치(zone 1=PI Opening, 63=Scan Area 등).
    record_index×152 == byte_offset (84건 불일치 0) 로 보폭/헤더 확인.
  · 모든 오프셋이 채워져 있으면 _SCHEMA_READY=True → 기능 활성.  (혹시 None 으로 비우면
    파서가 빈 결과를 돌려주는 안전 폴백은 유지.)
──────────────────────────────────────────────────────────────────────────

★ **레코드 크기는 장비마다 다르다** — 152 만 있는 게 아니다(수집 자료 54 파일 전수, 관측
2026-11-14): 152(41 파일) · 136(8 파일, AOI-5·10) · 120(5 파일, AOI-1 구형 EAGLE).  152 로 고정해
읽으면 120·136 파일에서 **다른 Defect 의 수치**가 붙었다(앱이 수치를 보인 61 장 중 50 장).  장비 모델명으로는 못 가른다(EAGLETP_106821 = 152).  그래서:
  1. 옆에 ``Surface.flt.md`` 사이드카가 있으면 그 ``RecordSize`` · ``Field`` 오프셋을 그대로 쓴다.
  2. 없으면 알려진 레이아웃(``_FIELDS`` + ``_OLD_LAYOUTS``) 중 **파일 길이가 나눠떨어지고 모든
     레코드가 말이 되는 값**(유한 · 좌표 ±1e6 µm · 크기 ≥ 0)인 것이 **딱 하나**일 때만 쓴다.
     둘 이상이거나 없으면 **빈 결과**(측정정보 없음) — 틀린 수치보다 빈칸이 낫다.
     54 파일 전수에서 이 규칙은 54/54 정답을 골랐다(25,080 B 는 152·120 둘 다 나눠지지만
     120 으로 읽으면 209 중 65 레코드가 말이 안 돼 152 로 정해진다).
  3. ``recipe`` 칸이 없는 레이아웃(120, 사이드카 없는 136)은 recipe 를 ``None`` 으로 둔다 —
     136 의 byte 10 은 사이드카에 recipe 가 있는 파일에서만 recipe 이고 없는 파일에선 쓰레기다.
  · 120·136 의 Contrast 오프셋은 사이드카 값이다(파일 등급) — 실물 13 파일 값이 전부 0 이라
    값으로는 검산하지 못했다.  회귀 가드: ``test_surface_flt_layouts.py``(실물 바이트).

기존 coords 파서(camtek_ini/kla_info)와 동일한 관습: 폴더 단위 ``lru_cache``,
fail-safe(파일 없음/손상 시 빈 결과, **절대 raise 안 함**).
"""

from __future__ import annotations

import logging
import math
import re
import struct
from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from typing import Optional

__all__ = ["load_folder", "has_flt", "RawRecord"]

_LOG = logging.getLogger("aoi.coords")

# ── 스키마(TB500 실측 84건 brute-force 로 확정 — 위 docstring 참조) ─────────
_BYTE_ORDER = "<"        # little-endian
_RECORD_SIZE = 152       # 한 레코드 바이트 수
_HEADER_BYTES = 0        # 레코드 0 앞 헤더 바이트 수 (없음)

# field → (record 내부 byte offset, struct 포맷 문자).  offset 이 None 이면 미정.
_FIELDS: dict[str, tuple[Optional[int], str]] = {
    "actual_x":       (24,  "d"),
    "actual_y":       (32,  "d"),
    "area":           (80,  "d"),
    "blob_breadth":   (88,  "d"),
    "blob_feret_max": (104, "d"),
    "contrast":       (136, "d"),
    "zone":           (61,  "B"),
    "recipe":         (62,  "B"),
}

# 구형 장비 레이아웃(사이드카 ``Surface.flt.md`` 실물에서 옮김 — 파일 등급).
# recipe 가 None 이면 그 레이아웃에는 recipe 칸이 없다(값 None).
_OLD_LAYOUTS: dict[int, dict[str, tuple[Optional[int], str]]] = {
    136: {  # AOI-5 EAGLET_106102 · AOI-10 EAGLETP_107168
        "actual_x": (32, "d"), "actual_y": (40, "d"), "area": (72, "d"),
        "blob_breadth": (80, "d"), "blob_feret_max": (96, "d"),
        "contrast": (128, "d"), "zone": (9, "B"), "recipe": (None, "B"),
    },
    120: {  # AOI-1 EAGLE_106134
        "actual_x": (32, "d"), "actual_y": (40, "d"), "area": (72, "d"),
        "blob_breadth": (80, "d"), "blob_feret_max": (96, "d"),
        "contrast": (112, "d"), "zone": (8, "B"), "recipe": (None, "B"),
    },
}

# 사이드카 필드 이름 → RawRecord 필드.  recipe 만 없어도 된다.
_SIDECAR_NAMES = {
    "ActualX": "actual_x", "ActualY": "actual_y", "area": "area",
    "BlobBreadth": "blob_breadth", "BlobFeretMax": "blob_feret_max",
    "Contrast": "contrast", "zone": "zone", "recipe": "recipe",
}
# 사이드카 Vartype(VARIANT 형) → struct 포맷.  실물에 나온 것: 2·3·4·5·17·22.
_VARTYPE_FMT = {2: "h", 3: "i", 4: "f", 5: "d", 16: "b", 17: "B", 18: "H",
                19: "I", 22: "i", 23: "I"}
_RE_SIZE = re.compile(r'<RecordSize\b[^>]*\bSize="(\d+)"')
_RE_FIELD = re.compile(r"<Field\b([^>]*)>")
_RE_ATTR = re.compile(r'(\w+)="([^"]*)"')

# 모든 오프셋이 채워졌는지 — 하나라도 None 이면 파서는 비활성(빈 결과).
_SCHEMA_READY: bool = all(off is not None for off, _ in _FIELDS.values())

# Surface.flt 파일명 후보 — 대소문자 두 가지.
_FLT_CANDIDATES = ("Surface.flt", "surface.flt")

RawRecord = namedtuple(
    "RawRecord",
    ["actual_x", "actual_y", "area", "blob_breadth", "blob_feret_max", "contrast",
     "zone", "recipe"],
)


def _find_flt(folder: Path) -> Optional[Path]:
    for name in _FLT_CANDIDATES:
        p = folder / name
        if p.exists():
            return p
    return None


def has_flt(folder: Path) -> bool:
    """폴더에 Surface.flt 가 존재하는지(파싱 가능 여부와 무관).

    '측정정보 미지원 자재'(파일 자체가 없음) 와 '측정정보 없음'(파일은 있으나 매칭
    실패) 을 구분하기 위해 geometry.resolve 가 사용한다.
    """
    try:
        return _find_flt(folder) is not None
    except Exception:
        return False


def _unpack(buf: bytes, base: int, offset: int, fmt: str) -> Optional[float]:
    try:
        return struct.unpack_from(_BYTE_ORDER + fmt, buf, base + offset)[0]
    except struct.error:
        return None


def _record_struct(size: int, fields: dict):
    """레이아웃을 레코드 **한 개를 한 번에** 푸는 ``struct.Struct`` 로 만든다.

    반환 ``(Struct, RawRecord 필드 순서로 값을 고르는 인덱스 — 칸이 없으면 None)``.
    필드가 겹치거나 레코드 밖으로 나가면 ``None`` — 그때는 필드별로 푸는 경로를 쓴다."""
    parts: list[str] = []
    names: list[str] = []
    pos = 0
    present = [(n, f) for n, f in fields.items() if f[0] is not None]
    for name, (off, fmt) in sorted(present, key=lambda kv: kv[1][0]):
        if off < pos:
            return None
        if off > pos:
            parts.append(f"{off - pos}x")
        parts.append(fmt)
        names.append(name)
        pos = off + struct.calcsize(_BYTE_ORDER + fmt)
    if pos > size:
        return None
    if size > pos:
        parts.append(f"{size - pos}x")
    st = struct.Struct(_BYTE_ORDER + "".join(parts))
    return st, [names.index(f) if f in names else None for f in RawRecord._fields]


def _decode(body: bytes, size: int, fields: dict) -> tuple[RawRecord, ...]:
    """★ 레코드마다 ``struct.iter_unpack`` 한 번이다.  예전에는 필드 8개를 따로
    ``unpack_from`` 하고 dict 를 만들어 넘겼는데, Surface.flt 는 **웨이퍼 전체 결함**을
    담아 레코드가 수만 개일 수 있다(Defect 추출 저장이 느린 원인 후보 — 추정이며
    실측 전이다).  값은 같다
    (같은 바이트 순서·포맷, 꼬리의 불완전 레코드는 버린다) — 회귀 가드
    ``test_surface_flt.test_fast_parse_matches_field_by_field``."""
    fast = _record_struct(size, fields)
    if fast is None:
        return _parse_flt_by_field(body, size, fields)
    st, order = fast
    body = body[:len(body) // size * size]
    make = RawRecord._make
    return tuple(make([None if i is None else v[i] for i in order])
                 for v in st.iter_unpack(body))


def _parse_flt_by_field(data: bytes, size: int, fields: dict) -> tuple[RawRecord, ...]:
    """필드별로 푸는 예전 경로 — 레이아웃이 한 번에 풀 수 없는 모양일 때만."""
    out: list[RawRecord] = []
    pos = 0
    n = len(data)
    while pos + size <= n:
        vals: dict[str, Optional[float]] = {}
        ok = True
        for name in RawRecord._fields:
            offset, fmt = fields[name]
            if offset is None:
                vals[name] = None
                continue
            v = _unpack(data, pos, offset, fmt)
            if v is None:
                ok = False
                break
            vals[name] = v
        if ok:
            out.append(RawRecord(**vals))  # type: ignore[arg-type]
        pos += size
    return tuple(out)


def _sidecar_layout(flt: Path) -> Optional[tuple[int, dict]]:
    """``Surface.flt.md``(장비가 남긴 레코드 설명) → ``(레코드 크기, 레이아웃)``.

    없거나 필요한 칸(recipe 말고 전부)이 빠졌거나 형을 모르면 ``None``."""
    md = flt.with_name(flt.name + ".md")
    if not md.exists():
        return None
    text = md.read_bytes().decode("utf-8", errors="replace")
    m = _RE_SIZE.search(text)
    if not m or int(m.group(1)) <= 0:
        return None
    size = int(m.group(1))
    fields: dict[str, tuple[Optional[int], str]] = {}
    for fm in _RE_FIELD.finditer(text):
        a = dict(_RE_ATTR.findall(fm.group(1)))
        key = _SIDECAR_NAMES.get(a.get("Name", ""))
        if key is None:
            continue
        fmt = _VARTYPE_FMT.get(int(a["Vartype"])) if a.get("Vartype", "").isdigit() else None
        if fmt is None or not a.get("Offset", "").isdigit():
            return None
        fields[key] = (int(a["Offset"]), fmt)
    fields.setdefault("recipe", (None, "B"))
    if set(fields) != set(RawRecord._fields):
        return None
    if any(off is not None and off + struct.calcsize(_BYTE_ORDER + fmt) > size
           for off, fmt in fields.values()):
        return None
    return size, fields


def _plausible(recs: tuple[RawRecord, ...]) -> bool:
    """모든 레코드가 말이 되는 값인지 — 틀린 레이아웃으로 읽으면 쓰레기 수가 섞인다."""
    for r in recs:
        vals = (r.actual_x, r.actual_y, r.area, r.blob_breadth, r.blob_feret_max,
                r.contrast)
        if not all(math.isfinite(v) for v in vals):
            return False
        if not (-1e6 < r.actual_x < 1e6 and -1e6 < r.actual_y < 1e6):
            return False
        if not (0 <= r.area < 1e9 and 0 <= r.blob_breadth < 1e6
                and 0 <= r.blob_feret_max < 1e6):
            return False
    return True


def _parse_flt(path: Path) -> tuple[RawRecord, ...]:
    """레이아웃을 정해(사이드카 → 길이·값 검사) 레코드를 푼다.  못 정하면 빈 튜플."""
    data = path.read_bytes()
    body = data[_HEADER_BYTES:]
    side = _sidecar_layout(path)
    if side is not None:
        return _decode(body, *side)
    if path.with_name(path.name + ".md").exists():
        _LOG.warning("Surface.flt.md 를 읽지 못해 길이·값 검사로 레코드 크기를 정함: %s", path)
    layouts = {_RECORD_SIZE: _FIELDS}
    for size, fields in _OLD_LAYOUTS.items():
        layouts.setdefault(size, fields)
    fits = []
    for size, fields in layouts.items():
        if not body or len(body) % size:
            continue
        recs = _decode(body, size, fields)
        if _plausible(recs):
            fits.append((size, recs))
    if len(fits) == 1:
        return fits[0][1]
    if body:
        _LOG.warning("Surface.flt 레코드 크기를 정하지 못함(후보 %s, %d 바이트) — "
                     "측정정보 없음으로 처리: %s", [f[0] for f in fits], len(body), path)
    return ()


@lru_cache(maxsize=256)
def load_folder(folder: Path) -> tuple[RawRecord, ...]:
    """폴더의 Surface.flt 를 파싱해 RawRecord 튜플 반환(없거나 손상이면 빈 튜플).

    스키마 미충전(_SCHEMA_READY=False) 이면 항상 빈 튜플 — 기능 비활성.
    같은 폴더의 두 번째 호출부터는 캐시에서 즉시 반환된다.
    """
    if not _SCHEMA_READY:
        return ()
    try:
        flt = _find_flt(folder)
        if flt is None:
            return ()
        return _parse_flt(flt)
    except Exception:
        _LOG.warning("Surface.flt 파싱 실패 — 측정정보 없음으로 처리: %s", folder,
                     exc_info=True)
        return ()
