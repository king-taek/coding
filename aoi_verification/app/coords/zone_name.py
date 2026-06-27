"""zone 코드 → 이름 매핑을 결과 폴더의 recipe 파일에서 읽는다.

zone 이름은 **자재/제품(recipe)별로 다르다** — 실측: 같은 zone 1 이 어떤 제품에선
``PI_Opening``, 다른 제품에선 ``VIA`` 또는 ``RDL``. 따라서 0.77(픽셀크기)처럼 하드코딩
하지 말고 결과 폴더의 ``ProductInfo.ini`` / ``RecipesInfo.ini`` 에 저장된 ``ZoneName``/
``ZoneID`` 쌍에서 읽는다.

실측 예: zone 1=PI_Opening, 2=RDL, 63=Scan Area, 255=PostProcess.
못 찾으면 빈 매핑(이름 없이 코드만).  전 구간 fail-safe.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_FILES = ("ProductInfo.ini", "RecipesInfo.ini", "Recipe2-ProductInfo.ini")
_SECTION = re.compile(r"\[[^\]]*\]")
_NAME = re.compile(r"(?im)^\s*ZoneName\s*=\s*(.+?)\s*$")
_ID = re.compile(r"(?im)^\s*ZoneID\s*=\s*(\d+)")


@lru_cache(maxsize=256)
def zone_map(folder: Path) -> tuple:
    """{zone_id: zone_name} 을 (id, name) 튜플들로 반환(캐시용 hashable).  fail-safe."""
    out: dict = {}
    try:
        for fn in _FILES:
            p = folder / fn
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # 각 zone 섹션에 ZoneName + ZoneID 가 함께 있다.
            for body in _SECTION.split(txt):
                nm = _NAME.search(body)
                zid = _ID.search(body)
                if nm and zid:
                    out.setdefault(int(zid.group(1)), nm.group(1).strip())
    except Exception:
        return ()
    return tuple(sorted(out.items()))


def name_for(folder: Path, zone: int) -> Optional[str]:
    """결과 폴더 기준 zone 코드의 이름.  없으면 None."""
    try:
        for zid, nm in zone_map(folder):
            if zid == zone:
                return nm
    except Exception:
        return None
    return None
