"""매칭 쌍 누적 저장소 (pairs.jsonl).

사용자가 결과 화면에서 ‘학습 자료로 사용’ 에 동의했을 때만 append 된다.
파일 형식은 줄 단위 JSON (append-only) — 락 충돌 위험이 거의 없고 큰 데이터셋도
스트리밍 로드 가능.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from ..models.result import MatchResult
from ..utils import paths


_PAIRS_FILE = "pairs.jsonl"


@dataclass(frozen=True)
class TrainingPair:
    slot: str
    ref_path: str
    val_path: str
    ref_machine: str
    val_machine: str
    direction: str       # "A→B" | "B→A" | "양방향"
    score: float
    ts: float            # epoch seconds

    def to_json(self) -> str:
        return json.dumps(
            {
                "slot": self.slot,
                "ref_path": self.ref_path,
                "val_path": self.val_path,
                "ref_machine": self.ref_machine,
                "val_machine": self.val_machine,
                "direction": self.direction,
                "score": self.score,
                "ts": self.ts,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, d: dict) -> "TrainingPair":
        return cls(
            slot=str(d.get("slot", "")),
            ref_path=str(d.get("ref_path", "")),
            val_path=str(d.get("val_path", "")),
            ref_machine=str(d.get("ref_machine", "")),
            val_machine=str(d.get("val_machine", "")),
            direction=str(d.get("direction", "A→B")),
            score=float(d.get("score", 0.0)),
            ts=float(d.get("ts", 0.0)),
        )


class TrainingDataStore:
    """append-only JSONL 저장소."""

    def __init__(self, file: Path | None = None) -> None:
        self._file = Path(file) if file is not None else (
            paths.training_data_dir() / _PAIRS_FILE
        )

    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._file

    def append_session(self,
                       matches: Iterable[MatchResult],
                       *,
                       ref_machine: str,
                       val_machine: str) -> int:
        """한 세션의 매칭 쌍을 append. 추가된 줄 수를 반환."""
        rows = 0
        ts = time.time()
        with self._file.open("a", encoding="utf-8") as f:
            for m in matches:
                pair = TrainingPair(
                    slot=m.slot,
                    ref_path=str(Path(m.ref_path).resolve()),
                    val_path=str(Path(m.val_path).resolve()),
                    ref_machine=ref_machine,
                    val_machine=val_machine,
                    direction=str(m.direction),
                    score=float(m.score),
                    ts=ts,
                )
                f.write(pair.to_json() + "\n")
                rows += 1
        return rows

    # ------------------------------------------------------------------
    def count(self) -> int:
        if not self._file.exists():
            return 0
        n = 0
        with self._file.open("r", encoding="utf-8") as f:
            for _ in f:
                n += 1
        return n

    def iter_pairs(self) -> Iterator[TrainingPair]:
        if not self._file.exists():
            return
        with self._file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield TrainingPair.from_dict(json.loads(line))
                except Exception:
                    continue

    def load_all(self) -> list[TrainingPair]:
        return list(self.iter_pairs())

    # ------------------------------------------------------------------
    def clear(self) -> None:
        if self._file.exists():
            try:
                self._file.unlink()
            except OSError:
                pass
