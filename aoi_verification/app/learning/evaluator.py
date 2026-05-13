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
from dataclasses import dataclass, field
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
                 decision: str = "pick") -> None:
    """매 결정마다 한 줄 append. 실패해도 예외를 던지지 않는다.

    decision: "pick" | "defer" | "none"
      - pick: 사용자가 후보를 선택해 매칭 확정
      - defer: ‘잠시 보류’ — Skip 재시도 대상
      - none: ‘매칭 없음 확정’ — 미탐 영구 기록
    """
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
            "decision": decision,
            # 호환 — 기존 로그 reader 가 ‘skipped’ 만 보는 경우 대비
            "skipped": decision != "pick",
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
    num_evaluations: int = 0     # 결정 수 (pick + none) — defer 는 제외
    picks: int = 0
    none_count: int = 0          # 매칭 없음 확정
    defers: int = 0              # 잠시 보류 (지표에서 제외, 참고용)
    hit_at_1: float = 0.0
    hit_at_5: float = 0.0
    hit_at_8: float = 0.0
    pick_rate: float = 0.0
    mean_rank: float = 0.0       # 1-based (사용자에게 친숙)
    last_evaluated_at: Optional[str] = None
    # 신뢰구간 — Wilson score interval (#8)
    hit_at_5_lo: float = 0.0
    hit_at_5_hi: float = 0.0
    # 슬롯별 / 호기쌍별 분해 (#7) — key → (num_evals, hit_at_5)
    per_slot: dict[str, tuple[int, float]] = field(default_factory=dict)

    @property
    def has_enough(self) -> bool:
        return self.num_evaluations >= MIN_EVALS_FOR_LABEL


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% 신뢰구간 (lower, upper)."""
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _row_decision(row: dict) -> str:
    """legacy row 호환 — decision 필드가 없으면 skipped 로부터 추론."""
    d = row.get("decision")
    if d in ("pick", "defer", "none"):
        return d
    return "defer" if row.get("skipped") else "pick"


def aggregate(model_name: str) -> Metrics:
    """모델별 평가 로그를 다시 읽어 지표 계산.

    - ``defer`` 는 지표에서 제외 (사용자가 ‘잠시 보류’ 한 결정이므로 모델 평가
      대상이 아님).
    - ``pick`` 은 picked_rank 가 곧 정답 순위.
    - ``none`` 은 “모델 추천이 무엇이든 정답이 없었음” → ``num_evaluations`` 에
      포함하지만 Hit@K 분모에는 안 들어감 (Hit@K 는 picks 만 사용).
    """
    log_file = paths.evaluations_dir() / f"{model_name}.jsonl"
    m = Metrics()
    if not log_file.exists():
        return m

    rank_sum = 0
    hit1 = hit5 = hit8 = 0
    last_ts: Optional[str] = None

    per_slot_picks: dict[str, int] = {}
    per_slot_hit5: dict[str, int] = {}

    with log_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            decision = _row_decision(row)
            ts = row.get("ts")
            if isinstance(ts, str) and (last_ts is None or ts > last_ts):
                last_ts = ts
            slot = str(row.get("slot", ""))

            if decision == "defer":
                m.defers += 1
                continue
            if decision == "none":
                m.none_count += 1
                m.num_evaluations += 1
                continue

            # decision == "pick"
            rank = row.get("picked_rank")
            if not isinstance(rank, int):
                continue
            m.num_evaluations += 1
            m.picks += 1
            rank_sum += rank + 1
            if rank < 1:
                hit1 += 1
            if rank < 5:
                hit5 += 1
                per_slot_hit5[slot] = per_slot_hit5.get(slot, 0) + 1
            if rank < 8:
                hit8 += 1
            per_slot_picks[slot] = per_slot_picks.get(slot, 0) + 1

    if m.picks > 0:
        m.hit_at_1 = hit1 / m.picks
        m.hit_at_5 = hit5 / m.picks
        m.hit_at_8 = hit8 / m.picks
        m.mean_rank = rank_sum / m.picks
        m.hit_at_5_lo, m.hit_at_5_hi = wilson_interval(hit5, m.picks)
    if m.num_evaluations > 0:
        m.pick_rate = m.picks / m.num_evaluations
    m.last_evaluated_at = last_ts

    for s, picks in per_slot_picks.items():
        h5 = per_slot_hit5.get(s, 0) / picks if picks else 0.0
        m.per_slot[s] = (picks, h5)

    return m


def merge_into_meta(info: registry.ModelInfo, metrics: Metrics) -> dict:
    """기존 meta + 최신 metrics 를 합쳐 새 meta dict 반환."""
    meta = dict(info.meta)
    meta.update({
        "num_evaluations": metrics.num_evaluations,
        "picks": metrics.picks,
        "none_count": metrics.none_count,
        "defers": metrics.defers,
        "hit_at_1": round(metrics.hit_at_1, 4),
        "hit_at_5": round(metrics.hit_at_5, 4),
        "hit_at_8": round(metrics.hit_at_8, 4),
        "hit_at_5_lo": round(metrics.hit_at_5_lo, 4),
        "hit_at_5_hi": round(metrics.hit_at_5_hi, 4),
        "pick_rate": round(metrics.pick_rate, 4),
        "mean_rank": round(metrics.mean_rank, 3),
        "per_slot": {
            k: {"picks": v[0], "hit_at_5": round(v[1], 4)}
            for k, v in metrics.per_slot.items()
        },
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


def basic_metrics() -> Metrics:
    """``basic.jsonl`` (기본 탐지 모드) 집계 — 학습 모델과 비교용 (#6)."""
    return aggregate(registry.BASIC)


def refresh_accuracy() -> list[RefreshOutcome]:
    """모든 학습 모델의 평가 로그를 집계하여 메타 / 파일명을 동기화.

    basic 모드 자체는 가중치 파일이 없어 리네임 대상이 아니지만, basic.jsonl
    의 메트릭은 setup_page.refresh_models() 가 ``aggregate(BASIC)`` 으로 직접
    읽어가므로 별도 처리할 필요 없다.
    """
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
