"""임시 레퍼런스 로깅 (진단용).

GPU/NPU vs CPU 의 매치 정확도를 비교하기 위해, 사용자가 매치 검토에서 수정하기
직전의 자동 랭킹(top-10)과 최종 결정, 사용된 모드·옵션, 그 ref 를 처리한 장치를
``결과/레퍼런스/{session}.jsonl`` 에 한 줄씩 남긴다.

- 첫 줄: ``{"type": "options", ...}`` (세션 모드/옵션).
- 이후 줄: ``{"type": "ref", ...}`` (ref 한 건).

모든 쓰기는 실패해도 예외를 던지지 않는다 — 진단 로깅이 본 작업 흐름을 막지 않도록.
임시 기능이므로 나중에 제거 예정.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from . import paths


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def session_path(session_id: Optional[str] = None) -> Optional[Path]:
    """세션별 레퍼런스 JSONL 경로 — ``결과/레퍼런스/`` 아래.  실패 시 None."""
    try:
        base = paths.results_dir() / "레퍼런스"
        base.mkdir(parents=True, exist_ok=True)
        name = f"{(session_id or 'session')}_{_ts()}.jsonl"
        return base / name
    except Exception:
        return None


def _append(path: Optional[Path], obj: dict) -> None:
    if path is None:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass            # 진단 로깅 실패는 무시


def write_options(path: Optional[Path], options: dict) -> None:
    """세션 헤더(모드/옵션)를 첫 줄로 기록."""
    rec = {"type": "options", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rec.update(options or {})
    _append(path, rec)


def append_ref(path: Optional[Path], record: dict) -> None:
    """ref 한 건(검토 전 top-10 + 최종 매치 + 장치)을 기록."""
    rec = {"type": "ref", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rec.update(record or {})
    _append(path, rec)


def append_final(path: Optional[Path], record: dict) -> None:
    """엑셀 저장 시점의 최종 매치 묶음(검토·미탐검토 반영 후)을 기록."""
    rec = {"type": "final", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    rec.update(record or {})
    _append(path, rec)
