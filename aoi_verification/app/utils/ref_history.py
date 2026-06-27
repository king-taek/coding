"""기준(ref) 폴더별 '직접 고른 기준 사진' 기록 저장소.

같은 기준 폴더로 다시 검증을 시작할 때, 이전에 사용자가 직접 고른(=검증 대상으로
넘긴) 기준 사진을 그대로 재사용할지 물어보기 위한 영속화.

- 식별 키: 기준 폴더의 **절대경로** (사용자 확정).
- 저장 형식 (``~/.aoi_verification_cache/ref_selection_history.json``):

    { "<ref_root_abspath>": {
        "slots": { "<slot>": ["<filename>", ...], ... },
        "updated_at": "<iso8601>"
      }, ... }

- 읽기/쓰기 실패는 묵묵히 무시 — 검증 흐름을 절대 막지 않는다 (prefs 와 동일 원칙).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from . import paths

_HISTORY_FILE = "ref_selection_history.json"


def _file() -> Path:
    return paths.cache_root() / _HISTORY_FILE


def _key(ref_root) -> str:
    """기준 폴더 절대경로를 정규화한 식별 키."""
    try:
        return str(Path(ref_root).resolve())
    except Exception:
        return str(ref_root)


def _load_all() -> Dict[str, dict]:
    p = _file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(data: Dict[str, dict]) -> None:
    p = _file()
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def has_history(ref_root) -> bool:
    """이 기준 폴더로 매치를 진행한 기록(고른 기준 사진)이 있는지."""
    entry = _load_all().get(_key(ref_root))
    if not isinstance(entry, dict):
        return False
    slots = entry.get("slots")
    return bool(isinstance(slots, dict) and any(slots.values()))


def get_chosen(ref_root) -> Dict[str, List[str]]:
    """기준 폴더에서 이전에 고른 기준 사진 (슬롯 → 파일명 리스트). 없으면 {}."""
    entry = _load_all().get(_key(ref_root))
    if not isinstance(entry, dict):
        return {}
    slots = entry.get("slots")
    if not isinstance(slots, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for slot, names in slots.items():
        if isinstance(names, list):
            out[str(slot)] = [str(n) for n in names]
    return out


def save_chosen(ref_root, slots_to_filenames: Dict[str, List[str]]) -> None:
    """기준 폴더의 '직접 고른 기준 사진'을 슬롯별 파일명으로 저장.

    빈 매핑이면 (고른 사진이 없으면) 아무것도 기록하지 않는다.
    """
    cleaned = {
        str(slot): [str(n) for n in names]
        for slot, names in (slots_to_filenames or {}).items()
        if names
    }
    if not cleaned:
        return
    data = _load_all()
    data[_key(ref_root)] = {
        "slots": cleaned,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_all(data)
