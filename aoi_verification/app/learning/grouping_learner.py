"""Stage 1 그룹화 임계치를 사용자 피드백으로 자동 튜닝하는 학습기.

GroupReviewPage 에서 사용자가 ‘그룹 분리’ 한 결정과 ‘유지된 그룹’ 의 (rep, sib)
쌍을 학습 자료로 누적. 충분히 모이면 두 분포 (same / different) 사이의 최적
경계를 찾아 ``prefs.group_similarity`` 를 자동 갱신.

저장 위치: ``~/.aoi_verification_cache/grouping_feedback.jsonl``
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from ..similarity import phash as _phash
from ..utils import image_io, paths
from ..utils import prefs as _prefs


_FILE_NAME = "grouping_feedback.jsonl"
# 임계치 자동 튜닝을 시도할 최소 데이터 수.  너무 적으면 noise 가 우세.
_MIN_SAMPLES_FOR_RETUNE = 20
# 같은 그룹 데이터의 95% 가 임계치 이상이도록 — 학습 데이터의 5th percentile.
_SAME_PERCENTILE = 5
_LOCK = threading.Lock()


@dataclass
class _Sample:
    similarity: float
    label: str          # "same" or "different"


def _file() -> Path:
    return paths.cache_root() / _FILE_NAME


def _phash_sim(a: Path, b: Path) -> Optional[float]:
    """두 사진의 pHash 유사도. 실패 시 None."""
    try:
        ga = image_io.center_roi_gray(a)
        gb = image_io.center_roi_gray(b)
        ha = _phash.compute_phash(ga)
        hb = _phash.compute_phash(gb)
        return float(_phash.phash_similarity(ha, hb))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 피드백 기록 — GroupReviewPage 에서 사용자 변경 사항을 학습 자료로 변환
# ---------------------------------------------------------------------------
def record_feedback(*,
                    kept_pairs: Iterable[tuple[Path, Path]],
                    detached_pairs: Iterable[tuple[Path, Path]]) -> int:
    """사용자 검토 결과를 누적 저장한다.

    - ``kept_pairs``    : 사용자가 그대로 둔 (rep, sibling) 쌍 → ‘same’
    - ``detached_pairs``: 사용자가 분리한 (rep, detached) 쌍 → ‘different’
    """
    rows: list[dict] = []
    ts = datetime.now().isoformat(timespec="seconds")
    for a, b in kept_pairs:
        sim = _phash_sim(a, b)
        if sim is None:
            continue
        rows.append({"ts": ts, "phash_similarity": sim, "label": "same"})
    for a, b in detached_pairs:
        sim = _phash_sim(a, b)
        if sim is None:
            continue
        rows.append({"ts": ts, "phash_similarity": sim, "label": "different"})
    if not rows:
        return 0
    fp = _file()
    fp.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with fp.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def load_all() -> list[_Sample]:
    fp = _file()
    if not fp.exists():
        return []
    out: list[_Sample] = []
    with fp.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                sim = float(d.get("phash_similarity", 0.0))
                lab = str(d.get("label", ""))
                if lab in ("same", "different"):
                    out.append(_Sample(similarity=sim, label=lab))
            except Exception:
                continue
    return out


# ---------------------------------------------------------------------------
# 임계치 자동 튜닝
# ---------------------------------------------------------------------------
def maybe_retune_threshold() -> Optional[float]:
    """충분한 데이터가 모였으면 임계치를 자동 조정하고 새 값 반환.

    전략: same 분포의 5th percentile 을 임계치로 — 같은 그룹 데이터의 95% 가
    이 값 이상이 되도록.  단 different 분포의 max 값보다 너무 가까우면 두
    분포 중간점으로 후퇴 (separation 확보).  prefs.group_similarity 에
    즉시 반영.
    """
    samples = load_all()
    if len(samples) < _MIN_SAMPLES_FOR_RETUNE:
        return None
    same = np.array([s.similarity for s in samples if s.label == "same"])
    diff = np.array([s.similarity for s in samples if s.label == "different"])
    if same.size < 5 or diff.size < 5:
        return None
    same_5th = float(np.percentile(same, _SAME_PERCENTILE))
    diff_max = float(np.max(diff))
    # 두 분포가 겹치는 영역이 있으면 안전한 중간점 사용.
    if same_5th <= diff_max:
        new_thr = float((same_5th + diff_max) / 2.0)
    else:
        new_thr = same_5th
    new_thr = max(0.05, min(0.99, new_thr))
    try:
        _prefs.patch(group_similarity=float(new_thr))
    except Exception:
        return None
    return new_thr
