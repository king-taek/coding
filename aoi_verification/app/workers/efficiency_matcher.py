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

    def match_batch(self, refs: List[ImageItem],
                    vals: List[ImageItem]) -> Dict[Path, List[Candidate]]:
        # CPU 고전 경로는 동시성 이득이 없어 ref 별로 그대로 처리(정확·안전).
        return {Path(r.path): self.match(r, vals) for r in refs}


class _EmbedUnit:
    """OpenVINO 임베딩 유닛 (GPU=MobileNetV3 / NPU=ResNet18).

    슬롯 val 임베딩 + ANN 인덱스를 슬롯당 한 번 만들어 (단일 슬롯만 상주) ref
    임베딩으로 전수 cosine 랭킹.  임베딩이 불가한 ref 는 고전으로 안전 폴백한다.
    """

    def __init__(self, tag: str, model_kind: str, device: str,
                 cfg, threshold: float, *, jobs: Optional[int] = None,
                 batch: int = 1) -> None:
        self.tag = tag
        self._model_kind = model_kind
        self._device = device
        self._cfg = cfg
        self._threshold = float(threshold)
        self._jobs = jobs
        self._batch = max(1, int(batch))
        self._slot: Optional[str] = None
        self._built: Optional[Tuple[object, list]] = None

    def _embed(self, paths: List[Path]) -> Dict[Path, "object"]:
        return _ov.device_embed(
            paths, model_kind=self._model_kind, device=self._device,
            cfg=self._cfg, jobs=self._jobs, batch=self._batch,
        )

    def _slot_index(self, slot: str, vals: List[ImageItem]):
        if self._slot == slot:
            return self._built
        # 단일 슬롯 상주 — 이전 슬롯 인덱스는 폐기 (메모리 규율).
        self._slot = slot
        emb = self._embed([Path(v.path) for v in vals])
        self._built = _ann.build_from(emb) if emb else None
        return self._built

    def _rank(self, remb, built, by_path: Dict[Path, ImageItem]) -> List[Candidate]:
        index, val_paths = built
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

    def match(self, ref: ImageItem, vals: List[ImageItem]) -> List[Candidate]:
        return self.match_batch([ref], vals).get(Path(ref.path), [])

    def match_batch(self, refs: List[ImageItem],
                    vals: List[ImageItem]) -> Dict[Path, List[Candidate]]:
        """묶음 ref 를 한 번에 동시 임베딩 → ref 별 cosine 랭킹.

        ref 전체를 단일 ``device_embed`` 호출로 임베딩해 ``AsyncInferQueue`` 가
        여러 추론을 동시 in-flight 로 돌리게 한다(디바이스 idle 제거).  결과는
        per-ref 단건 처리와 동일(임베딩은 순서·동시성 무관)."""
        out: Dict[Path, List[Candidate]] = {}
        if not refs:
            return out
        slot = refs[0].slot
        built = self._slot_index(slot, vals)
        if built is None:
            # 슬롯 인덱스 자체 실패(컴파일/디코드) — 전부 고전 폴백.
            return {Path(r.path): score_ref_classical(
                r, vals, threshold=self._threshold, cfg=self._cfg) for r in refs}
        by_path = {Path(v.path): v for v in vals}
        embs = self._embed([Path(r.path) for r in refs])   # 묶음 동시 임베딩
        for r in refs:
            rp = Path(r.path)
            remb = embs.get(rp)
            if remb is None:
                # 이 ref 만 임베딩 실패 → 고전 폴백.
                out[rp] = score_ref_classical(
                    r, vals, threshold=self._threshold, cfg=self._cfg)
            else:
                out[rp] = self._rank(remb, built, by_path)
        return out


DEFAULT_ACCEL_CONCURRENCY = 32     # 기본 in-flight 추론 수(NPU 기준)


def accel_concurrency(cfg) -> int:
    """cfg 의 동시 추론 수(in-flight) — 없거나 잘못되면 기본값.  최소 1 보장.

    이 값이 throughput·메모리의 핵심 노브: 높일수록 NPU/GPU 가 더 많은 추론을
    동시에 in-flight 로 돌려 메모리·시간당 계산량이 올라간다(계산 결과는 불변)."""
    n = getattr(cfg, "accel_concurrency", None)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return DEFAULT_ACCEL_CONCURRENCY
    return max(1, n)


def embed_batch(cfg) -> int:
    """cfg 의 정적 배치 B (테스트용) — 없거나 잘못되면 1(끔).  최소 1."""
    b = getattr(cfg, "embed_batch", None)
    try:
        b = int(b)
    except (TypeError, ValueError):
        return 1
    return max(1, b)


def build_units(cfg, threshold: float) -> List[object]:
    """가용 유닛 워커 목록.  CPU 는 항상.  GPU/NPU 는 OpenVINO 컴파일 성공 시만.

    ``compile_model_on`` 사전 호출은 (스케줄러 스레드에서) 콜드 컴파일을 미리
    끝내 워커 루프의 lru_cache 직렬 대기를 없앤다."""
    import logging
    log = logging.getLogger("aoi.openvino")
    # 동시 추론 수(in-flight) — 사용자 조절 노브.  높일수록 NPU/GPU 메모리·
    # throughput↑.  NPU 가 메모리(8GB)가 커서 더 적극, GPU 는 절반.
    npu_jobs = accel_concurrency(cfg)
    gpu_jobs = max(1, npu_jobs // 2)
    batch = embed_batch(cfg)               # 정적 배치 B (테스트용, 기본 1)
    # 장치 사용 토글(테스트용) — 끄면 해당 유닛을 안 띄움.  기본 전부 True.
    use_cpu = bool(getattr(cfg, "use_cpu", True))
    use_gpu = bool(getattr(cfg, "use_gpu", True))
    use_npu = bool(getattr(cfg, "use_npu", True))
    units: List[object] = []
    if use_cpu:
        units.append(_CpuUnit(cfg, threshold))
    avail = _ov.available_units()              # ["GPU","NPU"] 중 존재분
    if use_gpu and "GPU" in avail:
        if _ov.compile_model_on(_ov.MODEL_MOBILENET_V3, "GPU", batch) is not None:
            units.append(_EmbedUnit("gpu", _ov.MODEL_MOBILENET_V3, "GPU", cfg,
                                    threshold, jobs=gpu_jobs, batch=batch))
        else:
            log.warning("GPU 감지됐으나 컴파일 실패 → GPU 유닛 비활성")
    if use_npu and "NPU" in avail:
        if _ov.compile_model_on(_ov.MODEL_RESNET18, "NPU", batch) is not None:
            units.append(_EmbedUnit("npu", _ov.MODEL_RESNET18, "NPU", cfg,
                                    threshold, jobs=npu_jobs, batch=batch))
        else:
            log.warning("NPU 감지됐으나 컴파일 실패 → NPU 유닛 비활성 "
                        "(상태바 툴팁의 NPU 에러 참고)")
    if not units:
        # 모든 장치를 끄거나(또는 가용/컴파일 실패) → 유닛 0개 방지: CPU 폴백.
        log.warning("활성 유닛이 없어 CPU 로 폴백(장치 토글/컴파일 확인)")
        units.append(_CpuUnit(cfg, threshold))
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
        # 1) 공유 큐 — 슬롯 순서대로 ref 를 묶음(chunk)으로 적재.  묶음 단위로
        # 한 번에 동시 추론하면 GPU/NPU 가 idle 없이 채워진다.  묶음 크기는
        # in-flight 수와 맞추되(파이프라인 충전), 슬롯당 여러 묶음이 나오게 해
        # 3 유닛 간 work-stealing 부하분산을 유지한다.
        chunk = max(1, accel_concurrency(self._cfg))
        q: "queue.Queue" = queue.Queue()
        slot_remaining: Dict[str, int] = {}
        slot_order: List[str] = []
        # 진행률은 '계산 건수(ref×val 비교 수)' 로 표시 — 기본 모드
        # (SlotPrecomputeWorker) 와 동일 의미.  slot_remaining 은 슬롯 완료
        # 판정을 위해 ref 수로 따로 센다.
        total_pairs = 0
        for slot, refs, vals in self._tasks:
            if slot not in slot_remaining:
                slot_remaining[slot] = 0
                slot_order.append(slot)
            for i in range(0, len(refs), chunk):
                ref_chunk = refs[i:i + chunk]
                q.put((slot, ref_chunk, vals))
                slot_remaining[slot] += len(ref_chunk)
                total_pairs += len(ref_chunk) * len(vals)
        total_slots = len(slot_order)
        if total_pairs == 0:
            self.signals.finished.emit()
            return

        # 2) 유닛 구성 + 콜드 컴파일 사전 워밍 (이 스레드에서).
        units = build_units(self._cfg, self._threshold)
        self._active_units = [getattr(u, "tag", "?") for u in units]
        self.signals.phase.emit(i18n.KO.PHASE_SCORING)

        # 3) 유닛별 스레드 — 공유 큐에서 work-stealing.
        lock = threading.Lock()
        done_pairs = [0]
        finished_slots = [0]

        def worker(unit) -> None:
            while not self._stop.is_set():
                try:
                    slot, ref_chunk, vals = q.get_nowait()
                except queue.Empty:
                    break
                try:
                    res = unit.match_batch(ref_chunk, vals)
                except Exception:
                    res = {}
                n = len(ref_chunk)
                with lock:
                    for r in ref_chunk:
                        cands = res.get(Path(r.path), [])
                        self._results[(slot, Path(r.path))] = [
                            (c.item.path, float(c.score)) for c in cands
                        ]
                    done_pairs[0] += n * len(vals)
                    cur_done = done_pairs[0]
                    slot_remaining[slot] -= n
                    slot_done = slot_remaining[slot] <= 0
                    fs = finished_slots[0]
                    if slot_done:
                        finished_slots[0] += 1
                        fs = finished_slots[0]
                self.signals.progress.emit(cur_done, total_pairs)
                if slot_done:
                    self.signals.slot_finished.emit(slot, fs, total_slots)

        threads = [threading.Thread(target=worker, args=(u,), daemon=True)
                   for u in units]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.signals.finished.emit()
