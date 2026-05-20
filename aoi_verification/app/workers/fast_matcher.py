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
    """고속 모드 가용 여부.

    경량 디스크립터(중심 ROI gray → 32×32 정규화 벡터)만 사용하므로 torch /
    모델 다운로드 / hnswlib 가 전혀 필요 없다.  NumPy·OpenCV·Pillow(핵심
    의존성)만 있으면 되어 오프라인/제한 환경에서도 항상 동작한다."""
    return _ann.is_available()             # NumPy 폴백 → 항상 True


# ---------------------------------------------------------------------------
# 경량 디스크립터 (디스크 캐시 경유) — torch/CNN 불필요
# ---------------------------------------------------------------------------
_DESC_PX = 32          # 디스크립터 그리드 — 32×32 = 1024 차원

# 디스크립터 계산용 ROI 디코드 크기 (작게 → 빠름).  유사도 정밀 비교(SSIM)는
# 별도로 pipeline.extract 가 원래 해상도로 수행하므로 여기선 작아도 무방.
_DESC_DECODE_PX = 96


def _descriptor_from_gray(gray: np.ndarray) -> np.ndarray:
    """중심 ROI gray → 32×32 축소 → 평균 제거 → L2 정규화 1-D 벡터.

    near-identical 검사 사진의 거시 구조를 잘 포착하면서 매우 빠르다 (행렬곱
    cosine 검색용 후보 추림).  밝기 차이에 강인하도록 평균을 뺀다."""
    try:
        import cv2
        small = cv2.resize(gray, (_DESC_PX, _DESC_PX), interpolation=cv2.INTER_AREA)
    except Exception:
        from PIL import Image
        small = np.asarray(
            Image.fromarray(gray).resize((_DESC_PX, _DESC_PX)), dtype=np.uint8,
        )
    v = small.astype(np.float32).reshape(-1)
    v -= float(v.mean())
    n = float(np.linalg.norm(v)) + 1e-9
    return (v / n).astype(np.float32)


def _desc_cache_path(src: Path, cfg_extra: str) -> Path:
    return cache.cache_path(src, "feature", extra=f"desc{_DESC_PX}-{cfg_extra}")


def compute_slot_embeddings(items: List[ImageItem],
                            *, cfg=None) -> Dict[Path, np.ndarray]:
    """슬롯 이미지들의 경량 디스크립터를 {path: vec(L2 정규화)} 로 반환.

    CNN/torch/모델 다운로드 없이 중심 ROI gray 를 축소·정규화한 벡터를 쓴다.
    cfg(중앙20%/KLA/강화)가 디스크립터에도 반영된다.  결과는 디스크 캐시되어
    재실행이 빠르다.
    """
    from ..utils import image_io
    out: Dict[Path, np.ndarray] = {}
    for it in items:
        p = Path(it.path)
        side = getattr(it, "side", None)
        cfg_extra = cfg.cache_extra(side) if cfg is not None else ""
        cp = _desc_cache_path(p, cfg_extra)
        if cp.exists() and cp.stat().st_size > 0:
            try:
                out[p] = np.load(str(cp))["d"]
                continue
            except Exception:
                pass
        try:
            gray = image_io.center_roi_gray(p, cfg=cfg, side=side,
                                            long_edge=_DESC_DECODE_PX)
            v = _descriptor_from_gray(gray)
        except Exception:
            continue
        out[p] = v
        try:
            cp.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(str(cp), d=v)
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# 단일 ref 재정렬 — top-K 후보를 정밀 score() 로 재정렬 (공용)
# ---------------------------------------------------------------------------
def rerank_ref(ref_item: ImageItem,
               index_entry: Tuple[object, List[Path], Dict[Path, np.ndarray]],
               *, top_k: int, cfg=None) -> List[Tuple[Path, float]]:
    """ref 디스크립터로 인덱스에서 top-K 후보를 뽑아 ``pipeline.score()`` 로
    정밀 재정렬.  반환: ``[(val_path, score), ...]`` 점수 내림차순."""
    index, val_paths, ref_emb = index_entry
    rv = ref_emb.get(Path(ref_item.path)) if ref_emb else None
    if rv is None:
        rv = compute_slot_embeddings([ref_item], cfg=cfg).get(Path(ref_item.path))
    if rv is None:
        return []
    hits = index.query(rv, top_k)
    ref_feat = _pipeline.extract(ref_item.path, cfg=cfg, side="ref")
    out: List[Tuple[Path, float]] = []
    for label, _sim in hits:
        if 0 <= label < len(val_paths):
            vpath = val_paths[label]
            try:
                vf = _pipeline.extract(vpath, cfg=cfg, side="val")
                out.append((vpath, float(_pipeline.score(ref_feat, vf))))
            except Exception:
                continue
    out.sort(key=lambda x: x[1], reverse=True)
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
    phase = pyqtSignal(str)                     # 현재 작업 단계 라벨 (#8)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class FastIndexWorker(QThread):
    """슬롯별 디스크립터 추출 + ANN 인덱스 구축.

    ``auto=True`` 면(자동 매칭) 각 슬롯 인덱스를 만든 직후 그 슬롯의 모든 ref 를
    미리 재정렬해 ``results[(slot, ref_path)] = [(val_path, score), ...]`` 에
    저장한다 — 매칭 단계는 즉시 결과만 읽으면 되므로 사진 한 장씩 백그라운드
    계산이 보이지 않고(#2/#3) 단일 진행 바로 끝난다.

    ``auto=False`` 면(수동) 인덱스만 만들고 슬롯 단위 스트리밍으로 진행한다.
    """

    def __init__(self,
                 tasks: List[Tuple[str, List[ImageItem], List[ImageItem]]],
                 index_cache: SlotIndexCache,
                 *,
                 cfg=None,
                 auto: bool = False,
                 top_k: int = 50,
                 results: Optional[dict] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tasks = [(s, list(r), list(v)) for s, r, v in tasks]
        self._index_cache = index_cache
        self._cfg = cfg
        self._auto = bool(auto)
        self._top_k = int(top_k)
        self._results = results if results is not None else {}
        self._stop = False
        self.signals = _FastSignals()

    def stop(self) -> None:
        self._stop = True

    _EMB_CHUNK = 64        # 임베딩 청크 크기 — 진행률 피드백 단위.

    def _embed_chunked(self, items, done_box, total_images):
        """``items`` 임베딩을 청크로 계산하며 이미지 단위 progress 를 emit.

        ``done_box`` 는 전역 누적 카운터를 담은 1-원소 리스트(가변 참조).
        """
        out = {}
        for i in range(0, len(items), self._EMB_CHUNK):
            if self._stop:
                return out
            chunk = items[i:i + self._EMB_CHUNK]
            out.update(compute_slot_embeddings(chunk, cfg=self._cfg))
            done_box[0] += len(chunk)
            self.signals.progress.emit(done_box[0], total_images)
        return out

    def run(self) -> None:        # type: ignore[override]
        try:
            total = len(self._tasks)
            # 진행 바 단위 = 디스크립터(이미지) + (자동이면) ref 재정렬.
            total_imgs = sum(len(r) + len(v) for _, r, v in self._tasks)
            total_units = max(1, total_imgs + (
                sum(len(r) for _, r, _ in self._tasks) if self._auto else 0))
            done_box = [0]
            from .. import i18n
            for idx, (slot, refs, vals) in enumerate(self._tasks, start=1):
                if self._stop:
                    return
                self.signals.phase.emit(i18n.KO.PHASE_FEATURE)
                val_emb = self._embed_chunked(vals, done_box, total_units)
                if self._stop:
                    return
                built = _ann.build_from(val_emb)
                ref_emb = self._embed_chunked(refs, done_box, total_units)
                if self._stop:
                    return
                if built is not None:
                    index, val_paths = built
                    self._index_cache.put(slot, index, val_paths, ref_emb)
                    if self._auto:
                        # 자동 모드 — 모든 ref 를 미리 재정렬해 결과 저장.
                        self.signals.phase.emit(i18n.KO.PHASE_SCORING)
                        entry = (index, val_paths, ref_emb)
                        for r in refs:
                            if self._stop:
                                return
                            self._results[(slot, Path(r.path))] = rerank_ref(
                                r, entry, top_k=self._top_k, cfg=self._cfg,
                            )
                            done_box[0] += 1
                            self.signals.progress.emit(done_box[0], total_units)
                elif self._auto:
                    # 인덱스 미생성(디스크립터 0) — 빈 결과로 진행 카운트만 맞춤.
                    for r in refs:
                        self._results[(slot, Path(r.path))] = []
                        done_box[0] += 1
                        self.signals.progress.emit(done_box[0], total_units)
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
            results = rerank_ref(
                self._ref, (self._index, self._val_paths, self._ref_emb),
                top_k=self._top_k, cfg=self._cfg,
            )
            out: List[Candidate] = []
            for vpath, s in results:
                if s >= self._threshold:
                    vitem = self._val_by_path.get(vpath)
                    if vitem is not None:
                        out.append(Candidate(item=vitem, score=float(s)))
            out.sort(key=lambda c: c.score, reverse=True)
            self.signals.done.emit(out)
        except Exception as exc:                # pragma: no cover - 방어
            self.signals.failed.emit(str(exc))
