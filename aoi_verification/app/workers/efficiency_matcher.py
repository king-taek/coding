"""고효율 모드 — GPU 임베딩 recall + CPU 고전 재채점의 **fusion-zscore** 매칭.

검증(2 웨이퍼·4회 벤치마크) 결과 채택된 결정 규칙:
  1) GPU(MobileNetV3, OpenVINO) 임베딩으로 슬롯 val 후보를 코사인 랭킹(recall).
  2) 상위 K(=40) 후보를 CPU 고전(pHash+ORB+SSIM, ``score_ref_classical``)으로 채점.
  3) ref별 **z-점수 융합** ``z(코사인)+z(고전)`` 으로 최종 순위(단일 신호 약점 보완).

GPU/OpenVINO 가 없으면 CPU 고전 단독으로 폴백한다(절대 크래시 없음).

**NPU 관련 코드는 보존**하되(``_EmbedUnit`` 의 NPU 지원, ``build_units``,
``embedder_openvino`` 의 ResNet18 경로) 효율 모드에서 **선택하지 않는다** — NPU 는
GPU 대비 정확도 이득이 없고 느려서 비활성화했다(docs/NPU 효율성 분석 보고서 참조).
추후 재활성화는 ``_select_backend`` 에 NPU 분기를 추가하면 된다.

결과는 ``results[(slot, ref_path)] = [(val_path, score), ...]`` (내림차순) 으로
저장되어 ``match_page`` 가 무수정 소비한다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..learning import embedder_openvino as _ov
from ..models.slot import ImageItem
from ..similarity import embedding_index as _ann
from .matcher import Candidate, score_ref_classical

# 고정 하이퍼파라미터(벤치마크에서 확정 — 데이터에 맞춰 튜닝하지 않음).
FUSION_TOPK = 40          # 고전 재채점할 임베딩 상위 후보 수
GPU_BATCH = 16            # GPU 정적 배치(=batch=1 이면 처리량 폭락 → 멈춤)


def _cos_to_unit(cos: float) -> float:
    """코사인 유사도 [-1,1] → [0,1] (순서 보존)."""
    return max(0.0, min(1.0, (float(cos) + 1.0) / 2.0))


# ---------------------------------------------------------------------------
# fusion-zscore 순수 함수 (헤드리스 테스트 대상)
# ---------------------------------------------------------------------------
def _zscores(xs: List[float]) -> List[float]:
    """평균 0·표준편차 1 정규화.  길이<2 또는 std≈0 이면 전부 0(=신호 무시)."""
    n = len(xs)
    if n < 2:
        return [0.0] * n
    m = sum(xs) / n
    sd = (sum((x - m) ** 2 for x in xs) / n) ** 0.5
    if sd < 1e-9:
        return [0.0] * n
    return [(x - m) / sd for x in xs]


def zfuse(emb: List[float], cls: List[float]) -> List[float]:
    """z(임베딩 코사인) + z(고전 점수).  스케일이 다른 두 신호를 동등 융합."""
    ze, zc = _zscores(emb), _zscores(cls)
    return [a + b for a, b in zip(ze, zc)]


def map_score(fused: List[float]) -> List[float]:
    """ref별 융합 점수를 [0.80, 0.98] 밴드로 min-max 매핑(순위 보존 + 최상위가
    임계치 통과 — 현 임베딩 모드의 (cos+1)/2≈0.85~0.95 와 동일 성격)."""
    if not fused:
        return []
    lo, hi = min(fused), max(fused)
    span = hi - lo
    if span < 1e-9:
        return [0.98] * len(fused)
    return [0.80 + 0.18 * (f - lo) / span for f in fused]


# ---------------------------------------------------------------------------
# 유닛 — CPU 고전 + (보존용) OpenVINO 임베딩 유닛
# ---------------------------------------------------------------------------
class _CpuUnit:
    """CPU 고전 파이프라인.  ``pipeline.extract`` 의 디스크 캐시로 재추출은 저렴."""

    tag = "cpu"

    def __init__(self, cfg, threshold: float) -> None:
        self._cfg = cfg
        self._threshold = float(threshold)

    def match(self, ref: ImageItem, vals: List[ImageItem]) -> List[Candidate]:
        return score_ref_classical(ref, vals, threshold=self._threshold, cfg=self._cfg)

    def match_batch(self, refs: List[ImageItem],
                    vals: List[ImageItem]) -> Dict[Path, List[Candidate]]:
        return {Path(r.path): self.match(r, vals) for r in refs}


class _EmbedUnit:
    """OpenVINO 임베딩 유닛 (GPU=MobileNetV3 / NPU=ResNet18) — NPU 재활성용 보존.

    효율 모드 fusion 경로는 이 클래스를 직접 쓰지 않고 ``device_embed`` +
    ``embedding_index`` 를 직접 사용하지만, NPU 지원 코드를 보존하기 위해 유지한다.
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
        return _ov.device_embed(paths, model_kind=self._model_kind,
                                device=self._device, cfg=self._cfg,
                                jobs=self._jobs, batch=self._batch)

    def _slot_index(self, slot: str, vals: List[ImageItem]):
        if self._slot == slot:
            return self._built
        self._slot = slot
        emb = self._embed([Path(v.path) for v in vals])
        self._built = _ann.build_from(emb) if emb else None
        return self._built

    def match(self, ref: ImageItem, vals: List[ImageItem]) -> List[Candidate]:
        built = self._slot_index(ref.slot, vals)
        if built is None:
            return score_ref_classical(ref, vals, threshold=self._threshold, cfg=self._cfg)
        index, val_paths = built
        embs = self._embed([Path(ref.path)])
        remb = embs.get(Path(ref.path))
        if remb is None:
            return score_ref_classical(ref, vals, threshold=self._threshold, cfg=self._cfg)
        by_path = {Path(v.path): v for v in vals}
        out: List[Candidate] = []
        for label, cos in index.query(remb, len(val_paths)):
            if 0 <= label < len(val_paths):
                s = _cos_to_unit(cos)
                if s >= self._threshold:
                    vi = by_path.get(Path(val_paths[label]))
                    if vi is not None:
                        out.append(Candidate(item=vi, score=s))
        out.sort(key=lambda c: c.score, reverse=True)
        return out


DEFAULT_ACCEL_CONCURRENCY = 32


def accel_concurrency(cfg) -> int:
    """cfg 의 동시 추론 수(in-flight).  없거나 잘못되면 기본값.  최소 1."""
    try:
        return max(1, int(getattr(cfg, "accel_concurrency", None)))
    except (TypeError, ValueError):
        return DEFAULT_ACCEL_CONCURRENCY


def build_units(cfg, threshold: float) -> List[object]:
    """(보존용) 과거 work-stealing 유닛 빌더 — NPU 재활성 경로 문서화용.  효율
    모드 fusion 경로는 사용하지 않는다."""
    units: List[object] = [_CpuUnit(cfg, threshold)]
    avail = _ov.available_units()
    jobs = accel_concurrency(cfg)
    if bool(getattr(cfg, "use_gpu", True)) and "GPU" in avail:
        if _ov.compile_model_on(_ov.MODEL_MOBILENET_V3, "GPU", GPU_BATCH) is not None:
            units.append(_EmbedUnit("gpu", _ov.MODEL_MOBILENET_V3, "GPU", cfg,
                                    threshold, jobs=jobs, batch=GPU_BATCH))
    if bool(getattr(cfg, "use_npu", False)) and "NPU" in avail:  # 보존: NPU 경로
        if _ov.compile_model_on(_ov.MODEL_RESNET18, "NPU", 1) is not None:
            units.append(_EmbedUnit("npu", _ov.MODEL_RESNET18, "NPU", cfg,
                                    threshold, jobs=jobs, batch=1))
    return units


def describe_active_units() -> str:
    """상태바용 라벨 — CPU + (가용) GPU.  NPU 는 효율 모드에서 비활성."""
    from .. import i18n
    avail = [d for d in _ov.available_units() if d == "GPU"]
    units = ["CPU"] + avail
    return i18n.KO.ACCEL_UNITS_FMT.format(units="+".join(units))


def has_accel_units() -> bool:
    """효율 모드용 가속(GPU)이 있는지."""
    return "GPU" in _ov.available_units()


def _select_backend(cfg):
    """효율 모드 임베딩 백엔드 선택 — **CPU+GPU만**.  GPU 가용·컴파일 OK 면
    (MobileNetV3, "GPU", batch=16), 아니면 None(=CPU 고전 단독 폴백).

    NPU 는 의도적으로 선택하지 않는다(코드는 보존).  재활성화하려면 아래에
    NPU 분기를 추가하면 된다."""
    if bool(getattr(cfg, "use_gpu", True)) and "GPU" in _ov.available_units():
        if _ov.compile_model_on(_ov.MODEL_MOBILENET_V3, "GPU", GPU_BATCH) is not None:
            return (_ov.MODEL_MOBILENET_V3, "GPU", GPU_BATCH)
    return None


# ---------------------------------------------------------------------------
# 스케줄러 — 슬롯 순차 fusion 파이프라인 (FastIndexWorker 와 동일 시그널 계약)
# ---------------------------------------------------------------------------
class _SchedSignals(QObject):
    progress = pyqtSignal(int, int)            # done_pairs, total_pairs
    slot_finished = pyqtSignal(str, int, int)  # slot, done_slots(1-base), total_slots
    phase = pyqtSignal(str)
    finished = pyqtSignal()
    failed = pyqtSignal(str)


class EfficiencyScheduler(QThread):
    """슬롯 순차로 GPU 임베딩(recall) → CPU 고전 재채점 → z-융합 → ``results`` 저장."""

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

    # -- fusion 한 ref 처리 -------------------------------------------------
    def _fuse_ref(self, ref, vals, built, ref_emb, by_path) -> Optional[list]:
        """임베딩 성공 시 fusion 결과 [(val_path, score), …] 내림차순, 실패 시 None."""
        if built is None:
            return None
        remb = ref_emb.get(Path(ref.path))
        if remb is None:
            return None
        index, val_paths = built
        hits = index.query(remb, len(val_paths))     # [(label, cos)] 내림차순
        ordered = [(val_paths[lab], float(cos)) for lab, cos in hits
                   if 0 <= lab < len(val_paths)]
        if not ordered:
            return None
        top = ordered[:FUSION_TOPK]
        items = [by_path.get(Path(vp)) for vp, _ in top]
        valid = [it for it in items if it is not None]
        cls_cands = (score_ref_classical(ref, valid, threshold=0.0, cfg=self._cfg)
                     if valid else [])
        cls_map = {c.item.path: float(c.score) for c in cls_cands}
        emb_scores = [cos for _, cos in top]
        cls_scores = []
        for vp, _ in top:
            it = by_path.get(Path(vp))
            cls_scores.append(cls_map.get(it.path, 0.0) if it is not None else 0.0)
        mapped = map_score(zfuse(emb_scores, cls_scores))
        head = sorted(zip([vp for vp, _ in top], mapped), key=lambda x: -x[1])
        tail = [(vp, _cos_to_unit(cos)) for vp, cos in ordered[FUSION_TOPK:]]
        out = list(head) + tail
        out.sort(key=lambda x: -x[1])
        return out

    def _run(self) -> None:
        from .. import i18n
        tasks = self._tasks
        total_pairs = sum(len(refs) * len(vals) for _, refs, vals in tasks)
        slot_order: List[str] = []
        for s, _r, _v in tasks:
            if s not in slot_order:
                slot_order.append(s)
        total_slots = len(slot_order)
        if total_pairs == 0:
            self.signals.finished.emit()
            return

        backend = _select_backend(self._cfg)         # (model_kind, device, batch) | None
        jobs = accel_concurrency(self._cfg)
        self._active_units = ["cpu"] + ([backend[1].lower()] if backend else [])
        self.signals.phase.emit(i18n.KO.PHASE_SCORING)

        done_pairs = 0
        finished_slots = 0
        cfg, thr = self._cfg, self._threshold
        for slot, refs, vals in tasks:
            if self._stop.is_set():
                break
            by_path = {Path(v.path): v for v in vals}
            built = None
            ref_emb: Dict = {}
            if backend is not None and vals:
                mk, dev, batch = backend
                try:
                    val_emb = _ov.device_embed([Path(v.path) for v in vals],
                                               model_kind=mk, device=dev, cfg=cfg,
                                               jobs=jobs, batch=batch)
                    built = _ann.build_from(val_emb) if val_emb else None
                    if built is not None:
                        ref_emb = _ov.device_embed([Path(r.path) for r in refs],
                                                   model_kind=mk, device=dev, cfg=cfg,
                                                   jobs=jobs, batch=batch)
                except Exception:
                    built, ref_emb = None, {}

            for r in refs:
                if self._stop.is_set():
                    break
                rp = Path(r.path)
                cands = None
                try:
                    cands = self._fuse_ref(r, vals, built, ref_emb, by_path)
                except Exception:
                    cands = None
                if cands is None:                    # 고전 폴백(ref 또는 슬롯 임베딩 실패)
                    cc = score_ref_classical(r, vals, threshold=thr, cfg=cfg)
                    cands = [(c.item.path, float(c.score)) for c in cc]
                self._results[(slot, rp)] = cands
                done_pairs += len(vals)
                self.signals.progress.emit(done_pairs, total_pairs)

            finished_slots += 1
            self.signals.slot_finished.emit(slot, finished_slots, total_slots)

        self.signals.finished.emit()
