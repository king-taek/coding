"""세션 상태(자동 저장/이어하기) 모델 + JSON 직렬화 헬퍼."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from ..utils import paths


@dataclass
class SessionState:
    """현재 작업 중인 검증 세션의 모든 상태."""

    # ── 입력 ────────────────────────────────────────────────────────────
    mode: str = "single"                       # "single" | "cross"
    ref_root: str = ""
    val_root: str = ""
    ref_machine: str = ""
    val_machine: str = ""
    threshold: float = 0.7
    session_id: str = ""

    # ── Stage 1 진행 상태 ────────────────────────────────────────────────
    stage: str = "setup"                       # setup|stage1|stage2|result
    phase: str = "A"                           # cross 모드에서 "A" / "B"
    # ImageItem.key → "verify" | "exclude" (Stage 1 의 누적 결정)
    decisions: dict[str, str] = field(default_factory=dict)
    decision_history: list[list[Any]] = field(default_factory=list)
    # 직렬화 가능한 형태로 변경: [(action, key), ...]

    # ── Stage 2 진행 상태 ────────────────────────────────────────────────
    matches: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)        # defer 풀
    no_match: list[str] = field(default_factory=list)       # 매칭 없음 확정

    # ── 교차 검증 ────────────────────────────────────────────────────────
    phase_a_matched_val_keys: list[str] = field(default_factory=list)
    phase_a_decisions: dict[str, str] = field(default_factory=dict)
    phase_a_matches: list[dict[str, Any]] = field(default_factory=list)
    phase_a_no_match: list[str] = field(default_factory=list)
    cross_matches_b: list[dict[str, Any]] = field(default_factory=list)
    cross_skipped_b: list[str] = field(default_factory=list)

    # ── 메타 ────────────────────────────────────────────────────────────
    updated_at: float = field(default_factory=lambda: time.time())

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        # 알 수 없는 키는 무시하여 하위 호환 유지
        valid = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**valid)


# ---------------------------------------------------------------------------
# Persistence — single shared file in cache dir
# ---------------------------------------------------------------------------
_SESSION_FILE = "current_session.json"


def session_path() -> Path:
    return paths.session_cache_dir() / _SESSION_FILE


def save(state: SessionState) -> None:
    state.updated_at = time.time()
    tmp = session_path().with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(session_path())


def load() -> SessionState | None:
    p = session_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return SessionState.from_dict(data)
    except Exception:
        return None


def clear() -> None:
    p = session_path()
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass
