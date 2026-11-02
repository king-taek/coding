"""이전 결과 엑셀을 기준으로 쓸 때의 좌표 — 풀어 둔 폴더 옆의 사이드카 파일.

:mod:`~..utils.excel_ref` 가 엑셀을 ``<루트>/<슬롯>/<파일명>`` 사진으로 풀면서 루트에
``.excel_ref.json`` 을 함께 쓴다(사진마다 엑셀 셀에 적혀 있던 col/row · x/y).
풀어 둔 사진에는 옆에 INI·``.001`` 이 없어 다른 파서가 좌표를 못 읽으므로, 여기서 읽는다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import DefectCoord

__all__ = ["SIDECAR_NAME", "resolve", "photo_key"]

SIDECAR_NAME = ".excel_ref.json"


def photo_key(slot: str, stem: str) -> str:
    return f"{slot}/{stem}".lower()


@lru_cache(maxsize=8)
def _load(root: str) -> dict:
    try:
        data = json.loads((Path(root) / SIDECAR_NAME).read_text(encoding="utf-8"))
        return data.get("photos", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """``<루트>/<슬롯>/<사진>`` 이고 루트에 사이드카가 있으면 그 좌표, 아니면 None."""
    p = Path(image_path)
    root = p.parent.parent
    if not (root / SIDECAR_NAME).is_file():
        return None
    rec = _load(str(root)).get(photo_key(p.parent.name, p.stem))
    if not rec:
        return None
    try:
        return DefectCoord(
            col=int(rec["col"]), row=int(rec["row"]),
            x=float(rec["x"]), y=float(rec["y"]), source=str(rec["source"]),
            native_x=rec.get("native_x"), native_y=rec.get("native_y"))
    except (KeyError, TypeError, ValueError):
        return None
