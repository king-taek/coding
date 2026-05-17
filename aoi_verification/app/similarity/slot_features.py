"""Slot 단위 in-RAM 특징 / 점수 캐시 + 사전 계산 워커.

Stage 2 에서 한 슬롯의 모든 검증측 이미지 ``Feature`` 객체를 한 번만 추출하고,
같은 슬롯의 여러 reference 가 매칭될 때 디스크 재로드 없이 그대로 재사용한다.
나아가 (ref, val) 모든 쌍의 점수도 Stage 2 진입 시 미리 한 번에 계산해서
``SlotScoreCache`` 에 보관 → 매 reference 마다 점수 재계산 없이 즉시 응답.

설계 원칙:
- **per-image 디스크 캐시 (``feature_cache_dir`` 의 .npz) 는 그대로 사용**.
  이 모듈은 그 위에 ‘얼마 동안 RAM 에 들고 있을지’ 를 결정하는 매니저일 뿐이다.
- 메모리 규율을 위해 ‘활성 슬롯 1 개’ + 옵션으로 ‘미리 로드해둘 다음 슬롯 1 개’
  만 유지. 슬롯 변경 시 이전 슬롯의 dict 를 명시적으로 비워 RAM 을 빠르게
  돌려준다.
- thread-safe: ``threading.Lock`` 으로 보호.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..models.slot import ImageItem
from . import pipeline as _pipeline
from .pipeline import Feature


class SlotFeatureCache:
    """슬롯명 → ``{Path: Feature}`` 매핑. ``set_active`` 로 활성 슬롯만 유지."""

    def __init__(self, *, keep_lookahead: bool = True) -> None:
        self._lock = threading.Lock()
        self._slots: Dict[str, Dict[Path, Feature]] = {}
        self._active: Optional[str] = None
        self._lookahead: Optional[str] = None
        self._keep_lookahead = bool(keep_lookahead)

    # ------------------------------------------------------------------
    def active_slot(self) -> Optional[str]:
        return self._active

    def has(self, slot: str) -> bool:
        with self._lock:
            return slot in self._slots

    def get_features(self, slot: str) -> Optional[Dict[Path, Feature]]:
        with self._lock:
            d = self._slots.get(slot)
            return None if d is None else dict(d)

    def size(self) -> int:
        with self._lock:
            return sum(len(d) for d in self._slots.values())

    # ------------------------------------------------------------------
    def set_active(self, slot: str) -> None:
        """``slot`` 을 활성으로 표시. 활성 + (옵션) lookahead 외의 슬롯은 제거."""
        with self._lock:
            self._active = slot
            keep = {slot}
            if self._keep_lookahead and self._lookahead:
                keep.add(self._lookahead)
            for k in list(self._slots.keys()):
                if k not in keep:
                    del self._slots[k]

    def set_lookahead(self, slot: Optional[str]) -> None:
        """다음에 진입할 가능성이 높은 슬롯을 표시. 활성/lookahead 외 슬롯 제거."""
        with self._lock:
            self._lookahead = slot
            keep = {self._active or "", slot or ""}
            for k in list(self._slots.keys()):
                if k not in keep:
                    del self._slots[k]

    # ------------------------------------------------------------------
    def build(self, slot: str, items: Iterable[ImageItem]) -> Dict[Path, Feature]:
        """슬롯의 ``Feature`` 들을 추출(또는 캐시 로드) 해서 dict 로 반환·저장.

        이미 빌드된 슬롯은 그대로 반환한다 (idempotent). 항목이 추가됐다면
        새 path 만 추가 추출한다.
        """
        items_list = list(items)
        existing: Dict[Path, Feature] = {}
        with self._lock:
            existing = dict(self._slots.get(slot, {}))

        # 누락된 path 만 새로 추출 (디스크 캐시가 있다면 거의 무비용).
        to_build = [it.path for it in items_list if it.path not in existing]
        for p in to_build:
            try:
                feat = _pipeline.extract(p)
                existing[p] = feat
            except Exception:
                # 단일 이미지 실패는 무시 — 호출자가 빈 dict 로 처리.
                pass

        with self._lock:
            self._slots[slot] = existing
            # 만약 set_active 가 아직 호출되지 않았으면 이 슬롯을 활성으로 간주.
            if self._active is None:
                self._active = slot
        return dict(existing)

    # ------------------------------------------------------------------
    def clear(self) -> None:
        with self._lock:
            self._slots.clear()
            self._active = None
            self._lookahead = None

    def release(self, slot: str) -> None:
        """슬롯의 RAM features 를 즉시 폐기.

        점수 계산이 끝나 더는 features 가 필요 없을 때 (스트리밍 사전 계산
        워커가 다음 슬롯으로 넘어갈 때) 호출. 점수 캐시는 별도 객체에 남아
        있어 그대로 유지되고, RAM 만 비운다.
        """
        with self._lock:
            self._slots.pop(slot, None)
            if self._active == slot:
                self._active = None
            if self._lookahead == slot:
                self._lookahead = None

    def known_slots(self) -> List[str]:
        with self._lock:
            return list(self._slots.keys())


# ---------------------------------------------------------------------------
# 점수 캐시 — (slot, ref_path, val_path) → score
# ---------------------------------------------------------------------------
class SlotScoreCache:
    """Stage 2 에서 모든 reference 와 모든 검증 후보 사이의 유사도 점수를
    미리 계산해 보관. 매 reference 마다 점수를 다시 매길 필요 없음.

    메모리 비용: float 한 개 ≈ 32 bytes. 슬롯당 (refs × vals) entries 라서
    1000 쌍 ≈ 32 KB — 사실상 무시 가능.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scores: Dict[str, Dict[Tuple[Path, Path], float]] = {}

    def put(self, slot: str, ref_path: Path, val_path: Path, score: float) -> None:
        with self._lock:
            self._scores.setdefault(slot, {})[(ref_path, val_path)] = float(score)

    def has_pair(self, slot: str, ref_path: Path, val_path: Path) -> bool:
        with self._lock:
            return (slot in self._scores
                    and (ref_path, val_path) in self._scores[slot])

    def get_pair(self, slot: str, ref_path: Path, val_path: Path) -> Optional[float]:
        with self._lock:
            return self._scores.get(slot, {}).get((ref_path, val_path))

    def has_all_pairs(self,
                      slot: str,
                      ref_path: Path,
                      val_paths: Iterable[Path]) -> bool:
        """ref 와 주어진 모든 val 쌍 점수가 캐시에 있는지."""
        with self._lock:
            slot_scores = self._scores.get(slot)
            if not slot_scores:
                return False
            for v in val_paths:
                if (ref_path, v) not in slot_scores:
                    return False
            return True

    def has_slot(self, slot: str) -> bool:
        with self._lock:
            return slot in self._scores

    def clear_slot(self, slot: str) -> None:
        with self._lock:
            self._scores.pop(slot, None)

    def clear(self) -> None:
        with self._lock:
            self._scores.clear()

    def size(self) -> int:
        with self._lock:
            return sum(len(d) for d in self._scores.values())


# ---------------------------------------------------------------------------
# 사전 계산 워커 — 슬롯 단위 스트리밍 점수 계산
# ---------------------------------------------------------------------------
class _PrecomputeSignals(QObject):
    progress = pyqtSignal(int, int)            # done_pairs, total_pairs
    slot_finished = pyqtSignal(str, int, int)  # slot, idx (1-base), total_slots
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class SlotPrecomputeWorker(QThread):
    """주어진 슬롯들의 (ref, val) 쌍 점수를 슬롯 하나씩 계산해서
    ``SlotScoreCache`` 에 저장한다.

    슬롯 하나의 점수 계산이 끝날 때마다 ``slot_finished`` 시그널을 발생.
    ``release_after_slot=True`` 면 그 슬롯의 features 를 즉시 RAM 에서 폐기
    (점수만 남기고 메모리 회수) → 백그라운드에서 돌아도 메모리 사용 최소화.
    """

    def __init__(self,
                 tasks: List[Tuple[str, List[ImageItem], List[ImageItem]]],
                 slot_cache: SlotFeatureCache,
                 score_cache: SlotScoreCache,
                 *,
                 release_after_slot: bool = False,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # (slot 이름, ref ImageItem 리스트, val ImageItem 리스트)
        self._tasks = [
            (slot, list(refs), list(vals)) for slot, refs, vals in tasks
        ]
        self._slot_cache = slot_cache
        self._score_cache = score_cache
        self._release_after_slot = bool(release_after_slot)
        self._stop = False
        self.signals = _PrecomputeSignals()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        try:
            total = sum(len(r) * len(v) for _, r, v in self._tasks)
            total_slots = len(self._tasks)
            if total == 0:
                self.signals.finished.emit()
                return
            done = 0
            for slot_idx, (slot, refs, vals) in enumerate(self._tasks):
                if self._stop:
                    return
                if not refs or not vals:
                    self.signals.slot_finished.emit(
                        slot, slot_idx + 1, total_slots,
                    )
                    continue
                # 1) val features 빌드 (디스크 캐시 있으면 빠름)
                val_feats = self._slot_cache.build(slot, vals)
                # 2) ref features (sim.extract 가 디스크 캐시 자동 사용)
                ref_feats: Dict[Path, Feature] = {}
                for r in refs:
                    if self._stop:
                        return
                    try:
                        ref_feats[r.path] = _pipeline.extract(r.path)
                    except Exception:
                        pass
                # 3) 모든 (ref, val) 쌍 점수 — 정밀 score() 한 번 호출.
                for r in refs:
                    if self._stop:
                        return
                    rf = ref_feats.get(r.path)
                    for v in vals:
                        if self._stop:
                            return
                        vf = val_feats.get(v.path)
                        done += 1
                        if rf is None or vf is None:
                            continue
                        try:
                            s = _pipeline.score(rf, vf)
                        except Exception:
                            continue
                        self._score_cache.put(slot, r.path, v.path, s)
                        if done % 25 == 0:
                            self.signals.progress.emit(done, total)
                # 슬롯 단위로 진행률 + 슬롯 완료 emit.
                self.signals.progress.emit(done, total)
                self.signals.slot_finished.emit(
                    slot, slot_idx + 1, total_slots,
                )
                # 메모리 절약: 점수 계산이 끝났으니 features 는 더 이상
                # 필요 없음 (점수 캐시만 남으면 _launch_matcher 가 즉시 응답).
                if self._release_after_slot:
                    self._slot_cache.release(slot)
                    # ref features 도 같이 회수.
                    ref_feats.clear()
                    val_feats.clear()
            self.signals.finished.emit()
        except Exception as exc:        # pragma: no cover — 안전망
            self.signals.failed.emit(str(exc))
