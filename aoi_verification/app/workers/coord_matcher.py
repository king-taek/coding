"""좌표 기반 매칭 스케줄러 (v2).

이미지 유사도를 계산하지 않고 defect 좌표(col/row + x/y µm)로 직접 매칭한다.

매칭 규칙:
    1. col/row 가 완전히 일치해야 후보로 인정 (정수 게이트).
    2. 유클리드 거리 sqrt((x1-x2)^2 + (y1-y2)^2) ≤ tolerance (µm) 면 매칭.
    3. 점수 = max(0.0, 1.0 - dist/tolerance)  (tolerance 가 0 이면 1.0).
    4. 좌표가 없는 ref 는 score_ref_classical 으로 폴백(기본 모드 동작 보존).

시그널 계약은 EfficiencyScheduler / SlotPrecomputeWorker 와 동일:
    progress(done_pairs: int, total_pairs: int)
    slot_finished(slot: str, done_slots: int, total_slots: int)
    phase(phase_label: str)
    finished()
    failed(msg: str)

결과는 ``results[(slot, ref_path)] = [(val_path, score), ...]`` (내림차순) 에 저장.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..coords import resolve as _resolve_coord
from ..models.slot import ImageItem
from .matcher import score_ref_classical


# ---------------------------------------------------------------------------
class _CoordSignals(QObject):
    progress = pyqtSignal(int, int)            # done_pairs, total_pairs
    slot_finished = pyqtSignal(str, int, int)  # slot, done_slots(1-base), total_slots
    phase = pyqtSignal(str)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


# ---------------------------------------------------------------------------
class CoordScheduler(QThread):
    """슬롯 순차로 좌표 매칭(v2) → ``results`` 저장.

    SlotPrecomputeWorker / EfficiencyScheduler 와 동일 시그널 계약이므로
    match_page 의 신호 연결 코드를 수정하지 않아도 된다.
    """

    def __init__(self,
                 tasks: List[Tuple[str, List[ImageItem], List[ImageItem]]],
                 *,
                 cfg=None,
                 threshold: float = 0.0,
                 auto: bool = False,
                 results: Optional[dict] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tasks = [(s, list(r), list(v)) for s, r, v in tasks]
        self._cfg = cfg
        self._threshold = float(threshold)
        self._auto = bool(auto)
        self._results = results if results is not None else {}
        self._stop = threading.Event()
        self.signals = _CoordSignals()

        tol = getattr(cfg, "coord_tolerance", 500.0) if cfg is not None else 500.0
        self._tolerance = float(tol) if tol and tol > 0.0 else 500.0

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:        # type: ignore[override]
        try:
            self._run()
        except Exception as exc:
            self.signals.failed.emit(str(exc))

    # ------------------------------------------------------------------
    def _run(self) -> None:
        total_slots = len(self._tasks)
        total_pairs = sum(len(r) * len(v) for _, r, v in self._tasks)
        done_pairs = 0
        self.signals.phase.emit("좌표 매칭 중")

        for slot_idx, (slot, refs, vals) in enumerate(self._tasks):
            if self._stop.is_set():
                break

            # val 좌표 캐시 — (col, row) → [(item, x, y), ...]
            val_coord_map: Dict[Tuple[int, int], List[Tuple[ImageItem, float, float]]] = {}
            for v in vals:
                coord = _resolve_coord(v.path)
                if coord is not None:
                    key = (coord.col, coord.row)
                    val_coord_map.setdefault(key, []).append((v, coord.x, coord.y))

            fallback_refs: List[ImageItem] = []

            for ref in refs:
                if self._stop.is_set():
                    break

                ref_coord = _resolve_coord(ref.path)

                if ref_coord is None:
                    # 좌표 없는 ref → 폴백 대기열
                    fallback_refs.append(ref)
                    done_pairs += len(vals)
                    self.signals.progress.emit(done_pairs, total_pairs)
                    continue

                candidates = val_coord_map.get((ref_coord.col, ref_coord.row), [])
                scored: List[Tuple[Path, float]] = []
                for v_item, vx, vy in candidates:
                    dist = math.sqrt((ref_coord.x - vx) ** 2 + (ref_coord.y - vy) ** 2)
                    if dist <= self._tolerance:
                        score = max(0.0, 1.0 - dist / self._tolerance) if self._tolerance > 0 else 1.0
                        scored.append((v_item.path, score))

                scored.sort(key=lambda x: -x[1])
                self._results[(slot, ref.path)] = scored
                done_pairs += len(vals)
                self.signals.progress.emit(done_pairs, total_pairs)

            # 폴백 — 좌표 없는 ref 를 고전 유사도로 처리
            for ref in fallback_refs:
                if self._stop.is_set():
                    break
                cands = score_ref_classical(
                    ref, vals, threshold=0.0, cfg=self._cfg,
                    stop_cb=self._stop.is_set,
                )
                self._results[(slot, ref.path)] = [
                    (c.item.path, float(c.score)) for c in cands
                ]

            self.signals.slot_finished.emit(slot, slot_idx + 1, total_slots)

        self.signals.finished.emit()
