"""매칭 결과 데이터 클래스."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MatchDirection = Literal["A→B", "B→A", "양방향"]


@dataclass
class MatchResult:
    """기준 사진 ↔ 검증 사진 1:1 매칭 한 줄."""
    slot: str
    ref_path: Path        # 항상 "낮은 호기" 쪽 사진을 가리키도록 정규화
    val_path: Path        # "높은 호기" 쪽 사진
    score: float
    direction: MatchDirection = "A→B"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.slot, self.ref_path.name, self.val_path.name)


@dataclass
class MissEntry:
    """매칭되지 못해 상대 장비의 미탐(놓침)으로 기록되는 항목."""
    slot: str
    side: str            # "ref" or "val" (어느 장비에서 봤는데 상대가 못 봤는지)
    path: Path
    note: str = ""


@dataclass
class FinalResult:
    """엑셀 저장으로 전달되는 최종 결과 묶음."""
    mode: str                                            # "single" | "cross"
    ref_machine: str                                     # 예) "1호기"
    val_machine: str                                     # 예) "3호기"
    matches: list[MatchResult] = field(default_factory=list)
    miss_fast: list[MissEntry] = field(default_factory=list)   # 빠른(낮은) 호기 쪽
    miss_slow: list[MissEntry] = field(default_factory=list)   # 느린(높은) 호기 쪽
    slot_only_ref: list[str] = field(default_factory=list)
    slot_only_val: list[str] = field(default_factory=list)
    # Stage 2 에서 매칭을 찾지 못한 (Skip + No-match) 기준 사진들 — 엑셀에
    # ‘기준 이미지 + 빨간 파일명’ 행으로 함께 표기 (#7).
    unmatched_refs: list[MissEntry] = field(default_factory=list)
