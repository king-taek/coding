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

import gzip
import hashlib
import json
import os
import queue
import threading
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..models.slot import ImageItem
from . import pipeline as _pipeline
from .pipeline import Feature

# OpenCV 가 내부에서 multi-threading 하면 우리 ThreadPoolExecutor 와
# over-subscription 발생 → 오히려 느려짐.  외부에서만 병렬화하도록 끔.
try:
    import cv2 as _cv2
    _cv2.setNumThreads(1)
except Exception:
    pass


def _worker_count() -> int:
    """ThreadPoolExecutor 워커 수 — CPU 코어 -1 (UI 응답성 확보)."""
    return max(2, (os.cpu_count() or 2) - 1)


# ---------------------------------------------------------------------------
# 점수 디스크 영속 캐시 (#5B) — 옵션. 슬롯당 1 파일(gzip-JSON).
#
# 한 쌍의 점수는 (전처리 cfg, 엔진, active 모델, 가중치) 에 따라 달라지므로
# 이 모든 요소를 묶은 ``signature`` 로 파일을 분리한다.  개별 엔트리 키에는
# ref/val 의 절대경로 + mtime 을 박아두어, 사진이 바뀌면 키가 달라져 자동으로
# 무효화(재계산)된다 (utils/cache.py 와 동일한 패턴).
# ---------------------------------------------------------------------------
def _score_signature(cfg) -> str:
    """점수에 영향을 주는 모든 설정을 묶은 sha1 시그니처."""
    from .. import config as _config
    src = "|".join([
        getattr(cfg, "engine", "basic"),
        cfg.cache_extra("ref") if cfg is not None else "",
        cfg.cache_extra("val") if cfg is not None else "",
        # 재채점 항 선택·중앙가중도 점수를 바꾸므로 시그니처에 포함(캐시 분리).
        repr(sorted(getattr(cfg, "rerank_components", None) or [])) if cfg is not None else "",
        repr(float(getattr(cfg, "orb_center_weight", 0.0) or 0.0)) if cfg is not None else "",
        _pipeline._active_model_name(),
        repr(_config.CONFIG.similarity.normalized()),
    ])
    return hashlib.sha1(src.encode("utf-8")).hexdigest()


def _stat_mtime(path: Path, cache: Dict[Path, int]) -> int:
    m = cache.get(path)
    if m is None:
        try:
            m = int(Path(path).stat().st_mtime)
        except OSError:
            m = 0
        cache[path] = m
    return m


def _slot_score_file(slot: str, sig: str) -> Path:
    from ..utils import paths as _paths
    name = hashlib.sha1(slot.encode("utf-8")).hexdigest()
    return _paths.score_cache_dir() / f"{name}_{sig[:16]}.json.gz"


def _load_slot_scores(slot: str, sig: str) -> Dict[str, float]:
    """슬롯의 디스크 점수 맵 로드. 없거나 손상되면 빈 dict (손상 파일은 삭제)."""
    f = _slot_score_file(slot, sig)
    try:
        if not f.exists() or f.stat().st_size == 0:
            return {}
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()}
    except Exception:
        try:
            f.unlink()
        except OSError:
            pass
    return {}


def _save_slot_scores(slot: str, sig: str, mapping: Dict[str, float]) -> None:
    if not mapping:
        return
    f = _slot_score_file(slot, sig)
    tmp = f.parent / (f.name + ".tmp")
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(mapping, fh)
        tmp.replace(f)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


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
    def build(self, slot: str, items: Iterable[ImageItem],
              *, cfg=None) -> Dict[Path, Feature]:
        """슬롯의 ``Feature`` 들을 추출(또는 캐시 로드) 해서 dict 로 반환·저장.

        이미 빌드된 슬롯은 그대로 반환한다 (idempotent). 항목이 추가됐다면
        새 path 만 추가 추출한다.  ``cfg`` 는 강화/KLA 전처리를 extract 에 전달.
        """
        items_list = list(items)
        existing: Dict[Path, Feature] = {}
        with self._lock:
            existing = dict(self._slots.get(slot, {}))

        # 누락된 항목만 새로 추출 (디스크 캐시가 있다면 거의 무비용).
        # 검증측 특징 캐시이므로 side='val' (중앙 30% crop 의 side 별 적용).
        for it in items_list:
            if it.path in existing:
                continue
            try:
                existing[it.path] = _pipeline.extract(
                    it.path, cfg=cfg, side=getattr(it, "side", "val"),
                )
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

    메모리 비용: 한 항목 ≈ 수십~수백 bytes(tuple+Path 참조).  대용량(슬롯
    수백 개 × 수백만 쌍)에서 무제한 증가하면 GB 단위가 되므로 **슬롯 LRU
    상한**(``max_pairs``)을 둔다.  상한 초과 시 가장 오래 접근하지 않은
    슬롯부터 제거 — 제거된 슬롯이 다시 필요해지면 matcher 폴백이 재계산
    하므로 정확도는 유지되고 메모리만 절약된다 (#17).
    """

    def __init__(self, *, max_pairs: int = 3_000_000) -> None:
        self._lock = threading.Lock()
        self._scores: "OrderedDict[str, Dict[Tuple[Path, Path], float]]" = OrderedDict()
        self._max_pairs = int(max_pairs)
        self._total = 0

    def _evict_locked(self, protect: str) -> None:
        """상한 초과 시 LRU 슬롯 제거 (``protect`` 슬롯은 보존)."""
        while self._total > self._max_pairs and len(self._scores) > 1:
            # OrderedDict 앞쪽 = 가장 오래 접근한 슬롯.
            victim = next(iter(self._scores))
            if victim == protect:
                # 보호 슬롯이 앞에 있으면 뒤로 미루고 다음 후보 평가.
                self._scores.move_to_end(victim)
                victim2 = next(iter(self._scores))
                if victim2 == protect:
                    break
                victim = victim2
            d = self._scores.pop(victim, None)
            if d:
                self._total -= len(d)

    def put(self, slot: str, ref_path: Path, val_path: Path, score: float) -> None:
        with self._lock:
            d = self._scores.get(slot)
            if d is None:
                d = {}
                self._scores[slot] = d
            self._scores.move_to_end(slot)
            key = (ref_path, val_path)
            if key not in d:
                self._total += 1
            d[key] = float(score)
            if self._total > self._max_pairs:
                self._evict_locked(protect=slot)

    def has_pair(self, slot: str, ref_path: Path, val_path: Path) -> bool:
        with self._lock:
            return (slot in self._scores
                    and (ref_path, val_path) in self._scores[slot])

    def get_pair(self, slot: str, ref_path: Path, val_path: Path) -> Optional[float]:
        with self._lock:
            d = self._scores.get(slot)
            if d is None:
                return None
            self._scores.move_to_end(slot)        # LRU 갱신
            return d.get((ref_path, val_path))

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
            self._scores.move_to_end(slot)        # LRU 갱신
            return True

    def has_slot(self, slot: str) -> bool:
        with self._lock:
            return slot in self._scores

    def clear_slot(self, slot: str) -> None:
        with self._lock:
            d = self._scores.pop(slot, None)
            if d:
                self._total -= len(d)

    def clear(self) -> None:
        with self._lock:
            self._scores.clear()
            self._total = 0

    def size(self) -> int:
        with self._lock:
            return self._total


# ---------------------------------------------------------------------------
# 사전 계산 워커 — 슬롯 단위 스트리밍 점수 계산
# ---------------------------------------------------------------------------
class _PrecomputeSignals(QObject):
    progress = pyqtSignal(int, int)            # done_pairs, total_pairs
    slot_finished = pyqtSignal(str, int, int)  # slot, idx (1-base), total_slots
    phase = pyqtSignal(str)                    # 현재 작업 단계 라벨 (#8)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


@dataclass
class _SlotJob:
    """생산자(GPU)→소비자(CPU) 핸드오프 단위.

    생산자가 한 슬롯의 Feature 빌드 + CNN 임베딩 주입까지 끝낸 뒤 큐에 올린다.
    소비자는 이 Feature 들로 스코어링만 하므로 torch 호출이 없다(thread-safe).
    """
    slot_idx: int
    slot: str
    refs: List[ImageItem] = field(default_factory=list)
    vals: List[ImageItem] = field(default_factory=list)
    ref_feats: Dict[Path, Feature] = field(default_factory=dict)
    val_feats: Dict[Path, Feature] = field(default_factory=dict)
    empty: bool = False
    error: Optional[str] = None


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
                 cfg=None,
                 pipeline: bool = True,
                 lookahead: int = 1,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # (slot 이름, ref ImageItem 리스트, val ImageItem 리스트)
        self._tasks = [
            (slot, list(refs), list(vals)) for slot, refs, vals in tasks
        ]
        self._slot_cache = slot_cache
        self._score_cache = score_cache
        self._release_after_slot = bool(release_after_slot)
        self._cfg = cfg                 # 강화/KLA 전처리 설정 (extract 에 전달)
        self._pipeline_enabled = bool(pipeline)
        self._lookahead = max(1, int(lookahead))
        self._stop = False
        self.signals = _PrecomputeSignals()

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    def _should_pipeline(self) -> bool:
        """GPU 임베딩 ↔ CPU 스코어링을 겹칠 이득이 있을 때만 파이프라인 사용.

        가속기(Intel GPU/NPU·CUDA 등)가 있고 학습 모델(CNN 임베딩)이 활성일
        때만 겹칠 GPU 작업이 존재한다.  CPU-only/BASIC 모드는 producer 의 작업이
        CPU 라 스코어링과 경합만 하므로 기존 순차 경로가 안전·동등하다.
        """
        if not self._pipeline_enabled or len(self._tasks) <= 1:
            return False
        try:
            from ..learning import embedder as _emb
            return bool(
                _emb.has_accelerator()
                and _emb.is_available()
                and _emb.get_active_mode() != _emb.registry.BASIC
            )
        except Exception:
            return False

    def run(self) -> None:        # type: ignore[override]
        if self._should_pipeline():
            self._run_pipelined()
        else:
            self._run_sequential()

    # ------------------------------------------------------------------
    def _score_signature_or_blank(self) -> Tuple[bool, str]:
        """persist 여부 + 점수 시그니처 (sequential/pipeline 공용)."""
        persist = (bool(getattr(self._cfg, "persist_scores", False))
                   and not bool(getattr(self._cfg, "bench_no_cache", False)))
        return persist, (_score_signature(self._cfg) if persist else "")

    def _consume_slot(self, job: "_SlotJob", *, persist: bool, score_sig: str,
                      done: int, total: int, total_slots: int) -> int:
        """한 슬롯의 CPU 스코어링 + 디스크 캐시 + 시그널.  새 ``done`` 반환.

        sequential/pipeline 양쪽이 공유하는 소비자 측 로직 (torch 호출 없음)."""
        from .. import i18n
        slot, refs, vals = job.slot, job.refs, job.vals
        self.signals.phase.emit(i18n.KO.PHASE_SCORING)
        disk_scores = (_load_slot_scores(slot, score_sig) if persist else None)
        out_map = self._score_pairs_parallel(
            slot, refs, vals, job.ref_feats, job.val_feats,
            done_offset=done, total=total, disk_scores=disk_scores,
        )
        done += len(refs) * len(vals)
        if persist and out_map:
            _save_slot_scores(slot, score_sig, out_map)
        self.signals.progress.emit(done, total)
        self.signals.slot_finished.emit(slot, job.slot_idx + 1, total_slots)
        if self._release_after_slot:
            self._slot_cache.release(slot)
            job.ref_feats.clear()
            job.val_feats.clear()
        return done

    # ------------------------------------------------------------------
    def _run_sequential(self) -> None:
        """기존(검증된) 순차 경로 — 슬롯마다 임베딩 완료 후 스코어링."""
        try:
            total = sum(len(r) * len(v) for _, r, v in self._tasks)
            total_slots = len(self._tasks)
            if total == 0:
                self.signals.finished.emit()
                return
            # 점수 디스크 캐시 옵션 (#5B) — active 모델은 실행 중 불변이므로
            # 시그니처는 한 번만 계산한다.
            persist = (bool(getattr(self._cfg, "persist_scores", False))
                       and not bool(getattr(self._cfg, "bench_no_cache", False)))
            score_sig = _score_signature(self._cfg) if persist else ""
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
                from .. import i18n
                # 특징 분석 단계도 진행률을 표시해 '0 에서 멈춘 것처럼' 보이지 않게
                # 한다(#2) — 이미지 장수(ref+val) 기준 카운트.
                self.signals.phase.emit(i18n.KO.PHASE_FEATURE)
                feat_total = len(refs) + len(vals)
                self.signals.progress.emit(0, feat_total)
                val_feats = self._slot_cache.build(slot, vals, cfg=self._cfg)
                self.signals.progress.emit(len(vals), feat_total)
                # 2) ref features (sim.extract 가 디스크 캐시 자동 사용)
                ref_feats: Dict[Path, Feature] = {}
                for ri, r in enumerate(refs, start=1):
                    if self._stop:
                        return
                    try:
                        ref_feats[r.path] = _pipeline.extract(
                            r.path, cfg=self._cfg, side="ref",
                        )
                    except Exception:
                        pass
                    self.signals.progress.emit(len(vals) + ri, feat_total)

                # 2.5) CNN 임베딩 사전 배치 (#5 — GPU 가속 + thread-safety).
                # score() 안에서 lazy 계산 + Feature.cnn 변형이 일어나면
                # ThreadPoolExecutor 환경에서 race condition / torch 비-스레드
                # 안전성에 걸린다.  슬롯 단위로 한 번에 GPU 배치 추론 → score()
                # 는 캐시 hit 만 하게 한다.
                self._prefetch_cnn_embeddings(ref_feats, val_feats)

                # 3) 모든 (ref, val) 쌍 점수 — ThreadPoolExecutor 로 병렬 (#5).
                # _pipeline.score 의 cv2/numpy/skimage 호출은 GIL 을 잘 양보
                # 하므로 thread 가 실제 병렬 처리 가능.  cv2 내부 multi-thread
                # 는 over-subscription 회피 위해 외부에서만 병렬화한다.
                self.signals.phase.emit(i18n.KO.PHASE_SCORING)
                # 디스크 캐시가 켜져 있으면 슬롯의 기존 점수를 불러와 재계산을
                # 건너뛰고, 새로 계산한 점수까지 합쳐 다시 저장한다 (#5B).
                disk_scores = (_load_slot_scores(slot, score_sig)
                               if persist else None)
                out_map = self._score_pairs_parallel(
                    slot, refs, vals, ref_feats, val_feats,
                    done_offset=done, total=total, disk_scores=disk_scores,
                )
                done += len(refs) * len(vals)
                if persist and out_map:
                    _save_slot_scores(slot, score_sig, out_map)
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

    # ------------------------------------------------------------------
    # 파이프라인 경로 — GPU 임베딩(생산자) ↔ CPU 스코어링(소비자) 겹침
    # ------------------------------------------------------------------
    def _run_pipelined(self) -> None:
        """생산자 스레드가 다음 슬롯을 미리 GPU 임베딩하고, 소비자(이 스레드)가
        이미 임베딩된 슬롯을 CPU 로 스코어링한다.  슬롯 처리/시그널 순서는 순차
        경로와 동일하게 유지 → 다운스트림 가정 불변, 점수 결과 bit-identical."""
        try:
            total = sum(len(r) * len(v) for _, r, v in self._tasks)
            total_slots = len(self._tasks)
            if total == 0:
                self.signals.finished.emit()
                return
            persist, score_sig = self._score_signature_or_blank()

            q: "queue.Queue[Optional[_SlotJob]]" = queue.Queue(
                maxsize=self._lookahead
            )
            prod = threading.Thread(target=self._producer, args=(q,),
                                    daemon=True)
            prod.start()

            done = 0
            try:
                while not self._stop:
                    try:
                        job = q.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if job is None:               # sentinel — 생산자 종료
                        break
                    if job.error is not None:
                        self.signals.failed.emit(job.error)
                        return
                    if job.empty:
                        self.signals.slot_finished.emit(
                            job.slot, job.slot_idx + 1, total_slots,
                        )
                        continue
                    done = self._consume_slot(
                        job, persist=persist, score_sig=score_sig,
                        done=done, total=total, total_slots=total_slots,
                    )
            finally:
                prod.join(timeout=2.0)

            if not self._stop:
                self.signals.finished.emit()
        except Exception as exc:        # pragma: no cover — 안전망
            self.signals.failed.emit(str(exc))

    def _producer(self, q: "queue.Queue[Optional[_SlotJob]]") -> None:
        """슬롯을 순서대로 Feature 빌드 + CNN 임베딩(유일한 GPU 호출)하여 큐로.

        torch/OpenVINO 는 이 단일 스레드에서만 호출된다 → thread-safe.  진행률
        시그널은 첫 슬롯(차단 오버레이 표시 중)에서만 emit 해 소비자의 전역
        스코어링 진행률과 스케일이 섞이지 않게 한다.
        """
        from .. import i18n
        try:
            for slot_idx, (slot, refs, vals) in enumerate(self._tasks):
                if self._stop:
                    break
                if not refs or not vals:
                    self._put(q, _SlotJob(slot_idx, slot, empty=True))
                    continue
                first = (slot_idx == 0)
                if first:
                    self.signals.phase.emit(i18n.KO.PHASE_FEATURE)
                    feat_total = len(refs) + len(vals)
                    self.signals.progress.emit(0, feat_total)
                # 1) val features 빌드 (디스크 캐시 있으면 빠름)
                val_feats = self._slot_cache.build(slot, vals, cfg=self._cfg)
                if first:
                    self.signals.progress.emit(len(vals), feat_total)
                if self._stop:
                    break
                # 2) ref features (sim.extract 가 디스크 캐시 자동 사용)
                ref_feats: Dict[Path, Feature] = {}
                for ri, r in enumerate(refs, start=1):
                    if self._stop:
                        break
                    try:
                        ref_feats[r.path] = _pipeline.extract(
                            r.path, cfg=self._cfg, side="ref",
                        )
                    except Exception:
                        pass
                    if first:
                        self.signals.progress.emit(len(vals) + ri, feat_total)
                if self._stop:
                    break
                # 2.5) CNN 임베딩 사전 배치 (#5 — GPU 가속 + thread-safety).
                self._prefetch_cnn_embeddings(ref_feats, val_feats)
                # 3) 임베딩 완료된 슬롯을 소비자에게 넘김 (backpressure 로 RAM 경계).
                self._put(q, _SlotJob(
                    slot_idx, slot, refs, vals, ref_feats, val_feats,
                ))
        except Exception as exc:        # pragma: no cover — 안전망
            self._put(q, _SlotJob(-1, "", error=str(exc)))
        finally:
            self._put(q, None)          # sentinel

    def _put(self, q: "queue.Queue", item) -> None:
        """``_stop`` 을 존중하는 인터럽트 가능 put (소비자가 사라져도 안 멈춤)."""
        while not self._stop:
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    # ------------------------------------------------------------------
    def _prefetch_cnn_embeddings(self,
                                  ref_feats: Dict[Path, Feature],
                                  val_feats: Dict[Path, Feature]) -> None:
        """CNN 활성 모드라면 슬롯의 모든 이미지에 대한 임베딩을 한 번에 GPU
        배치 추론으로 계산 → Feature.cnn 에 주입 (#5).  병렬 score() 단계에서
        torch 호출 / Feature 변형을 모두 없애서 thread-safe 보장."""
        try:
            from ..learning import embedder as _emb
        except Exception:
            return
        if not _emb.is_available():
            return
        mode = _emb.get_active_mode()
        if mode == _emb.registry.BASIC:
            return
        # 캐시에 없는 이미지만 계산.
        paths_needed = []
        for d in (ref_feats, val_feats):
            for p, f in d.items():
                if f is None:
                    continue
                if f.cnn is None or f.cnn_model != mode:
                    paths_needed.append(p)
        # 중복 제거 (ref 와 val 에 동일 path 가 있을 수 있음).
        paths_needed = list(dict.fromkeys(paths_needed))
        if not paths_needed:
            return
        try:
            emb_map = _emb.compute_embeddings(paths_needed, batch_size=64)
        except Exception:
            return
        # Feature 객체에 결과 주입 (main thread, 병렬 진입 전).
        for d in (ref_feats, val_feats):
            for p, f in d.items():
                e = emb_map.get(p)
                if e is not None and f is not None:
                    f.cnn = e
                    f.cnn_model = mode

    def _score_pairs_parallel(self,
                               slot: str,
                               refs: List[ImageItem],
                               vals: List[ImageItem],
                               ref_feats: Dict[Path, Feature],
                               val_feats: Dict[Path, Feature],
                               *,
                               done_offset: int,
                               total: int,
                               disk_scores: Optional[Dict[str, float]] = None,
                               ) -> Optional[Dict[str, float]]:
        """(ref × val) 모든 쌍을 ThreadPoolExecutor 로 병렬 계산해 score_cache 에 저장.

        쌍을 한꺼번에 전부 제출하지 않고 in-flight future 를 ``window`` 개로
        제한한다(#5A) — 50,000+ 쌍에서도 ``pair_args``/``futures`` 리스트가
        한꺼번에 메모리를 점유하지 않아 속도 저하/메모리 급증을 막는다.

        ``disk_scores`` 가 주어지면(#5B) 이미 계산된 쌍은 계산을 건너뛰고
        캐시 값을 그대로 사용하며, 슬롯의 전체 점수 맵을 돌려줘 호출자가 다시
        디스크에 저장하게 한다.  ``None`` 이면 영속 캐시 비활성(반환값 None).
        """
        persist = disk_scores is not None
        out_map: Optional[Dict[str, float]] = {} if persist else None
        mtime_cache: Dict[Path, int] = {}
        done = done_offset

        def _key(rp: Path, vp: Path) -> str:
            return (f"{rp}|{_stat_mtime(rp, mtime_cache)}"
                    f"|{vp}|{_stat_mtime(vp, mtime_cache)}")

        def _iter_miss_pairs():
            """미계산 쌍만 yield. 디스크 캐시 hit 은 즉석에서 처리(진행 카운트 포함)."""
            nonlocal done
            for r in refs:
                rf = ref_feats.get(r.path)
                if rf is None:
                    continue
                for v in vals:
                    vf = val_feats.get(v.path)
                    if vf is None:
                        continue
                    rp, vp = r.path, v.path
                    if persist:
                        k = _key(rp, vp)
                        cached = disk_scores.get(k)
                        if cached is not None:
                            self._score_cache.put(slot, rp, vp, cached)
                            out_map[k] = cached
                            done += 1
                            if done % 25 == 0:
                                self.signals.progress.emit(done, total)
                            continue
                    yield (rp, vp, rf, vf)

        def _score(args):
            rp, vp, rf, vf = args
            try:
                return rp, vp, float(_pipeline.score(rf, vf))
            except Exception:
                return rp, vp, None

        n_workers = _worker_count()
        window = max(8, n_workers * 4)
        miss = _iter_miss_pairs()
        inflight: set = set()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            for _ in range(window):
                try:
                    inflight.add(pool.submit(_score, next(miss)))
                except StopIteration:
                    break
            while inflight:
                if self._stop:
                    pool.shutdown(wait=False, cancel_futures=True)
                    return out_map
                finished, inflight = wait(
                    inflight, timeout=0.5,
                    return_when=FIRST_COMPLETED,
                )
                for fut in finished:
                    rp, vp, s = fut.result()
                    done += 1
                    if s is not None:
                        self._score_cache.put(slot, rp, vp, s)
                        if persist:
                            out_map[_key(rp, vp)] = s
                    if done % 25 == 0:
                        self.signals.progress.emit(done, total)
                    try:
                        inflight.add(pool.submit(_score, next(miss)))
                    except StopIteration:
                        pass
        return out_map
