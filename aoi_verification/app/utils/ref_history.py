"""기준(ref) 폴더별 '직접 고른 기준 사진' 기록 저장소.

같은 기준 폴더로 다시 검증을 시작할 때, 이전에 사용자가 직접 고른(=검증 대상으로
넘긴) 기준 사진을 그대로 재사용할지 물어보기 위한 영속화.

- 식별 키: 기준 폴더의 **절대경로** (사용자 확정).
- 저장 형식 (``~/.aoi_verification_cache/ref_selection_history.json``):

    { "<ref_root_abspath>": {
        "slots": { "<slot>": ["<filename>", ...], ... },
        "updated_at": "<iso8601>"
      }, ... }

- 읽기/쓰기 실패가 검증 흐름을 막지 않는다 (prefs 와 동일 원칙).  단 읽기 실패의 '빈 기록' 이
  다음 저장에서 사용자 파일을 덮어쓰지 않게 한다: 깨진 JSON 은 ``.corrupt-<시각>`` 으로 치우고,
  일시적 읽기 오류(OSError)면 그 저장은 건너뛴다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from . import paths
from .prefs import move_aside_corrupt

_LOG = logging.getLogger("aoi.ref_history")

_HISTORY_FILE = "ref_selection_history.json"


def _file() -> Path:
    return paths.cache_root() / _HISTORY_FILE


def _key(ref_root) -> str:
    """기준 폴더 절대경로를 정규화한 식별 키."""
    try:
        return str(Path(ref_root).resolve())
    except Exception:
        return str(ref_root)


def _read_all() -> Tuple[Dict[str, dict], bool]:
    """(기록, 안전하게 덮어써도 되는가).

    파일 없음 = 정상(빈 기록, 쓰기 가능).  깨진 JSON = 치우고 빈 기록(쓰기 가능 — 원본은 보존됨).
    일시적 읽기 오류 = 빈 기록이지만 **쓰기 불가**(있는 파일을 모르는 채 덮어쓰면 기록을 잃는다)."""
    p = _file()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, True
    except UnicodeDecodeError:
        text = None
    except OSError:
        _LOG.warning("기준 사진 기록을 읽지 못했다(일시적일 수 있음): %s", p, exc_info=True)
        return {}, False
    try:
        if text is None:
            raise ValueError("UTF-8 이 아니다")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("최상위가 객체가 아니다")
        return data, True
    except Exception:
        moved = move_aside_corrupt(p)
        _LOG.warning("기준 사진 기록이 깨져 있다 — 비어 있는 것으로 시작한다(원본: %s)",
                     moved or p, exc_info=True)
        return {}, True


def _load_all() -> Dict[str, dict]:
    return _read_all()[0]


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
    data, writable = _read_all()
    if not writable:
        _LOG.warning("기록을 읽지 못한 상태라 저장을 건너뛴다: %s", _file())
        return
    data[_key(ref_root)] = {
        "slots": cleaned,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_all(data)
