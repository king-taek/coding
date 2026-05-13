"""매칭 결정 로그 + Hit@K 집계 + 자동 리네임.

흐름:
- Stage 2 의 매 pick/skip 마다 ``log_decision()`` 으로 JSONL 한 줄 append.
- 셋업 화면 진입 시 ``refresh_accuracy()`` 가 모든 모델의 로그를 다시 집계해
  메타 갱신 + 필요 시 ``registry.rename_with_accuracy()`` 호출.
- 파일명 표기에 사용하는 주 지표 = **Hit@5** (백분율 반올림).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..utils import paths
from . import registry


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_EVALS_FOR_LABEL = 10        # 이 수 미만이면 정확도 표기 안 함
RENAME_THRESHOLD_PCT = 2         # Hit@5 가 이 정도(%p) 차이날 때만 리네임


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def log_decision(*,
                 model_name: str,
                 session_id: str,
                 slot: str,
                 ref_path: Path,
                 threshold: float,
                 candidates: list[tuple[Path, float]],
                 picked_path: Optional[Path],
                 picked_rank: Optional[int],
                 skipped: bool) -> None:
    """매 결정마다 한 줄 append. 실패해도 예외를 던지지 않는다."""
    try:
        log_file = paths.evaluations_dir() / f"{model_name}.jsonl"
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session_id": session_id,
            "model": model_name,
            "slot": slot,
            "ref_path": str(Path(ref_path).resolve()),
            "threshold": float(threshold),
            "candidates": [
                {"path": str(Path(p).resolve()), "score": float(s)}
                for p, s in candidates
            ],
            "picked_path": (str(Path(picked_path).resolve())
                            if picked_path is not None else None),
            "picked_rank": (int(picked_rank) if picked_rank is not None else None),
            "skipped": bool(skipped),
        }
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        # 평가 로그 실패는 사용자 흐름을 막지 않는다.
        pass


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
@dataclass
class Metrics:
    num_evaluations: int = 0     # 총 결정 수 (pick + skip)
    picks: int = 0
    skips: int = 0
    hit_at_1: float = 0.0
    hit_at_5: float = 0.0
    hit_at_8: float = 0.0
    pick_rate: float = 0.0
    mean_rank: float = 0.0       # 1-based (사용자에게 친숙)
    last_evaluated_at: Optional[str] = None

    @property
    def has_enough(self) -> bool:
        return self.num_evaluations >= MIN_EVALS_FOR_LABEL


def aggregate(model_name: str) -> Metrics:
    """모델별 평가 로그를 다시 읽어 지표 계산."""
    log_file = paths.evaluations_dir() / f"{model_name}.jsonl"
    m = Metrics()
    if not log_file.exists():
        return m

    rank_sum = 0
    hit1 = hit5 = hit8 = 0
    last_ts: Optional[str] = None
    with log_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            m.num_evaluations += 1
            ts = row.get("ts")
            if isinstance(ts, str) and (last_ts is None or ts > last_ts):
                last_ts = ts
            if row.get("skipped"):
                m.skips += 1
                continue
            rank = row.get("picked_rank")
            if not isinstance(rank, int):
                continue
            m.picks += 1
            rank_sum += rank + 1
            if rank < 1:
                hit1 += 1
            if rank < 5:
                hit5 += 1
            if rank < 8:
                hit8 += 1

    if m.picks > 0:
        m.hit_at_1 = hit1 / m.picks
        m.hit_at_5 = hit5 / m.picks
        m.hit_at_8 = hit8 / m.picks
        m.mean_rank = rank_sum / m.picks
    if m.num_evaluations > 0:
        m.pick_rate = m.picks / m.num_evaluations
    m.last_evaluated_at = last_ts
    return m


def merge_into_meta(info: registry.ModelInfo, metrics: Metrics) -> dict:
    """기존 meta + 최신 metrics 를 합쳐 새 meta dict 반환."""
    meta = dict(info.meta)
    meta.update({
        "num_evaluations": metrics.num_evaluations,
        "picks": metrics.picks,
        "skips": metrics.skips,
        "hit_at_1": round(metrics.hit_at_1, 4),
        "hit_at_5": round(metrics.hit_at_5, 4),
        "hit_at_8": round(metrics.hit_at_8, 4),
        "pick_rate": round(metrics.pick_rate, 4),
        "mean_rank": round(metrics.mean_rank, 3),
    })
    if metrics.last_evaluated_at:
        meta["last_evaluated_at"] = metrics.last_evaluated_at
    return meta


# ---------------------------------------------------------------------------
# Refresh + rename trigger (셋업 화면 진입 시 호출)
# ---------------------------------------------------------------------------
@dataclass
class RefreshOutcome:
    info: registry.ModelInfo
    metrics: Metrics
    renamed_from: Optional[str] = None


def refresh_accuracy() -> list[RefreshOutcome]:
    """모든 학습 모델의 평가 로그를 집계하여 메타 / 파일명을 동기화."""
    out: list[RefreshOutcome] = []
    for info in registry.list_models():
        metrics = aggregate(info.name)
        new_meta = merge_into_meta(info, metrics)
        try:
            registry.write_meta(info, new_meta)
        except Exception:
            pass

        renamed_from: Optional[str] = None
        if metrics.has_enough:
            new_pct = int(round(metrics.hit_at_5 * 100))
            cur_pct = info.accuracy_pct
            if cur_pct is None or abs(new_pct - cur_pct) >= RENAME_THRESHOLD_PCT:
                try:
                    new_info = registry.rename_with_accuracy(info, new_pct)
                    if new_info.name != info.name:
                        renamed_from = info.name
                        info = new_info
                        # 리네임 후 새 메타 다시 보장
                        registry.write_meta(info, new_meta)
                except Exception:
                    pass

        out.append(RefreshOutcome(info=info, metrics=metrics,
                                  renamed_from=renamed_from))
    return out
