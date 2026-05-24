"""고효율 모드 — CPU·GPU·NPU 동시 work-stealing 매칭 워커.

세 연산 유닛이 **서로 다른 알고리즘**으로, **하나의 공유 ref 큐**에서 끝나는
대로 다음 기준 사진을 가져가 처리한다 (사전 할당 없음 = 동적 부하분산).

- CPU  : 고전 파이프라인 (pHash + ORB + SSIM) — ``score_ref_classical``.
- GPU  : MobileNetV3-Small 임베딩 (Intel GPU, OpenVINO) — cosine.
- NPU  : ResNet18 임베딩 (Intel NPU, OpenVINO) — cosine. 8GB 메모리를 적극
         활용하도록 다수 추론을 동시 in-flight (jobs 크게).

각 알고리즘 점수는 [0,1] 로 정규화(임베딩은 코사인 → ``(cos+1)/2``)되어 **동일
사용자 임계치**가 적용된다.  Intel 가속 장치/OpenVINO 가 없으면 가능한 유닛만
사용하고, 최소한 CPU 유닛은 항상 동작한다 (절대 크래시 없음).

결과는 ``results[(slot, ref_path)] = [(val_path, score), ...]`` (고속 모드와
동일 형태) 에 저장되어 ``match_page`` 가 무수정 소비한다.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..learning import embedder_openvino as _ov
from ..models.slot import ImageItem
from ..similarity import embedding_index as _ann
from ..similarity import pipeline as _pipeline
from .matcher import Candidate, score_ref_classical


def _cos_to_unit(cos: float) -> float:
    """코사인 유사도 [-1,1] → [0,1].  단독 임베딩 랭킹의 임계치 의미를 고전
    파이프라인([0,1])과 맞추기 위한 정규화 (순서 보존)."""
    return max(0.0, min(1.0, (float(cos) + 1.0) / 2.0))


# ---------------------------------------------------------------------------
# 유닛 — 공통 인터페이스: match(ref, vals) -> list[Candidate]
# ---------------------------------------------------------------------------
class _CpuUnit:
    """CPU 고전 파이프라인.  ``pipeline.extract`` 의 디스크 캐시로 재추출은 저렴."""

    tag = "cpu"

    def __init__(self, cfg, threshold: float) -> None:
        self._cfg = cfg
        self._threshold = float(threshold)

    def match(self, ref: ImageItem, vals: List[ImageItem]) -> List[Candidate]:
        return score_ref_classical(
            ref, vals, threshold=self._threshold, cfg=self._cfg,
        )


class _EmbedUnit:
    """OpenVINO 임베딩 유닛 (GPU=MobileNetV3 / NPU=ResNet18).

    슬롯 val 임베딩 + ANN 인덱스를 슬롯당 한 번 만들어 (단일 슬롯만 상주) ref
    임베딩으로 전수 cosine 랭킹.  임베딩이 불가한 ref 는 고전으로 안전 폴백한다.
    """

    def __init__(self, tag: str, model_kind: str, device: str,
                 cfg, threshold: float, *, jobs: Optional[int] = None) -> None:
        self.tag = tag
        self._model_kind = model_kind
        self._device = device
        self._cfg = cfg
        self._threshold = float(threshold)
        self._jobs = jobs
        self._slot: Optional[str] = None
        self._built: Optional[Tuple[object, list]] = None

    def _embed(self, paths: List[Path]) -> Dict[Path, "object"]:
        return _ov.device_embed(
            paths, model_kind=self._model_kind, device=self._device,
            cfg=self._cfg, jobs=self._jobs,
        )

    def _slot_index(self, slot: str, vals: List[ImageItem]):
        if self._slot == slot:
            return self._built
        # 단일 슬롯 상주 — 이전 슬롯 인덱스는 폐기 (메모리 규율).
        self._slot = slot
        emb = self._embed([Path(v.path) for v in vals])
        self._built = _ann.build_from(emb) if emb else None
        return self._built

    def match(self, ref: ImageItem, vals: List[ImageItem]) -> List[Candidate]:
        built = self._slot_index(ref.slot, vals)
        remb = None
        if built is not None:
            remb = self._embed([Path(ref.path)]).get(Path(ref.path))
        if built is None or remb is None:
            # 임베딩 불가(컴파일/디코드 실패) — 이 ref 만 고전으로 폴백.
            return score_ref_classical(
                ref, vals, threshold=self._threshold, cfg=self._cfg,
            )
        index, val_paths = built
        by_path = {Path(v.path): v for v in vals}
        hits = index.query(remb, len(val_paths))
        out: List[Candidate] = []
        for label, cos in hits:
            if 0 <= label < len(val_paths):
                s = _cos_to_unit(cos)
                if s >= self._threshold:
                    vi = by_path.get(Path(val_paths[label]))
                    if vi is not None:
                        out.append(Candidate(item=vi, score=s))
        out.sort(key=lambda c: c.score, reverse=True)
        return out


def build_units(cfg, threshold: float) -> List[object]:
    """가용 유닛 워커 목록.  CPU 는 항상.  GPU/NPU 는 OpenVINO 컴파일 성공 시만.

    ``compile_model_on`` 사전 호출은 (스케줄러 스레드에서) 콜드 컴파일을 미리
    끝내 워커 루프의 lru_cache 직렬 대기를 없앤다."""
    units: List[object] = [_CpuUnit(cfg, threshold)]
    avail = _ov.available_units()              # ["GPU","NPU"] 중 존재분
    if "GPU" in avail and _ov.compile_model_on(_ov.MODEL_MOBILENET_V3, "GPU") is not None:
        units.append(_EmbedUnit("gpu", _ov.MODEL_MOBILENET_V3, "GPU", cfg, threshold))
    if "NPU" in avail and _ov.compile_model_on(_ov.MODEL_RESNET18, "NPU") is not None:
        # NPU 8GB — 다수 추론 동시 in-flight 로 메모리/파이프라인 적극 활용.
        units.append(_EmbedUnit("npu", _ov.MODEL_RESNET18, "NPU", cfg, threshold, jobs=16))
    return units


def describe_active_units() -> str:
    """상태바용 라벨 — 가동 가능한 유닛 (CPU 항상 + 가용 GPU/NPU)."""
    from .. import i18n
    units = ["CPU"] + _ov.available_units()
    return i18n.KO.ACCEL_UNITS_FMT.format(units="+".join(units))


def has_accel_units() -> bool:
    """Intel GPU/NPU 가속 유닛이 하나라도 있는지 (없으면 CPU 단독 안내)."""
    return bool(_ov.available_units())


# ---------------------------------------------------------------------------
# work-stealing 스케줄러 — FastIndexWorker 와 동일 시그널 계약
# ---------------------------------------------------------------------------
class _SchedSignals(QObject):
    progress = pyqtSignal(int, int)            # done_refs, total_refs
    slot_finished = pyqtSignal(str, int, int)  # slot, done_slots(1-base), total_slots
    phase = pyqtSignal(str)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class EfficiencyScheduler(QThread):
    """공유 ref 큐 + 유닛별 스레드 (CPU/GPU/NPU).  각 스레드가 끝나는 대로
    다음 ref 를 가져가 자기 알고리즘으로 매칭 → ``results`` 에 저장."""

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
        self._active_units: List[str] = []
        self.signals = _SchedSignals()

    def stop(self) -> None:
        self._stop.set()

    def active_units(self) -> List[str]:
        return list(self._active_units)

    def run(self) -> None:        # type: ignore[override]
        try:
            self._run()
        except Exception as exc:                # pragma: no cover - 방어
            self.signals.failed.emit(str(exc))

    def _run(self) -> None:
        from .. import i18n
        # 1) 공유 큐 — 슬롯 순서대로 적재(수동 모드 조기 슬롯 우선).
        q: "queue.Queue" = queue.Queue()
        slot_remaining: Dict[str, int] = {}
        slot_order: List[str] = []
        total_refs = 0
        for slot, refs, vals in self._tasks:
            if slot not in slot_remaining:
                slot_remaining[slot] = 0
                slot_order.append(slot)
            for r in refs:
                q.put((slot, r, vals))
                slot_remaining[slot] += 1
                total_refs += 1
        total_slots = len(slot_order)
        if total_refs == 0:
            self.signals.finished.emit()
            return

        # 2) 유닛 구성 + 콜드 컴파일 사전 워밍 (이 스레드에서).
        units = build_units(self._cfg, self._threshold)
        self._active_units = [getattr(u, "tag", "?") for u in units]
        self.signals.phase.emit(i18n.KO.PHASE_SCORING)

        # 3) 유닛별 스레드 — 공유 큐에서 work-stealing.
        lock = threading.Lock()
        done = [0]
        finished_slots = [0]

        def worker(unit) -> None:
            while not self._stop.is_set():
                try:
                    slot, ref, vals = q.get_nowait()
                except queue.Empty:
                    break
                try:
                    cands = unit.match(ref, vals)
                except Exception:
                    cands = []
                rec = [(c.item.path, float(c.score)) for c in cands]
                with lock:
                    self._results[(slot, Path(ref.path))] = rec
                    done[0] += 1
                    cur_done = done[0]
                    slot_remaining[slot] -= 1
                    slot_done = slot_remaining[slot] <= 0
                    fs = finished_slots[0]
                    if slot_done:
                        finished_slots[0] += 1
                        fs = finished_slots[0]
                self.signals.progress.emit(cur_done, total_refs)
                if slot_done:
                    self.signals.slot_finished.emit(slot, fs, total_slots)

        threads = [threading.Thread(target=worker, args=(u,), daemon=True)
                   for u in units]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.signals.finished.emit()
