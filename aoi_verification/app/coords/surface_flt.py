"""Surface.flt 바이너리 파서 → 결함 raw geometry 레코드.

각 결과 폴더의 ``Surface.flt`` 는 152바이트 고정 레코드의 연속이다(보고서 기준).
한 레코드에서 절대 좌표(ActualX/ActualY)와 geometry(area/BlobBreadth/BlobFeretMax/
Contrast)를 읽어 :class:`geometry.resolve` 가 이미지 좌표와 nearest-match 하는 데 쓴다.

──────────────────────────────────────────────────────────────────────────
스키마(바이트 오프셋)는 외부 ``Surface.flt.md`` 에만 있고 저장소엔 없다.  실제 값
추출에 필요한 **유일한 미정 조각**이라, 아래 ``_FIELDS`` 한 곳에 격리한다.

  · 오프셋이 채워지기 전(_SCHEMA_READY=False): 파서는 항상 빈 결과를 돌려준다.
    → 보고서(엑셀)는 기존과 100% 동일하게 렌더되고, 합성 바이트로 단위 테스트만 가능.
  · ``Surface.flt.md`` 에서 각 필드의 (offset, struct 포맷)을 채우면 즉시 동작한다.

채울 값(전부 little-endian ``<`` 가정 — 다르면 _BYTE_ORDER 수정):
    actual_x, actual_y : 절대 wafer 좌표 (보통 float32 'f' 또는 float64 'd')
    area, blob_breadth, blob_feret_max, contrast : geometry (보통 'f')
레코드 앞에 헤더가 있으면 _HEADER_BYTES 에 그 바이트 수를 넣는다.
──────────────────────────────────────────────────────────────────────────

기존 coords 파서(camtek_ini/kla_info)와 동일한 관습: 폴더 단위 ``lru_cache``,
fail-safe(파일 없음/손상 시 빈 결과, **절대 raise 안 함**).
"""

from __future__ import annotations

import struct
from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from typing import Optional

__all__ = ["load_folder", "has_flt", "RawRecord"]

# ── 스키마(사용자가 Surface.flt.md 로 채울 유일한 곳) ──────────────────────
_BYTE_ORDER = "<"        # little-endian (다르면 ">" 로)
_RECORD_SIZE = 152       # 한 레코드 바이트 수 (확인 필요)
_HEADER_BYTES = 0        # 레코드 0 앞 헤더 바이트 수 (없으면 0)

# field → (record 내부 byte offset, struct 포맷 문자).  offset 이 None 이면 미정.
_FIELDS: dict[str, tuple[Optional[int], str]] = {
    "actual_x":       (None, "f"),
    "actual_y":       (None, "f"),
    "area":           (None, "f"),
    "blob_breadth":   (None, "f"),
    "blob_feret_max": (None, "f"),
    "contrast":       (None, "f"),
}

# 모든 오프셋이 채워졌는지 — 하나라도 None 이면 파서는 비활성(빈 결과).
_SCHEMA_READY: bool = all(off is not None for off, _ in _FIELDS.values())

# Surface.flt 파일명 후보 — 대소문자 두 가지.
_FLT_CANDIDATES = ("Surface.flt", "surface.flt")

RawRecord = namedtuple(
    "RawRecord",
    ["actual_x", "actual_y", "area", "blob_breadth", "blob_feret_max", "contrast"],
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


def _parse_flt(path: Path) -> tuple[RawRecord, ...]:
    data = path.read_bytes()
    out: list[RawRecord] = []
    pos = _HEADER_BYTES
    n = len(data)
    while pos + _RECORD_SIZE <= n:
        vals: dict[str, Optional[float]] = {}
        ok = True
        for name, (offset, fmt) in _FIELDS.items():
            v = _unpack(data, pos, offset, fmt)  # type: ignore[arg-type]
            if v is None:
                ok = False
                break
            vals[name] = v
        if ok:
            out.append(RawRecord(**vals))  # type: ignore[arg-type]
        pos += _RECORD_SIZE
    return tuple(out)


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
        return ()
