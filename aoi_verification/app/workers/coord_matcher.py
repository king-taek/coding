"""좌표 기반 매칭 스케줄러 (v2).

이미지 유사도를 계산하지 않고 defect 좌표(col/row + x/y µm)로 직접 매칭한다.

매칭 규칙:
    1. col/row 가 **±1 이내**면 후보로 인정 (정답 도구 AOI Data Viewer VBA
       ``Module_Compare.AOIMapCompare`` 와 동일: ``Abs(col차)<=1 And Abs(row차)<=1``).
       KLA↔Camtek 처럼 두 장비의 die 인덱스가 1 어긋날 수 있어, 정확 일치만 하면
       매칭이 전멸한다(근거: ``dev/좌표 확인`` 샘플).
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
from .. import i18n
from ..models.slot import ImageItem
from .matcher import score_ref_classical
from .. import config as _config


# 좌표 차이가 이 값(좌표 단위, µm) 이하이면 '거의 정확히 일치'로 보고 후보 1장만
# 보여준다. 초과 시 tol×3 이내 후보를 모두 차순위로 노출해 사용자가 직접 고른다.
# die 가 작은 디바이스는 tol 을 20 µm 근처까지 낮춰 쓰게 되는데, 그때 이 값이 tol 을
# 넘으면 **모든 매치가 '확정'** 이 돼 차순위가 사라진다 → tol 의 절반으로 묶는다.
# 기본 tol(200)에서는 20 그대로다 — 클램프는 tol < 40 일 때만 발동한다.
CONFIDENT_DIST = 20.0


def _confident_dist(tol: float) -> float:
    """'거의 정확히 일치' 판정 반경 — 절대 20 µm 이되 tol 의 절반을 넘지 않는다."""
    return min(CONFIDENT_DIST, tol * 0.5) if tol > 0 else CONFIDENT_DIST


def _select_coord_candidates(
    within3: List[Tuple[Path, float]], tol: float
) -> List[Tuple[Path, float]]:
    """tol×3 이내 후보 ``(path, dist)`` 목록에서 검토 화면에 보여줄 후보를
    ``(path, score)`` 로 환산해 반환한다(거리 오름차순 = 점수 내림차순).

    · 최소 거리 ≤ :func:`_confident_dist`  → 가장 가까운 1장만 (확정에 가까움).
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
    if ordered[0][1] <= _confident_dist(tol):
        ordered = ordered[:1]
    return [(p, score_of(d)) for p, d in ordered]


def _match_neighbors(
    ref_x: float, ref_y: float, ref_col: int, ref_row: int,
    val_coord_map: Dict[Tuple[int, int], List[Tuple[Path, float, float]]],
    tol: float,
) -> List[Tuple[Path, float]]:
    """ref 결함 1개에 대해 **(col,row) ±1 이웃 9칸**의 val 후보를 모아 die-내부 (x,y)
    거리로 매칭, ``(path, score)`` 목록을 반환한다(비었으면 매치 실패).

    정답 도구(AOI Data Viewer VBA ``Module_Compare.AOIMapCompare``)가 die 인덱스를
    ``Abs(col차)<=1 And Abs(row차)<=1`` 로 ±1 허용하므로 그대로 따른다(정확 일치만 하면
    KLA↔Camtek 처럼 한쪽 인덱스가 1 어긋날 때 매칭이 전멸한다).  거리 tol·후보 선택은
    기존 :func:`_select_coord_candidates` 를 재사용한다."""
    tol3 = tol * 3.0 if tol > 0 else 0.0
    within3: List[Tuple[Path, float]] = []
    for dc in (-1, 0, 1):
        for dr in (-1, 0, 1):
            for vpath, vx, vy in val_coord_map.get((ref_col + dc, ref_row + dr), ()):
                dist = math.hypot(ref_x - vx, ref_y - vy)
                if dist <= tol3:
                    within3.append((vpath, dist))
    return _select_coord_candidates(within3, tol)


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

        _dflt = _config.DEFAULT_COORD_TOLERANCE
        tol = getattr(cfg, "coord_tolerance", _dflt) if cfg is not None else _dflt
        self._tolerance = float(tol) if tol and tol > 0.0 else _dflt

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
        self.signals.phase.emit(i18n.KO.PHASE_COORD_PARSE)
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
            self.signals.failed.emit(i18n.KO.COORD_NO_DATA_MSG)
            return

        total_pairs = sum(len(r) * len(v) for _, r, v in self._tasks)
        done_pairs = 0
        tol = self._tolerance
        self.signals.phase.emit(i18n.KO.PHASE_COORD)

        for slot_idx, (slot, refs, vals) in enumerate(self._tasks):
            if self._stop.is_set():
                break

            # val 좌표 캐시 — (col, row) → [(path, x, y), ...]
            val_coord_map: Dict[Tuple[int, int], List[Tuple[Path, float, float]]] = {}
            for v in vals:
                coord = coord_cache.get(v.path)
                if coord is not None:
                    key = (coord.col, coord.row)
                    val_coord_map.setdefault(key, []).append((v.path, coord.x, coord.y))

            fallback_refs: List[ImageItem] = []

            for ref in refs:
                if self._stop.is_set():
                    break

                ref_coord = coord_cache.get(ref.path)

                if ref_coord is None:
                    # ★ 여기서 진행분을 **미리 빼지 않는다**.  폴백(유사도 계산)은 이
                    #   슬롯 루프가 끝난 뒤에야 도는데, 그 몫을 앞당겨 보고하면 바가
                    #   끝까지 차오른 채 "좌표 매칭 중" 문구로 수십 초~수 분 멈춰 보인다
                    #   — 실제로는 가장 비싼 작업이 그 뒤에서 돌고 있었다.
                    fallback_refs.append(ref)
                    continue

                # (col,row) ±1 이웃 후보를 모아 die-내부 거리로 매칭.
                result = _match_neighbors(
                    ref_coord.x, ref_coord.y, ref_coord.col, ref_coord.row,
                    val_coord_map, tol,
                )
                self._results[(slot, ref.path)] = result
                if not result:
                    self.failed_set.add((slot, ref.path))

                done_pairs += len(vals)
                self.signals.progress.emit(done_pairs, total_pairs)

            # 폴백 — 좌표 없는 ref 를 고전 유사도로 처리.
            # ★ 단계 라벨을 바꾼다.  여기서 도는 것은 좌표 매칭이 아니라 유사도 계산이고,
            #   슬롯당 후보 수만큼 걸리므로 사용자가 '왜 안 끝나지' 를 알아야 한다.
            if fallback_refs:
                self.signals.phase.emit(i18n.KO.PHASE_SCORING)
            for ref in fallback_refs:
                if self._stop.is_set():
                    break
                base = done_pairs

                def _on_pair(idx: int, _total: int, _b=base) -> None:
                    # emit 폭주를 막는다 — 후보 수백 장이면 tick 이 그만큼 나온다.
                    if idx % 25 == 0:
                        self.signals.progress.emit(min(_b + idx, total_pairs),
                                                   total_pairs)

                cands = score_ref_classical(
                    ref, vals, threshold=0.0, cfg=self._cfg,
                    progress_cb=_on_pair,
                    stop_cb=self._stop.is_set,
                )
                self._results[(slot, ref.path)] = [
                    (c.item.path, float(c.score)) for c in cands
                ]
                done_pairs += len(vals)
                self.signals.progress.emit(min(done_pairs, total_pairs), total_pairs)
            if fallback_refs and not self._stop.is_set():
                self.signals.phase.emit(i18n.KO.PHASE_COORD)   # 다음 슬롯을 위해 되돌린다

            self.signals.slot_finished.emit(slot, slot_idx + 1, total_slots)

        self.signals.finished.emit()
