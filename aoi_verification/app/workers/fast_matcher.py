"""고속 모드 — 임베딩 + hnswlib ANN 기반 매칭 워커.

대용량(슬롯당 val 수천~수만)에서 전수 SSIM 비교(O(N×M))를 피하기 위해:
1. 슬롯의 ref+val 임베딩을 한 번 추출(GPU/OpenVINO 가속 가능) → 디스크 캐시.
2. val 임베딩으로 슬롯별 ANN 인덱스 구축.
3. ref 임베딩으로 top-K 후보만 추린 뒤 기존 ``pipeline.score()`` 로 정밀 재정렬.

``embedder`` (torch) 또는 ``hnswlib`` 가 없으면 호출자(match_page)가 기본
모드로 폴백한다.  여기서는 가용성 헬퍼만 노출.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..models.slot import ImageItem
from ..similarity import embedding_index as _ann
from ..similarity import pipeline as _pipeline
from ..utils import cache


def is_available() -> bool:
    """고속 모드 가용 = 임베딩(embedder) + hnswlib 둘 다 사용 가능."""
    if not _ann.is_available():
        return False
    try:
        from ..learning import embedder as _emb
        return _emb.is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 임베딩 (디스크 캐시 경유)
# ---------------------------------------------------------------------------
def _emb_cache_path(src: Path, model: str, cfg_extra: str) -> Path:
    return cache.cache_path(src, "feature", extra=f"emb-{model}-{cfg_extra}")


def compute_slot_embeddings(items: List[ImageItem],
                            *, cfg=None) -> Dict[Path, np.ndarray]:
    """슬롯 이미지들의 임베딩을 {path: vec(L2 정규화)} 로 반환.

    디스크 캐시 히트는 즉시 로드, 미스만 ``embedder.compute_embeddings``
    (force_backbone — 학습 모델 없이 raw backbone, OpenVINO/GPU 가속) 로 계산
    후 캐시.  embedder 가 빈 dict 를 주면(가속/torch 없음) 빈 dict 반환 →
    호출자 폴백.
    """
    from ..learning import embedder as _emb
    try:
        model = _emb.get_active_mode()
    except Exception:
        model = "basic"
    cfg_extra = cfg.cache_extra() if cfg is not None else ""

    out: Dict[Path, np.ndarray] = {}
    missing: List[Path] = []
    for it in items:
        p = Path(it.path)
        cp = _emb_cache_path(p, model, cfg_extra)
        if cp.exists() and cp.stat().st_size > 0:
            try:
                out[p] = np.load(str(cp))["emb"]
                continue
            except Exception:
                pass
        missing.append(p)

    if missing:
        try:
            computed = _emb.compute_embeddings(
                missing, force_backbone=True, cfg=cfg,
            )
        except Exception:
            computed = {}
        for p, vec in computed.items():
            v = np.asarray(vec, dtype=np.float32)
            out[Path(p)] = v
            try:
                cp = _emb_cache_path(Path(p), model, cfg_extra)
                cp.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(str(cp), emb=v)
            except Exception:
                pass
    return out


# ---------------------------------------------------------------------------
# 슬롯 인덱스 캐시 — 활성 + lookahead 만 유지 (메모리 규율)
# ---------------------------------------------------------------------------
class SlotIndexCache:
    """슬롯명 → (EmbeddingIndex, 라벨순 val 경로, ref 임베딩 dict)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: Dict[str, Tuple[object, List[Path], Dict[Path, np.ndarray]]] = {}

    def put(self, slot: str, index, val_paths: List[Path],
            ref_emb: Dict[Path, np.ndarray]) -> None:
        with self._lock:
            self._slots[slot] = (index, list(val_paths), dict(ref_emb))

    def get(self, slot: str):
        with self._lock:
            return self._slots.get(slot)

    def has(self, slot: str) -> bool:
        with self._lock:
            return slot in self._slots

    def set_active(self, slot: str, keep: Optional[set] = None) -> None:
        with self._lock:
            protect = {slot} | (keep or set())
            for k in list(self._slots.keys()):
                if k not in protect:
                    del self._slots[k]

    def clear(self) -> None:
        with self._lock:
            self._slots.clear()


# ---------------------------------------------------------------------------
# 인덱스 빌드 워커 (슬롯 단위 스트리밍 — SlotPrecomputeWorker 와 동일 시그널)
# ---------------------------------------------------------------------------
class _FastSignals(QObject):
    progress = pyqtSignal(int, int)            # done_slots, total_slots
    slot_finished = pyqtSignal(str, int, int)  # slot, idx(1-base), total
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class FastIndexWorker(QThread):
    """슬롯별 임베딩 추출 + ANN 인덱스 구축.  슬롯 1 개 끝날 때마다
    ``slot_finished`` → match_page 가 그 슬롯의 ref 매칭을 시작할 수 있다.
    """

    def __init__(self,
                 tasks: List[Tuple[str, List[ImageItem], List[ImageItem]]],
                 index_cache: SlotIndexCache,
                 *,
                 cfg=None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tasks = [(s, list(r), list(v)) for s, r, v in tasks]
        self._index_cache = index_cache
        self._cfg = cfg
        self._stop = False
        self.signals = _FastSignals()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        try:
            total = len(self._tasks)
            for idx, (slot, refs, vals) in enumerate(self._tasks, start=1):
                if self._stop:
                    return
                # val 임베딩 → 인덱스, ref 임베딩 보관.
                val_emb = compute_slot_embeddings(vals, cfg=self._cfg)
                if self._stop:
                    return
                built = _ann.build_from(val_emb)
                ref_emb = compute_slot_embeddings(refs, cfg=self._cfg)
                if built is not None:
                    index, val_paths = built
                    self._index_cache.put(slot, index, val_paths, ref_emb)
                # built=None(임베딩 0) 이면 그 슬롯은 match_page 가 폴백.
                self.signals.progress.emit(idx, total)
                self.signals.slot_finished.emit(slot, idx, total)
            self.signals.finished.emit()
        except Exception as exc:                # pragma: no cover - 방어
            self.signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# 단일 ref 매칭 워커 — top-K 질의 + 정밀 재정렬
# ---------------------------------------------------------------------------
class FastMatchSignals(QObject):
    done = pyqtSignal(list)        # list[Candidate]
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)


class FastMatchWorker(QThread):
    """ref 임베딩으로 슬롯 인덱스에서 top-K 추출 → ``pipeline.score()`` 재정렬."""

    def __init__(self,
                 ref_item: ImageItem,
                 index_entry: Tuple[object, List[Path], Dict[Path, np.ndarray]],
                 val_items: List[ImageItem],
                 *,
                 threshold: float,
                 top_k: int = 50,
                 cfg=None,
                 slot_cache=None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._ref = ref_item
        self._index, self._val_paths, self._ref_emb = index_entry
        self._val_by_path = {Path(v.path): v for v in val_items}
        self._threshold = float(threshold)
        self._top_k = int(top_k)
        self._cfg = cfg
        self._slot_cache = slot_cache
        self._stop = False
        self.signals = FastMatchSignals()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        from ..workers.matcher import Candidate
        try:
            rv = self._ref_emb.get(Path(self._ref.path))
            if rv is None:
                got = compute_slot_embeddings([self._ref], cfg=self._cfg)
                rv = got.get(Path(self._ref.path))
            if rv is None:
                # 임베딩 실패 → 빈 결과(호출자 폴백 판단).
                self.signals.done.emit([])
                return
            hits = self._index.query(rv, self._top_k)
            ref_feat = _pipeline.extract(self._ref.path, cfg=self._cfg)
            slot_feats = (self._slot_cache.get_features(self._ref.slot)
                          if self._slot_cache is not None else None) or {}
            out: List[Candidate] = []
            total = len(hits)
            for i, (label, _sim) in enumerate(hits, start=1):
                if self._stop:
                    break
                if label < 0 or label >= len(self._val_paths):
                    continue
                vpath = self._val_paths[label]
                vitem = self._val_by_path.get(vpath)
                if vitem is None:
                    continue
                vf = slot_feats.get(vpath)
                if vf is None:
                    vf = _pipeline.extract(vpath, cfg=self._cfg)
                s = _pipeline.score(ref_feat, vf)
                if s >= self._threshold:
                    out.append(Candidate(item=vitem, score=float(s)))
                self.signals.progress.emit(i, total)
            out.sort(key=lambda c: c.score, reverse=True)
            self.signals.done.emit(out)
        except Exception as exc:                # pragma: no cover - 방어
            self.signals.failed.emit(str(exc))
