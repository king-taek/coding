"""좌표 기반 매칭 스케줄러 (v2).

이미지 유사도를 계산하지 않고 defect 좌표(col/row + x/y µm)로 직접 매칭한다.

매칭 규칙:
    1. col/row 가 완전히 일치해야 후보로 인정 (정수 게이트).
    2. dist = sqrt((x1-x2)²+(y1-y2)²)
       · dist ≤ tol              → 양수 score = 1 - dist/tol  (허용 오차 내)
       · tol < dist ≤ tol×3     → 음수 score = -(dist/tol)   (허용범위 초과)
       · dist > tol×3           → 매치 실패 (_failed_set 에 추가, 결과 빈 목록)
       표시 규칙(검토 화면): 최소 거리 ≤ CONFIDENT_DIST 면 '거의 정확히 일치'로
       보고 후보 1장만, 그렇지 않으면 tol×3 이내 후보를 모두 차순위로 보여준다.
    3. 좌표가 없는 ref 는 score_ref_classical 으로 폴백(기본 모드 동작 보존).
    4. 모든 이미지 좌표를 시작 전 일괄 프리패치해 INI/KLA 반복 파싱을 방지.

score 인코딩 (match_review_page 역산용):
    · score ≥ 0  →  dist = (1 - score) × tol  µm
    · score < 0  →  dist = (-score) × tol  µm  (허용범위 초과 표식)

시그널 계약은 EfficiencyScheduler / SlotPrecomputeWorker 와 동일:
    progress(done_pairs: int, total_pairs: int)
    slot_finished(slot: str, done_slots: int, total_slots: int)
    phase(phase_label: str)
    finished()
    failed(msg: str)

결과는 ``results[(slot, ref_path)] = [(val_path, score), ...]`` (내림차순) 에 저장.
실패 목록은 ``failed_set: frozenset[(slot, ref_path)]`` 속성으로 접근.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..coords import resolve_batch as _resolve_batch
from ..models.slot import ImageItem
from .matcher import score_ref_classical

try:
    import numpy as _np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# 좌표 차이가 이 값(좌표 단위, µm) 이하이면 '거의 정확히 일치'로 보고 후보 1장만
# 보여준다. 초과 시 tol×3 이내 후보를 모두 차순위로 노출해 사용자가 직접 고른다.
CONFIDENT_DIST = 20.0


def _select_coord_candidates(
    within3: List[Tuple[Path, float]], tol: float
) -> List[Tuple[Path, float]]:
    """tol×3 이내 후보 ``(path, dist)`` 목록에서 검토 화면에 보여줄 후보를
    ``(path, score)`` 로 환산해 반환한다(거리 오름차순 = 점수 내림차순).

    · 최소 거리 ≤ :data:`CONFIDENT_DIST`  → 가장 가까운 1장만 (확정에 가까움).
    · 그 외                                → 전부 노출(사용자가 직접 고름).

    score 인코딩은 ``_RunnerUpTile`` 역산과 round-trip 되게 유지한다:
    dist ≤ tol 은 양수(1-dist/tol), tol < dist ≤ tol×3 은 음수(-dist/tol).
    """
    def score_of(dist: float) -> float:
        if dist <= tol:
            return max(0.0, 1.0 - dist / tol) if tol > 0 else 1.0
        return -(dist / tol) if tol > 0 else -1.0

    ordered = sorted(within3, key=lambda x: x[1])
    if not ordered:
        return []
    if ordered[0][1] <= CONFIDENT_DIST:
        ordered = ordered[:1]
    return [(p, score_of(d)) for p, d in ordered]


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

    완료 후 ``failed_set`` 속성으로 매치 실패(tolerance×3 초과) ref 목록 조회.
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
        self.failed_set: Set[Tuple[str, Path]] = set()

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

        # ── 좌표 일괄 프리패치 ──────────────────────────────────────────
        self.signals.phase.emit("좌표 파싱 중...")
        all_paths: List[Path] = []
        for _slot, refs, vals in self._tasks:
            for r in refs:
                all_paths.append(r.path)
            for v in vals:
                all_paths.append(v.path)
        coord_cache: Dict[Path, object] = _resolve_batch(all_paths)

        # ── 좌표 없는 경우 조기 종료 ────────────────────────────────────
        total_refs = sum(len(refs) for _, refs, _ in self._tasks)
        coords_ok = sum(
            1 for _, refs, _ in self._tasks
            for r in refs if coord_cache.get(r.path) is not None
        )
        if total_refs > 0 and coords_ok == 0:
            from .. import i18n
            self.signals.failed.emit(i18n.KO.COORD_NO_DATA_MSG)
            return

        total_pairs = sum(len(r) * len(v) for _, r, v in self._tasks)
        done_pairs = 0
        tol = self._tolerance
        tol3 = tol * 3.0
        self.signals.phase.emit("좌표 매칭 중")

        for slot_idx, (slot, refs, vals) in enumerate(self._tasks):
            if self._stop.is_set():
                break

            # val 좌표 캐시 — (col, row) → [(item, x, y), ...]
            val_coord_map: Dict[Tuple[int, int], List[Tuple[ImageItem, float, float]]] = {}
            for v in vals:
                coord = coord_cache.get(v.path)
                if coord is not None:
                    key = (coord.col, coord.row)
                    val_coord_map.setdefault(key, []).append((v, coord.x, coord.y))

            # (col,row) 그룹별 numpy 배열 미리 구성 (벡터화 최적화)
            if _HAS_NUMPY:
                val_np: Dict[Tuple[int, int], object] = {}
                for key, entries in val_coord_map.items():
                    val_np[key] = _np.array(
                        [(vx, vy) for _, vx, vy in entries], dtype=_np.float64
                    )
            else:
                val_np = {}

            fallback_refs: List[ImageItem] = []

            for ref in refs:
                if self._stop.is_set():
                    break

                ref_coord = coord_cache.get(ref.path)

                if ref_coord is None:
                    fallback_refs.append(ref)
                    done_pairs += len(vals)
                    self.signals.progress.emit(done_pairs, total_pairs)
                    continue

                candidates = val_coord_map.get((ref_coord.col, ref_coord.row), [])

                if not candidates:
                    # 같은 col/row 자체가 없으면 매치 실패
                    self.failed_set.add((slot, ref.path))
                    self._results[(slot, ref.path)] = []
                else:
                    # tol×3 이내 후보를 (path, dist) 로 모은다(numpy·비numpy 동일).
                    within3: List[Tuple[Path, float]] = []

                    # ── 거리 계산 ──────────────────────────────────────
                    if _HAS_NUMPY and (key := (ref_coord.col, ref_coord.row)) in val_np:
                        arr = val_np[key]
                        dists = _np.hypot(
                            arr[:, 0] - ref_coord.x,
                            arr[:, 1] - ref_coord.y,
                        )
                        for i, (v_item, _vx, _vy) in enumerate(candidates):
                            dist = float(dists[i])
                            if dist <= tol3:
                                within3.append((v_item.path, dist))
                    else:
                        for v_item, vx, vy in candidates:
                            dist = math.sqrt(
                                (ref_coord.x - vx) ** 2 + (ref_coord.y - vy) ** 2
                            )
                            if dist <= tol3:
                                within3.append((v_item.path, dist))

                    if within3:
                        self._results[(slot, ref.path)] = \
                            _select_coord_candidates(within3, tol)
                    else:
                        # 3배 초과 — 매치 실패
                        self.failed_set.add((slot, ref.path))
                        self._results[(slot, ref.path)] = []

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
