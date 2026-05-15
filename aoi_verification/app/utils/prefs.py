"""사용자 UI 환경설정의 영속 저장소.

- 슬라이더 위치 / 임계치 / 사진 크기 / 모델 카드 펼침 여부 등.
- ``~/.aoi_verification_cache/ui_prefs.json`` 1 개 파일에 통합.
- 읽기 실패는 묵묵히 기본값으로 fallback (검증 흐름을 절대 막지 않는다).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths


_PREFS_FILE = "ui_prefs.json"


@dataclass
class UiPrefs:
    """다음 실행에도 이어갈 UI 상태."""

    threshold: float = 0.55                  # 0.0 ~ 1.0 (교차 호기 친화적 기본)
    image_long_edge_select: int = 400        # Stage 1 사진 크기 (px) — 300~700
    image_long_edge_match: int = 400         # Stage 2 사진 크기 (px) — 300~700
    window_preset: str = "보통"               # 화면 크기 프리셋 (5가지 중 하나)
    last_ref_root: str = ""
    last_val_root: str = ""
    last_ref_machine: str = ""
    last_val_machine: str = ""
    last_mode: str = "single"
    group_similarity: float = 0.92           # pHash 그룹화 임계치 (#15)
    group_min_size: int = 3                  # 이 수 이상이어야 그룹으로 묶음
    # 창 크기 / 전체화면 (사용자가 직접 선택) — 0 은 “미설정” 으로 간주.
    window_width: int = 0
    window_height: int = 0
    fullscreen: bool = False
    # QSplitter 상태 (Select/Match 페이지). base64 인코딩 문자열.
    splitter_state_select_h: str = ""
    splitter_state_select_v: str = ""
    splitter_state_match_h: str = ""
    # 사용 방법 패널 펼침 상태 (기본 접힘)
    howto_expanded: bool = False
    # 썸네일 빠른 모드 (사용자가 강제로 가장 낮은 품질 티어 사용)
    speed_mode: bool = False
    # 자동화 수준 — 사용자 개입 정도 (#3 올인원 모드)
    #   "manual"      : 기존 흐름. Stage 1 (검증/제외) + Stage 2 (수동 매치).
    #   "user_select" : Stage 1 만 직접, Stage 2 자동 매치 + 검토.
    #   "auto_all"    : Stage 1 건너뜀 (모든 ref 사용 + 그룹 대표만 큐에),
    #                   Stage 2 자동 매치 + 그룹/매치 검토.
    automation_level: str = "manual"
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UiPrefs":
        valid = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**valid)


# ---------------------------------------------------------------------------
def _file() -> Path:
    return paths.cache_root() / _PREFS_FILE


def load() -> UiPrefs:
    p = _file()
    if not p.exists():
        return UiPrefs()
    try:
        return UiPrefs.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return UiPrefs()


def save(prefs: UiPrefs) -> None:
    p = _file()
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(prefs.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError:
        pass


def patch(**kwargs: Any) -> UiPrefs:
    """원하는 필드만 갱신. 결과 UiPrefs 반환."""
    cur = load()
    for k, v in kwargs.items():
        if k in UiPrefs.__dataclass_fields__:
            setattr(cur, k, v)
    save(cur)
    return cur
