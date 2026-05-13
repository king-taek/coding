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
from ..utils import content_hash as _ch


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
    ref_hash: str = ""   # 컨텐츠 해시 (경로 이동 대비)
    val_hash: str = ""
    source: str = "session"     # "session" | "evaluation"

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        """슬롯 + 양 끝 컨텐츠 해시 — 중복 학습 데이터 제거용."""
        return (self.slot, self.ref_hash, self.val_hash)

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
                "ref_hash": self.ref_hash,
                "val_hash": self.val_hash,
                "source": self.source,
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
            ref_hash=str(d.get("ref_hash", "")),
            val_hash=str(d.get("val_hash", "")),
            source=str(d.get("source", "session")),
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
                       val_machine: str,
                       source: str = "session") -> int:
        """한 세션의 매칭 쌍을 append. 이미 같은 ``dedup_key`` 가 있으면 skip.

        반환 = 실제로 추가된 줄 수.
        """
        known = {p.dedup_key for p in self.iter_pairs()}
        rows = 0
        ts = time.time()
        with self._file.open("a", encoding="utf-8") as f:
            for m in matches:
                rh = _ch.safe_content_hash(m.ref_path) or ""
                vh = _ch.safe_content_hash(m.val_path) or ""
                pair = TrainingPair(
                    slot=m.slot,
                    ref_path=str(Path(m.ref_path).resolve()),
                    val_path=str(Path(m.val_path).resolve()),
                    ref_machine=ref_machine,
                    val_machine=val_machine,
                    direction=str(m.direction),
                    score=float(m.score),
                    ts=ts,
                    ref_hash=rh,
                    val_hash=vh,
                    source=source,
                )
                if pair.dedup_key in known:
                    continue
                known.add(pair.dedup_key)
                f.write(pair.to_json() + "\n")
                rows += 1
        return rows

    def append_evaluation_picks(self) -> int:
        """``evaluations/*.jsonl`` 의 confirmed pick 항목을 학습 데이터로 통합 (#5).

        해당 모델 평가 로그를 모두 읽어 ``decision == "pick"`` 인 항목을
        dedup 후 append. 이미 session 으로 들어왔던 쌍은 자동 skip 된다.
        반환 = 실제로 추가된 줄 수.
        """
        eval_dir = paths.evaluations_dir()
        if not eval_dir.exists():
            return 0

        known = {p.dedup_key for p in self.iter_pairs()}
        rows = 0
        ts = time.time()

        with self._file.open("a", encoding="utf-8") as f:
            for log in eval_dir.glob("*.jsonl"):
                model = log.stem
                for line in log.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    decision = row.get("decision")
                    if decision is None:
                        decision = "defer" if row.get("skipped") else "pick"
                    if decision != "pick":
                        continue
                    ref = row.get("ref_path")
                    val = row.get("picked_path")
                    if not (isinstance(ref, str) and isinstance(val, str)):
                        continue
                    slot = str(row.get("slot", ""))
                    rh = _ch.safe_content_hash(ref) or ""
                    vh = _ch.safe_content_hash(val) or ""
                    pair = TrainingPair(
                        slot=slot,
                        ref_path=str(Path(ref).resolve()),
                        val_path=str(Path(val).resolve()),
                        ref_machine="", val_machine="",
                        direction="A→B",
                        score=float(0.0),
                        ts=ts,
                        ref_hash=rh,
                        val_hash=vh,
                        source=f"eval:{model}",
                    )
                    if pair.dedup_key in known:
                        continue
                    known.add(pair.dedup_key)
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
