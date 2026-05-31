"""매칭 가속 조합(레시피) 벤치마크 러너 — 속도·메모리·실제 정확도 측정·기록.

핵심 보장
- **캐시 우회**: 모든 레시피를 ``bench_no_cache=True`` 로 실행해 '처음 매칭처럼'
  측정한다(특징/임베딩/점수 디스크 캐시 미사용).
- **실제 정확도 우선**: 추천은 '현행(운영) 대비 정확도가 낮아지지 않으면서 가장
  빠른' 조합만 고른다.  정확도는 (1) 정답 라벨이 있으면 recall@1/@5, (2) 없으면
  CPU 고전 전수(정답 기준선) 대비 top-1 일치율로 측정한다.
- **로딩 안전**: 레시피는 항상 순차 실행, 동시성은 가용 메모리로 상한, 레시피별
  타임아웃, 대용량은 서브샘플 — 시스템이 로딩으로 꺼지지 않게 한다.

헤드리스로 단독 실행 가능(맨 아래 ``main``).  GUI(개발자 모드 다이얼로그)도 이
모듈의 ``run_suite`` 를 그대로 호출한다.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field, replace as _dc_replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import recipes as _rx
from .recipes import (RECALL_CPU, RECALL_GPU, RECALL_GPU_NPU, RECALL_NONE,
                      RECALL_NPU, SCORE_CLASSICAL, SCORE_EMBED_ONLY,
                      SCORE_FUSION, Recipe)

# 외부 ThreadPool 로 ref 를 병렬 재채점하므로 cv2 내부 스레드는 끈다(과도구독 방지).
try:
    import cv2 as _cv2
    _cv2.setNumThreads(1)
except Exception:
    pass


# 고속 재채점 노브 → 사용할 고전 컴포넌트 집합(None=전체 pHash+ORB+SSIM).
def _rerank_components(recipe) -> Optional[set]:
    mode = str(getattr(recipe, "rerank", "classical") or "classical")
    return {
        "classical": None,
        "phash": {"phash"},
        "phash_ssim": {"phash", "ssim"},      # ORB(최고비용) 제거
        "orb_ssim": {"orb", "ssim"},          # pHash 제거(대조)
        "phash_orb": {"phash", "orb"},        # SSIM 제거
        "orb": {"orb"},                       # ORB 단독(변별력/비용 분리)
        "ssim": {"ssim"},                     # SSIM 단독
    }.get(mode, None)


# 결과에 보관할 ref 당 후보 상한(메모리 절제) — recall@5 + 일치율@1 에 충분.
RESULT_KEEP = 10
# 한 추론 in-flight 당 대략적 작업 메모리(입력 텐서+오버헤드) — 동시성 상한 산정용.
_BYTES_PER_INFLIGHT = 4 * 1024 * 1024


# 결과 타입: results[(slot, ref_path)] = [(val_path, score), ...] 내림차순
Results = Dict[Tuple[str, str], List[Tuple[str, float]]]


# ---------------------------------------------------------------------------
# 메모리 안전 — 가용 메모리에 맞춘 동시성 상한 + 피크 RSS 샘플러
# ---------------------------------------------------------------------------
def available_bytes() -> Optional[int]:
    try:
        import psutil
        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def safe_concurrency(n_items: int, requested: int,
                     *, avail: Optional[int] = None,
                     fraction: float = 0.5) -> int:
    """가용 메모리의 ``fraction`` 안에서 동시 추론 수를 정한다.

    요청값과 워크로드(ceil 필요분), 메모리 한도 중 가장 작은 값으로 클램프해
    '로딩으로 시스템이 꺼지는' 상황을 막는다.  메모리 조회 실패 시 요청값을 8 로
    상한(보수적)."""
    req = max(1, int(requested))
    if n_items > 0:
        req = min(req, n_items)
    if avail is None:
        avail = available_bytes()
    if avail is None:
        return max(1, min(req, 8))
    mem_cap = int((avail * float(fraction)) // _BYTES_PER_INFLIGHT)
    return max(1, min(req, max(1, mem_cap)))


class PeakMem:
    """``with PeakMem() as pm:`` 블록 동안 프로세스 피크 RSS(MB)를 샘플링."""

    def __init__(self, interval: float = 0.05) -> None:
        self._interval = float(interval)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.peak_bytes = 0
        self._proc = None
        try:
            import psutil
            self._proc = psutil.Process()
        except Exception:
            self._proc = None

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                rss = self._proc.memory_info().rss
                if rss > self.peak_bytes:
                    self.peak_bytes = rss
            except Exception:
                pass
            self._stop.wait(self._interval)

    def __enter__(self) -> "PeakMem":
        if self._proc is not None:
            self._thread = threading.Thread(target=self._sample, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    @property
    def peak_mb(self) -> Optional[float]:
        if self._proc is None:
            return None
        return round(self.peak_bytes / (1024 * 1024), 1)


# ---------------------------------------------------------------------------
# 데이터셋 — (slot, refs, vals) 작업 목록 + 정답(GT)
# ---------------------------------------------------------------------------
@dataclass
class Dataset:
    tasks: List[Tuple[str, list, list]]            # (slot, refs[ImageItem], vals[ImageItem])
    gt: Dict[Tuple[str, str], set]                 # (slot, ref_path) -> {정답 val_path,...}
    ref_root: str = ""
    val_root: str = ""

    def n_pairs(self) -> int:
        return sum(len(r) * len(v) for _, r, v in self.tasks)

    def n_images(self) -> int:
        return sum(len(r) + len(v) for _, r, v in self.tasks)


def build_dataset(ref_root, val_root, *, labels: Optional[dict] = None,
                  max_slots: int = 0, max_images_per_side: int = 0) -> Dataset:
    """기준/검증 폴더를 스캔해 작업 목록을 만든다.  ``labels`` 가 있으면 GT 로 사용.

    ``max_slots`` / ``max_images_per_side`` 가 0 보다 크면 서브샘플(로딩 안전)."""
    from ..models.slot import scan
    sr = scan(Path(ref_root), Path(val_root))
    names = list(sr.common_slot_names)
    if max_slots and max_slots > 0:
        names = names[:max_slots]
    tasks: List[Tuple[str, list, list]] = []
    for name in names:
        slot = sr.slots[name]
        refs = list(slot.ref_images)
        vals = list(slot.val_images)
        if max_images_per_side and max_images_per_side > 0:
            refs = refs[:max_images_per_side]
            vals = vals[:max_images_per_side]
        if refs and vals:
            tasks.append((name, refs, vals))
    gt = _labels_to_gt(labels) if labels else {}
    return Dataset(tasks=tasks, gt=gt, ref_root=str(ref_root), val_root=str(val_root))


def _labels_to_gt(labels: dict) -> Dict[Tuple[str, str], set]:
    """라벨 JSON → GT.  형식: {slot: {ref_path: [정답 val_path,...] 또는 val_path}}."""
    gt: Dict[Tuple[str, str], set] = {}
    for slot, refmap in (labels or {}).items():
        for rp, vps in (refmap or {}).items():
            if isinstance(vps, (list, tuple, set)):
                gt[(str(slot), str(rp))] = {str(x) for x in vps}
            else:
                gt[(str(slot), str(rp))] = {str(vps)}
    return gt


# ---------------------------------------------------------------------------
# 임베딩 추출 — 레시피의 recall 장치에 따라 (폴백/분담/앙상블)
# ---------------------------------------------------------------------------
def _device_for(recall: str) -> str:
    return {RECALL_CPU: "CPU", RECALL_GPU: "GPU", RECALL_NPU: "NPU"}.get(recall, "GPU")


def _embed_paths(paths: List[Path], recipe: Recipe, cfg, devices: set,
                 progress: Optional[Callable] = None) -> Dict[Path, "object"]:
    """``paths`` 임베딩을 레시피 장치 정책대로 계산.  미가용/실패 시 빈 dict."""
    if not paths:
        return {}
    try:
        from ..learning import embedder_openvino as _ov
    except Exception:
        return {}
    model = recipe.embed_model or _rx.MODEL_MOBILENET_V3
    cap = safe_concurrency(len(paths), recipe.concurrency)
    batch = max(1, int(recipe.embed_batch))
    # NPU 사용 방식 노브 — perf_hint/streams/멀티스레드/해상도(레시피에서).
    knobs = dict(perf_hint=getattr(recipe, "perf_hint", "THROUGHPUT"),
                 streams=int(getattr(recipe, "streams", 0) or 0),
                 preprocess_threads=int(getattr(recipe, "preprocess_threads", 0) or 0),
                 input_px=int(getattr(recipe, "input_px", 0) or 0))

    # GPU+NPU '분담' — 절반씩 두 장치에서 동시에 뽑아 처리량을 올린다.
    if (recipe.recall == RECALL_GPU_NPU and not recipe.ensemble
            and {"GPU", "NPU"} <= devices):
        half = len(paths) // 2 or len(paths)
        chunks = [(paths[:half], "GPU"), (paths[half:], "NPU")]
        out: Dict[Path, object] = {}
        lock = threading.Lock()

        def _go(ps, dev):
            if not ps:
                return
            try:
                part = _ov.device_embed(ps, model_kind=model, device=dev, cfg=cfg,
                                        jobs=cap, batch=batch, progress_cb=progress,
                                        **knobs)
            except Exception:
                part = {}
            with lock:
                out.update(part)

        th = [threading.Thread(target=_go, args=c, daemon=True) for c in chunks]
        for t in th:
            t.start()
        for t in th:
            t.join()
        return out

    # 단일 장치(또는 앙상블의 GPU 측 — 앙상블 처리는 호출부에서).
    dev = _device_for(recipe.recall if recipe.recall != RECALL_GPU_NPU else RECALL_GPU)
    try:
        return _ov.device_embed(paths, model_kind=model, device=dev, cfg=cfg,
                                jobs=cap, batch=batch, progress_cb=progress, **knobs)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 한 레시피 실행 — (results, 측정치, 비고)
# ---------------------------------------------------------------------------
@dataclass
class RecipeRun:
    key: str
    name: str
    ok: bool = True
    used_embedding: bool = False
    fell_back_classical: bool = False
    skipped: bool = False
    timed_out: bool = False
    total_sec: float = 0.0
    embed_sec: float = 0.0
    score_sec: float = 0.0
    img_per_sec: float = 0.0
    peak_mb: Optional[float] = None
    n_images: int = 0
    n_pairs: int = 0
    note: str = ""
    # 정확도(평가 단계에서 채움)
    recall1: Optional[float] = None
    recall5: Optional[float] = None
    agree1: Optional[float] = None
    # 임베딩 후보 recall — GPU/NPU 가 추린 순위에서 정답이 몇 위에 있나(CPU 재채점 전).
    #   topk 재채점이 안전한지(정답이 잘려나가지 않는지) 판단용.  embed_recall[K]=정답이
    #   top-K 안에 든 쿼리 비율.  worst_correct_rank=정답의 최악 순위(=100% 잡으려면
    #   필요한 최소 topk).  cand_n=평가된 쿼리 수.
    embed_recall: Optional[dict] = None
    worst_correct_rank: Optional[int] = None
    cand_n: int = 0
    # NPU 사용량(프록시) — 진짜 HW 가동률 % 는 플랫폼 의존이라, 드라이브 설정과 실제 NPU
    # 추론 시간/처리량으로 '얼마나 바쁘게 굴렸나'를 기록한다(사용자 요청).
    npu_used: bool = False
    npu_sec: float = 0.0            # NPU 추론에 쓴 시간(임베딩 분담분)
    npu_infer: int = 0             # NPU 로 임베딩한 이미지 수(추론 횟수 프록시)
    npu_throughput: float = 0.0    # NPU img/s
    npu_busy_frac: Optional[float] = None  # npu_sec / total_sec (가동률 프록시)
    npu_drive: str = ""            # 드라이브 설정: jobs/streams/batch/hint
    desc: str = ""


def skip_reason(recipe: Recipe, devices: set) -> str:
    """이 레시피를 **실험할 필요가 없는** 이유(있으면 사유 문자열, 없으면 "").

    '불필요한 테스트는 하지 않는다' — 다음은 실행해도 새 정보가 없거나 같은 CPU
    고전 결과만 반복하므로 기본 스위트에서 건너뛴다(명시 선택하면 그래도 실행):
      · 필요한 가속 장치(GPU/NPU)가 없어 어차피 CPU 고전으로 폴백 → 중복 결과.
    함정/대조용(diagnostic)은 여기서 막지 않고 호출부에서 기본 제외한다."""
    need = recipe.required_devices()
    if need and not (need <= set(devices or set())):
        miss = ", ".join(sorted(need - set(devices or set())))
        return f"{miss} 미감지 → CPU 폴백 중복(건너뜀)"
    return ""


# ── 중앙-인식(center-aware) 채점 — 순수 함수(헤드리스 테스트 가능) ───────────
def blend_region_scores(center_map: Dict[str, float], full_map: Dict[str, float],
                        center_weight: float) -> Dict[str, float]:
    """defect(중앙) 점수와 주변 패턴(풀 ROI) 점수를 가중 융합.

    사용자 제안: "주변 패턴 유사도 + defect 유사도".  ``center_weight`` 는 중앙
    (defect) 비중 [0,1].  두 맵에 없는 경로는 0 으로 본다."""
    w = max(0.0, min(1.0, float(center_weight)))
    out: Dict[str, float] = {}
    for p in set(center_map) | set(full_map):
        out[p] = w * float(center_map.get(p, 0.0)) + (1.0 - w) * float(full_map.get(p, 0.0))
    return out


def cascade_survivors(center_map: Dict[str, float], keep: int) -> List[str]:
    """coarse(중앙) 점수 상위 ``keep`` 개 경로 — fine(풀 ROI) 정밀 재채점 대상."""
    keep = max(1, int(keep))
    return [p for p, _ in sorted(center_map.items(), key=lambda kv: -kv[1])[:keep]]


def fuse_zscore_signals(signals: List[List[float]]) -> List[float]:
    """N개 신호를 각각 z-정규화해 합산 — efficiency_matcher.zfuse 의 일반화.

    임베딩 코사인 + CPU 고전 + NPU defect 임베딩 등 **스케일이 다른 여러 신호**를
    동등 융합한다(NPU 병렬 보조기의 3신호 융합용).  순수 함수(헤드리스 테스트)."""
    from ..workers.efficiency_matcher import _zscores
    usable = [s for s in signals if s]
    if not usable:
        return []
    n = len(usable[0])
    zs = [_zscores(s) for s in usable if len(s) == n]
    return [sum(z[i] for z in zs) for i in range(n)]


def run_recipe(ds: Dataset, recipe: Recipe, *,
               threshold: float = 0.0,
               devices: Optional[set] = None,
               stop: Optional[Callable[[], bool]] = None,
               progress: Optional[Callable] = None) -> Tuple[Results, RecipeRun]:
    """한 레시피로 매칭을 수행하고 결과/측정치를 돌려준다(캐시 우회)."""
    from ..similarity import embedding_index as _ann
    from ..workers.efficiency_matcher import (_cos_to_unit, map_score, zfuse)
    from ..workers.matcher import score_ref_classical

    if devices is None:
        devices = detect_devices()
    cfg = recipe.to_cfg()
    # 임베딩(GPU/NPU) 후보 순위 — 쿼리별 (CPU 재채점 전) 전체 코사인 정렬.  GT 와 대조해
    # '정답이 몇 위에 있나'(후보 recall)를 계산한다.
    embed_order: Dict[Tuple[str, str], List[str]] = {}
    comp = _rerank_components(recipe)        # 고속 재채점 컴포넌트(None=전체 고전)
    run = RecipeRun(key=recipe.key, name=recipe.name, desc=recipe.desc,
                    n_images=ds.n_images(), n_pairs=ds.n_pairs())
    results: Results = {}
    embed_t = 0.0
    score_t = 0.0

    def stopped() -> bool:
        return bool(stop and stop())

    def _smap(r, items, c, comp_) -> Dict[str, float]:
        cands = score_ref_classical(r, items, threshold=0.0, cfg=c,
                                    components=comp_, stop_cb=stopped)
        return {str(x.item.path): float(x.score) for x in cands}

    def _rerank_score_map(r, items, comp_) -> Dict[str, float]:
        """ref 1장 vs 후보들의 고전 점수 맵 {경로:점수}.  recipe 가 center-aware 면
        중앙(defect)/풀(주변) 영역을 조합한다(B 캐스케이드 / A 영역융합)."""
        if not items:
            return {}
        if getattr(recipe, "cascade", False):
            ratio = float(getattr(recipe, "center_ratio", 0.0) or 0.25)
            keep = int(getattr(recipe, "cascade_keep", 0) or 8)
            cfg_c = _dc_replace(cfg, center_crop=True, center_ratio=ratio)
            cmap = _smap(r, items, cfg_c, comp_)            # coarse: 중앙(defect)
            keep_set = set(cascade_survivors(cmap, keep))
            survivors = [it for it in items if str(it.path) in keep_set]
            cfg_f = _dc_replace(cfg, center_crop=False)
            return _smap(r, survivors, cfg_f, comp_)        # fine: 풀 ROI(추려진 것만)
        if getattr(recipe, "region_fusion", False):
            ratio = float(getattr(recipe, "center_ratio", 0.0) or 0.25)
            w = float(getattr(recipe, "center_weight", 0.0) or 0.6)
            cfg_c = _dc_replace(cfg, center_crop=True, center_ratio=ratio)
            cfg_f = _dc_replace(cfg, center_crop=False)
            cmap = _smap(r, items, cfg_c, comp_)            # defect 유사도
            fmap = _smap(r, items, cfg_f, comp_)            # 주변 패턴 유사도
            return blend_region_scores(cmap, fmap, w)
        return _smap(r, items, cfg, comp_)                  # 일반(현행)

    def _store(slot, ref_path, ranked):
        ranked = sorted(ranked, key=lambda x: -x[1])[:RESULT_KEEP]
        results[(slot, str(ref_path))] = [(str(p), float(s)) for p, s in ranked]

    def _classical_refs(slot, refs, vals):
        """refs 전부를 vals 와 고전(또는 고속 부분) 채점해 저장.  ``rerank_workers``>1
        이면 ref 들을 멀티코어로 병렬 채점(결과 동일, 시간만↓)."""
        workers = int(getattr(recipe, "rerank_workers", 0) or 0)

        def one(r):
            m = _rerank_score_map(r, vals, comp)            # center-aware 면 영역 조합
            return [(Path(p), s) for p, s in m.items()]

        if workers > 1 and len(refs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(workers, len(refs))) as pool:
                futs = {pool.submit(one, r): r for r in refs}
                for fut in futs:
                    r = futs[fut]
                    try:
                        ranked = fut.result()
                    except Exception:
                        ranked = []
                    _store(slot, r.path, ranked)
        else:
            for r in refs:
                if stopped():
                    break
                _store(slot, r.path, one(r))

    classical_recall = (recipe.scoring == SCORE_CLASSICAL
                        or recipe.recall == RECALL_NONE)

    t_total = time.perf_counter()
    for slot, refs, vals in ds.tasks:
        if stopped():
            break
        by_path = {Path(v.path): v for v in vals}

        # ── 순수 고전(임베딩 없음) ────────────────────────────────────
        if classical_recall:
            t0 = time.perf_counter()
            _classical_refs(slot, refs, vals)
            score_t += time.perf_counter() - t0
            continue

        # ── 임베딩 recall ────────────────────────────────────────────
        t0 = time.perf_counter()
        if recipe.recall == RECALL_GPU_NPU and recipe.ensemble:
            built_a, built_b = _embed_ensemble(refs, vals, recipe, cfg, devices, progress)
            built = built_a            # 코사인 평균은 아래 분기에서
        else:
            val_emb = _embed_paths([Path(v.path) for v in vals], recipe, cfg,
                                   devices, progress)
            built = _ann.build_from(val_emb) if val_emb else None
            built_b = None
        embed_t += time.perf_counter() - t0

        if built is None:
            # 임베딩 미가용/실패 → CPU 고전 폴백(정확도 보존, 측정은 폴백 표시).
            run.fell_back_classical = True
            t0 = time.perf_counter()
            _classical_refs(slot, refs, vals)
            score_t += time.perf_counter() - t0
            continue

        run.used_embedding = True
        index, val_paths = built
        # ref 임베딩
        t0 = time.perf_counter()
        ref_emb = _embed_paths([Path(r.path) for r in refs], recipe, cfg, devices)
        embed_t += time.perf_counter() - t0

        comp = _rerank_components(recipe)        # 고속 재채점 컴포넌트(None=전체)

        def _npu_defect_signal(r, top):
            """NPU 병렬 보조기 — 상위 후보의 중앙(defect) 임베딩을 NPU(batch=1)로 뽑아
            ref 대비 코사인을 돌려준다(top 순서).  NPU 없음/실패/미설정이면 None."""
            if not getattr(recipe, "npu_defect_assist", False) or "NPU" not in devices:
                return None
            try:
                from ..learning import embedder_openvino as _ov
                ratio = float(getattr(recipe, "center_ratio", 0.0) or 0.25)
                model = recipe.embed_model or _rx.MODEL_MOBILENET_V3
                cfg_c = _dc_replace(cfg, center_crop=True, center_ratio=ratio, use_npu=True)
                paths = [Path(r.path)] + [Path(vp) for vp, _ in top]
                emb = _ov.device_embed(paths, model_kind=model, device="NPU",
                                       cfg=cfg_c, jobs=8, batch=1)
                rv = emb.get(Path(r.path))
                if rv is None:
                    return None
                return [(_cosine(rv, emb[Path(vp)]) if emb.get(Path(vp)) is not None
                         else 0.0) for vp, _ in top]
            except Exception:
                return None

        def _fuse_one(r):
            """ref 1장 → 저장할 ranked 리스트.  병렬 워커에서도 안전(읽기 전용 공유)."""
            remb = ref_emb.get(Path(r.path))
            if remb is None:
                cands = score_ref_classical(r, vals, threshold=threshold, cfg=cfg,
                                            components=comp, stop_cb=stopped)
                return [(c.item.path, c.score) for c in cands]
            hits = index.query(remb, len(val_paths))          # [(label, cos)] desc
            ordered = [(val_paths[lab], float(cos)) for lab, cos in hits
                       if 0 <= lab < len(val_paths)]
            if built_b is not None:                            # 앙상블: 두 코사인 평균
                ordered = _ensemble_merge(ordered, built_b, ref_emb_b_get(r))
            # 후보 recall 계산용 — CPU 재채점 전 임베딩 순위(정답이 몇 위에 있나).
            embed_order[(slot, str(r.path))] = [str(vp) for vp, _ in ordered]
            if recipe.scoring == SCORE_EMBED_ONLY:
                return [(vp, _cos_to_unit(c)) for vp, c in ordered]
            # fusion — 상위 topk 를 CPU 고전(또는 고속 부분) 재채점 후 z-융합
            topk = max(int(recipe.fusion_topk), 1)
            top = ordered[:topk]
            items = [by_path.get(Path(vp)) for vp, _ in top]
            valid = [it for it in items if it is not None]
            cls_map = _rerank_score_map(r, valid, comp)     # center-aware 면 영역 조합
            emb_scores = [c for _, c in top]
            cls_scores = [cls_map.get(str(vp), 0.0) for vp, _ in top]
            npu_sig = _npu_defect_signal(r, top)            # NPU 병렬 보조기(3신호) or None
            if npu_sig is not None:
                mapped = map_score(fuse_zscore_signals([emb_scores, cls_scores, npu_sig]))
            else:
                mapped = map_score(zfuse(emb_scores, cls_scores))
            head = list(zip([vp for vp, _ in top], mapped))
            tail = [(vp, _cos_to_unit(c)) for vp, c in ordered[topk:]]
            return head + tail

        t0 = time.perf_counter()
        workers = int(getattr(recipe, "rerank_workers", 0) or 0)
        if workers > 1 and len(refs) > 1:
            # CPU 멀티코어로 ref 들을 병렬 재채점 — 결과는 직렬과 동일, 시간만↓.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(workers, len(refs))) as pool:
                futs = {pool.submit(_fuse_one, r): r for r in refs}
                for fut in futs:
                    r = futs[fut]
                    try:
                        ranked = fut.result()
                    except Exception:
                        ranked = []
                    _store(slot, r.path, ranked)
        else:
            for r in refs:
                if stopped():
                    break
                _store(slot, r.path, _fuse_one(r))
        score_t += time.perf_counter() - t0

    run.total_sec = round(time.perf_counter() - t_total, 3)
    run.embed_sec = round(embed_t, 3)
    run.score_sec = round(score_t, 3)
    run.timed_out = stopped()
    n = run.n_images
    run.img_per_sec = round(n / run.total_sec, 1) if run.total_sec > 1e-6 else 0.0
    # 임베딩 후보 recall — 정답이 GPU/NPU 순위에서 몇 위에 있나(topk 안전성 판단).
    if embed_order and ds.gt:
        rec_k, worst, cn = candidate_recall(embed_order, ds.gt)
        run.embed_recall = {str(k): v for k, v in rec_k.items()}
        run.worst_correct_rank = worst
        run.cand_n = cn
    # NPU 사용량(프록시) — 드라이브 설정 + 추정 NPU 시간/처리량/가동률.
    uses_npu = (recipe.recall in (RECALL_NPU, RECALL_GPU_NPU)
                or bool(getattr(recipe, "npu_defect_assist", False)))
    run.npu_used = bool(uses_npu and "NPU" in (devices or set()))
    _assist = " assist" if getattr(recipe, "npu_defect_assist", False) else ""
    run.npu_drive = (f"jobs={recipe.concurrency} streams={getattr(recipe,'streams',0)} "
                     f"batch={recipe.embed_batch} hint={getattr(recipe,'perf_hint','')}{_assist}")
    if run.npu_used:
        # NPU 분담분 — NPU 단독=전체, GPU+NPU 분담=절반, defect 보조=별도 추정 어려워 0 처리.
        frac = (1.0 if recipe.recall == RECALL_NPU
                else 0.5 if recipe.recall == RECALL_GPU_NPU else 0.0)
        run.npu_sec = round(embed_t * frac, 3)
        run.npu_infer = int(run.n_images * frac)
        if run.npu_sec > 1e-6:
            run.npu_throughput = round(run.npu_infer / run.npu_sec, 1)
            run.npu_busy_frac = (round(run.npu_sec / run.total_sec, 3)
                                 if run.total_sec > 1e-6 else None)
    if run.fell_back_classical and recipe.uses_embedding():
        run.note = "가속/모델 미가용 → CPU 고전 폴백(속도는 CPU 기준)"
        need = str(getattr(recipe, "needs", "") or "")
        if need:
            run.note += f" · 필요: {need}"
    return results, run


# 앙상블 보조(거의 쓰이지 않는 대조군 — 두 장치 임베딩의 코사인 평균).
_ENSEMBLE_REF_B: Dict[str, object] = {}


def _embed_ensemble(refs, vals, recipe, cfg, devices, progress):
    """GPU(MobileNet) 전체 + NPU(ResNet18) 전체를 각각 임베딩(시간 2배 — 안티패턴)."""
    from ..similarity import embedding_index as _ann
    val_paths = [Path(v.path) for v in vals]
    ref_paths = [Path(r.path) for r in refs]
    # GPU 측(MobileNet)
    rec_g = Recipe(key="_g", name="g", recall=RECALL_GPU, scoring=recipe.scoring,
                   embed_model=_rx.MODEL_MOBILENET_V3, embed_batch=recipe.embed_batch,
                   concurrency=recipe.concurrency)
    rec_n = Recipe(key="_n", name="n", recall=RECALL_NPU, scoring=recipe.scoring,
                   embed_model=_rx.MODEL_RESNET18, embed_batch=1,
                   concurrency=recipe.concurrency)
    val_g = _embed_paths(val_paths, rec_g, cfg, devices, progress)
    val_n = _embed_paths(val_paths, rec_n, cfg, devices, progress)
    _ENSEMBLE_REF_B.clear()
    if val_n:
        ref_n = _embed_paths(ref_paths, rec_n, cfg, devices)
        for p, v in ref_n.items():
            _ENSEMBLE_REF_B[str(p)] = v
        _ENSEMBLE_REF_B["__index__"] = _ann.build_from(val_n)
    built_a = _ann.build_from(val_g) if val_g else None
    built_b = _ENSEMBLE_REF_B.get("__index__") if val_n else None
    return built_a, built_b


def ref_emb_b_get(r):
    return _ENSEMBLE_REF_B.get(str(Path(r.path)))


def _ensemble_merge(ordered_a, built_b, remb_b):
    """A 의 (val_path,cosA) 에 B 인덱스의 cosB 를 합쳐 평균 코사인으로 재정렬."""
    if built_b is None or remb_b is None:
        return ordered_a
    index_b, vpaths_b = built_b
    hits = dict()
    for lab, cos in index_b.query(remb_b, len(vpaths_b)):
        if 0 <= lab < len(vpaths_b):
            hits[str(vpaths_b[lab])] = float(cos)
    merged = [(vp, (cosA + hits.get(str(vp), cosA)) / 2.0) for vp, cosA in ordered_a]
    merged.sort(key=lambda x: -x[1])
    return merged


# ---------------------------------------------------------------------------
# 장치 감지
# ---------------------------------------------------------------------------
def detect_devices() -> set:
    """가용 Intel 가속 장치 집합 — ``{"GPU","NPU"}`` 중 실제 존재분."""
    try:
        from ..learning import embedder_openvino as _ov
        return set(_ov.available_units())
    except Exception:
        return set()


def _cosine(a, b) -> float:
    import numpy as np
    a = np.asarray(a, dtype="float64").ravel()
    b = np.asarray(b, dtype="float64").ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def diagnose_npu_embedding(paths: List[Path], *, model: str = "",
                           max_images: int = 24) -> dict:
    """NPU 배치 정확도 붕괴 **원인 규명** — 같은 이미지에서 GPU vs NPU(batch1) vs
    NPU(batch16) 임베딩이 수치적으로 같은지 측정한다.

    실측(bench결과)에서 NPU 는 batch=1 만 정확(97.6%)하고 batch≥4 는 78~85% 로
    떨어졌다.  배치 추론이 임베딩 벡터 자체를 바꾸면(코사인<1·L2>0) 후보 선정이
    틀어져 정확도가 깨지는 것이므로, 이 진단이 그 직접 증거를 제공한다.

    반환: {'rows': [...per-image...], 'summary': {pair: {cos_mean,cos_min,l2_mean}}}.
    openvino 미가용이면 {'error': ...}.  GUI/CLI 양쪽에서 호출 가능."""
    try:
        from ..learning import embedder_openvino as _ov
    except Exception as exc:
        return {"error": f"openvino 미가용: {exc}"}
    import numpy as np
    model = model or _rx.MODEL_MOBILENET_V3
    paths = [Path(p) for p in paths][:max_images]
    if not paths:
        return {"error": "이미지 경로가 없습니다"}
    cfg = Recipe(key="diag", name="diag", recall=RECALL_NPU,
                 scoring=SCORE_FUSION, embed_model=model).to_cfg()

    def _emb(device, batch):
        return _ov.device_embed(paths, model_kind=model, device=device,
                                cfg=cfg, jobs=8, batch=int(batch))

    configs = {"gpu_b1": ("GPU", 1), "npu_b1": ("NPU", 1), "npu_b16": ("NPU", 16)}
    embs: dict = {}
    errors: dict = {}
    for name, (dev, b) in configs.items():
        try:
            embs[name] = _emb(dev, b)
        except Exception as exc:                 # 장치 없음/실패 — 그 설정만 건너뜀
            errors[name] = str(exc)

    # 비교 대상 쌍 — 핵심은 npu_b1 vs npu_b16(배치 효과)과 npu_* vs gpu_b1(장치 효과).
    pairs = [("npu_b1", "npu_b16"), ("gpu_b1", "npu_b1"), ("gpu_b1", "npu_b16")]
    rows = []
    summary = {}
    for x, y in pairs:
        if x not in embs or y not in embs:
            continue
        coss, l2s = [], []
        for p in paths:
            ax, ay = embs[x].get(p), embs[y].get(p)
            if ax is None or ay is None:
                continue
            c = _cosine(ax, ay)
            l2 = float(np.linalg.norm(np.asarray(ax, "float64").ravel()
                                      - np.asarray(ay, "float64").ravel()))
            coss.append(c)
            l2s.append(l2)
            rows.append({"image": p.name, "pair": f"{x}|{y}", "cosine": c, "l2": l2})
        if coss:
            summary[f"{x}|{y}"] = {
                "cos_mean": float(np.mean(coss)), "cos_min": float(np.min(coss)),
                "l2_mean": float(np.mean(l2s)), "n": len(coss)}
    return {"model": model, "n_images": len(paths), "errors": errors,
            "summary": summary, "rows": rows}


# ---------------------------------------------------------------------------
# 정확도 평가 — recall@K(GT) + 기준선 대비 일치율
# ---------------------------------------------------------------------------
def evaluate(results: Results, gt: Dict[Tuple[str, str], set]) -> Tuple[Optional[float], Optional[float], int]:
    """GT 대비 recall@1, recall@5 와 평가 ref 수.  GT 없으면 (None,None,0)."""
    if not gt:
        return None, None, 0
    n = 0
    hit1 = 0
    hit5 = 0
    for key, ranked in results.items():
        correct = gt.get(key)
        if not correct:
            continue
        n += 1
        top = [vp for vp, _ in ranked]
        if top[:1] and top[0] in correct:
            hit1 += 1
        if any(vp in correct for vp in top[:5]):
            hit5 += 1
    if n == 0:
        return None, None, 0
    return round(hit1 / n, 4), round(hit5 / n, 4), n


CAND_RECALL_KS = (5, 10, 20, 40, 100)


def candidate_recall(embed_order: Dict[Tuple[str, str], List[str]],
                     gt: Dict[Tuple[str, str], set],
                     ks=CAND_RECALL_KS):
    """임베딩(GPU/NPU) 후보 순위에서 **정답이 몇 위에 있나**를 집계.

    반환 ``({K: recall@K}, worst_correct_rank, n)``.  ``embed_order`` 는 쿼리별
    임베딩 코사인 내림차순 val 경로 리스트(=CPU 재채점 전 후보 순서).
      · recall@K = 정답이 top-K 안에 든 쿼리 비율 → topk 재채점이 그 정답을 포함하는지.
      · worst_correct_rank = 정답의 최악 순위 → **100% 안 놓치려면 필요한 최소 topk**.
    검증셋이 커질수록(수백 장) 정답이 더 밀릴 수 있어, 이 값으로 안전한 topk 를 정한다.
    순수 함수(헤드리스 테스트)."""
    ranks: List[Optional[int]] = []
    for key, order in embed_order.items():
        correct = gt.get(key)
        if not correct:
            continue
        r = next((i + 1 for i, vp in enumerate(order) if vp in correct), None)
        ranks.append(r)
    n = len(ranks)
    if n == 0:
        return {k: None for k in ks}, None, 0
    out = {}
    for k in ks:
        hit = sum(1 for r in ranks if r is not None and r <= k)
        out[k] = round(hit / n, 4)
    valid = [r for r in ranks if r is not None]
    worst = max(valid) if valid else None
    return out, worst, n


def query_failures(results: Results, gt: Dict[Tuple[str, str], set]) -> List[dict]:
    """쿼리별 top-1 정오 + 정답 순위.  recall@1 이 놓친 '그 쿼리'를 짚어내는 데 쓴다.

    각 항목: {slot, query(ref 경로), ok(top1 정답?), top1(예측), correct_rank(정답이 몇
    위), n_correct}.  순수 함수(헤드리스 테스트) — 예측 결과만 있으면 GT 와 대조한다."""
    out: List[dict] = []
    for key, ranked in results.items():
        correct = gt.get(key)
        if not correct:
            continue
        top = [vp for vp, _ in ranked]
        ok = bool(top[:1] and top[0] in correct)
        rank = next((i + 1 for i, vp in enumerate(top) if vp in correct), None)
        out.append({"slot": key[0], "query": key[1], "ok": ok,
                    "top1": top[0] if top else None, "correct_rank": rank,
                    "n_correct": len(correct)})
    return out


def agreement(results: Results, baseline: Results) -> Optional[float]:
    """기준선(보통 CPU 고전 전수)의 top-1 과 일치하는 ref 비율 — '정확도 보존' 측정."""
    if not baseline:
        return None
    n = 0
    agree = 0
    for key, base_ranked in baseline.items():
        if not base_ranked:
            continue
        ranked = results.get(key)
        if not ranked:
            continue
        n += 1
        if ranked[0][0] == base_ranked[0][0]:
            agree += 1
    return round(agree / n, 4) if n else None


def accuracy_metric(run: RecipeRun) -> Optional[float]:
    """추천에 쓰는 단일 정확도 지표 — GT recall@1 우선, 없으면 기준선 일치율."""
    if run.recall1 is not None:
        return run.recall1
    return run.agree1


def _evaluate_run(run: RecipeRun, res: Results, baseline_results: Results,
                  gt) -> None:
    """한 레시피 결과의 정확도(recall@K + 기준선 일치율)를 ``run`` 에 채운다."""
    if not res:
        return
    r1, r5, _n = evaluate(res, gt)
    run.recall1, run.recall5 = r1, r5
    run.agree1 = (1.0 if run.key == _rx.BASELINE_ACCURACY_KEY
                  else agreement(res, baseline_results))


# ---------------------------------------------------------------------------
# 스위트 실행 — 순차 + 타임아웃 + 메모리 안전, 그리고 추천
# ---------------------------------------------------------------------------
@dataclass
class SuiteResult:
    runs: List[RecipeRun] = field(default_factory=list)
    recommended_key: str = ""
    baseline_key: str = ""
    production_key: str = ""
    speedup_vs_production: Optional[float] = None
    has_gt: bool = False
    devices: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _run_with_timeout(fn, timeout_sec: float):
    """``fn(stop)`` 를 스레드로 돌리고 ``timeout_sec`` 내 미완료면 stop 신호+표시."""
    box = {}
    ev = threading.Event()

    def _target():
        try:
            box["val"] = fn(ev.is_set)
        except Exception as e:           # pragma: no cover - 방어
            box["err"] = e

    th = threading.Thread(target=_target, daemon=True)
    th.start()
    th.join(timeout=timeout_sec if timeout_sec and timeout_sec > 0 else None)
    timed_out = th.is_alive()
    if timed_out:
        ev.set()
        th.join(timeout=10.0)
    if "err" in box:
        raise box["err"]
    return box.get("val"), timed_out


def run_suite(ds: Dataset, recipes: Optional[List[Recipe]] = None, *,
              threshold: float = 0.0,
              per_recipe_timeout: float = 0.0,
              include_diagnostic: bool = False,
              skip_redundant: bool = True,
              skip_low_history: bool = True,
              explicit_keys: Optional[set] = None,
              progress: Optional[Callable[..., None]] = None,
              checkpoint: Optional[Callable[["SuiteResult"], None]] = None,
              stop: Optional[Callable[[], bool]] = None) -> SuiteResult:
    """레시피들을 **순차** 실행(로딩 안전)하고 정확도/속도/추천을 산출한다.

    '불필요한 테스트는 하지 않는다'(사용자 요구):
    - ``include_diagnostic=False``(기본): 함정/대조용(diagnostic) 레시피는 사용자가
      **명시적으로 그 키를 고른 게 아니면** 실행하지 않는다(예: gpu_fusion_b1 함정).
    - ``skip_redundant=True``(기본): 필요한 가속 장치/패키지가 없어 어차피 CPU 고전
      으로 폴백해 **같은 결과를 반복**할 레시피는 측정하지 않고 'skipped' 로 기록한다.
    - ``skip_low_history=True``(기본): **이전 실험에서 운영 대비 정확도가 낮았던**
      레시피는 이번에도 측정하지 않는다(과거 기록 기반).  명시 선택한 키는 예외.

    ``explicit_keys`` 는 사용자가 **개별 키로 직접 고른** 집합이다(그룹/전체로 펼쳐진
    것은 제외).  여기 든 키는 어떤 스킵 규칙도 적용하지 않고 그대로 측정한다.  None
    이면 '개별 명시 없음'으로 보고 모든 스킵 규칙을 적용한다(그룹/전체 실행).
    """
    recipes = recipes if recipes is not None else list(_rx.REGISTRY)
    devices = detect_devices()
    # 과거 저성능 레시피(운영보다 정확도가 낮았던) — 명시 선택하지 않은 것만 스킵.
    low_hist = low_performers() if skip_low_history else {}
    suite = SuiteResult(baseline_key=_rx.BASELINE_ACCURACY_KEY,
                        production_key=_rx.PRODUCTION_SPEED_KEY,
                        has_gt=bool(ds.gt), devices=sorted(devices))

    # 함정/대조용은 사용자가 그 키를 **개별 명시**했을 때만 둔다.  '전체/그룹'으로
    # 들어온 diagnostic 은 제외해 불필요한 장시간 측정(예: batch1 함정)을 막는다.
    explicit = set(explicit_keys or set())
    if not include_diagnostic:
        recipes = [r for r in recipes
                   if (not getattr(r, "diagnostic", False)) or r.key in explicit]

    # 정확도 기준선(GT 없을 때 일치율 산정용)은 항상 먼저 한 번 확보한다.
    baseline_recipe = _rx.by_key(_rx.BASELINE_ACCURACY_KEY)
    if baseline_recipe not in recipes:
        recipes = [baseline_recipe] + list(recipes)

    def _emit(name, done, total, key=""):
        """progress 콜백 호출 — 키(4번째 인자)를 받는 콜백/안 받는 콜백 모두 지원."""
        if not progress:
            return
        try:
            progress(name, done, total, key)
        except TypeError:                 # 구형 3-인자 콜백 호환
            progress(name, done, total)

    def _checkpoint():
        """레시피 하나가 끝날 때마다 부분 결과를 저장(자식 크래시 대비 복구점)."""
        if not checkpoint:
            return
        try:
            checkpoint(suite)
        except Exception:                 # 저장 실패가 측정을 막지 않게
            pass

    def _append(run):
        suite.runs.append(run)
        _checkpoint()

    all_results: Dict[str, Results] = {}
    total = len(recipes)
    for i, recipe in enumerate(recipes, start=1):
        if stop and stop():
            break
        _emit(recipe.name, i - 1, total, recipe.key)

        # 불필요한 레시피는 측정하지 않고 건너뛴다 — 단, 정확도 기준선은 항상
        # 실행(다른 레시피의 일치율 평가에 필요)하고, 사용자가 정확히 그 키를
        # 명시 선택(explicit)했으면 존중해 그래도 측정한다.
        if recipe.key != _rx.BASELINE_ACCURACY_KEY and recipe.key not in explicit:
            why = ""
            if skip_redundant:
                why = skip_reason(recipe, devices)
            if not why and skip_low_history and recipe.key in low_hist:
                why = low_hist[recipe.key]
            if why:
                _append(RecipeRun(
                    key=recipe.key, name=recipe.name, ok=False, skipped=True,
                    note=why, desc=recipe.desc))
                continue

        def _do(local_stop):
            with PeakMem() as pm:
                res, run = run_recipe(ds, recipe, threshold=threshold,
                                      devices=devices,
                                      stop=lambda: (local_stop() or (stop and stop())))
            run.peak_mb = pm.peak_mb
            return res, run

        try:
            out, timed_out = _run_with_timeout(_do, per_recipe_timeout)
        except Exception as e:
            _append(RecipeRun(key=recipe.key, name=recipe.name, ok=False,
                              note=f"실패: {e}", desc=recipe.desc))
            continue
        if out is None:
            _append(RecipeRun(key=recipe.key, name=recipe.name, ok=False,
                              timed_out=timed_out,
                              note="타임아웃" if timed_out else "결과 없음",
                              desc=recipe.desc))
            continue
        res, run = out
        run.timed_out = run.timed_out or timed_out
        if timed_out:
            run.note = (run.note + " · 타임아웃 중단").strip(" ·")
            run.ok = False
        all_results[recipe.key] = res
        # 정확도를 **즉시** 평가한다(기준선은 항상 먼저 실행되므로 사용 가능).
        # 이렇게 해야 부분 저장(체크포인트)에도 정확도가 포함돼 크래시 복구가 온전하다.
        _evaluate_run(run, res, all_results.get(_rx.BASELINE_ACCURACY_KEY, {}), ds.gt)
        _append(run)

    suite.recommended_key = recommend(suite.runs)
    _fill_speedup(suite)
    _checkpoint()
    _emit("완료", total, total, "")
    return suite


def recommend(runs: List[RecipeRun]) -> str:
    """'현행(운영) 대비 정확도가 낮지 않으면서 가장 빠른' 레시피 키.

    정확도가 떨어지는 가속은 절대 추천하지 않는다(사용자 핵심 요구).  운영 레시피
    측정값이 있으면 그 정확도를 하한으로, 없으면 관측된 최고 정확도를 하한으로 쓴다."""
    done = [r for r in runs if r.ok and not r.timed_out and r.total_sec > 0]
    if not done:
        return ""
    prod = next((r for r in done if r.key == _rx.PRODUCTION_SPEED_KEY), None)
    accs = [accuracy_metric(r) for r in done if accuracy_metric(r) is not None]
    if prod is not None and accuracy_metric(prod) is not None:
        floor = accuracy_metric(prod)
    elif accs:
        floor = max(accs)
    else:
        floor = None
    eps = 1e-6
    if floor is None:
        # 정확도 정보가 전혀 없으면(평가 불가) 추천을 보류하지 않고 최속을 고르되
        # note 로 한계를 남긴다.
        return min(done, key=lambda r: r.total_sec).key
    eligible = [r for r in done
                if (accuracy_metric(r) is not None
                    and accuracy_metric(r) >= floor - eps)]
    if not eligible:
        eligible = done
    return min(eligible, key=lambda r: r.total_sec).key


def _fill_speedup(suite: SuiteResult) -> None:
    by_key = {r.key: r for r in suite.runs}
    prod = by_key.get(suite.production_key)
    rec = by_key.get(suite.recommended_key)
    if prod and rec and rec.total_sec > 1e-6:
        suite.speedup_vs_production = round(prod.total_sec / rec.total_sec, 2)


# ---------------------------------------------------------------------------
# 기록 — JSON + 사람이 읽는 표(마크다운)
# ---------------------------------------------------------------------------
def default_out_dir() -> Path:
    from ..utils import paths, run_log
    d = paths.cache_root() / "dev_bench" / run_log.machine_id()
    d.mkdir(parents=True, exist_ok=True)
    return d


def iter_history(extra_dirs: Optional[List[Path]] = None) -> List[dict]:
    """과거 벤치 기록(result.json)들을 모아 리스트로 반환(최신순 아님, 단순 수집).

    기본 기록 폴더(``dev_bench/<host>/*/result.json``) + 저장소의 ``bench결과/`` +
    ``extra_dirs`` 를 훑는다.  손상/무관 파일은 건너뛴다."""
    out: List[dict] = []
    seen: set = set()
    roots: List[Path] = []
    try:
        roots.append(default_out_dir())
    except Exception:
        pass
    try:
        from ..utils import paths as _paths
        roots.append(_paths._project_root() / "bench결과")
    except Exception:
        pass
    for d in (extra_dirs or []):
        roots.append(Path(d))
    for root in roots:
        if not root or not Path(root).exists():
            continue
        # result.json 이 root 바로 아래거나 한 단계 하위(타임스탬프 폴더)일 수 있다.
        cands = list(Path(root).glob("result.json")) + \
            list(Path(root).glob("*/result.json"))
        for f in cands:
            rp = str(f.resolve())
            if rp in seen:
                continue
            seen.add(rp)
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
    return out


def low_performers(history: Optional[List[dict]] = None, *,
                   margin: float = 0.03) -> Dict[str, str]:
    """과거 기록에서 '성능(실제 정확도)이 낮았던' 레시피 키 → 사유 매핑.

    각 기록의 운영(production) 정확도를 그 회차의 하한으로 삼아, **운영보다
    정확도가 margin(기본 3%p) 넘게 낮았던** 레시피를 저성능으로 본다.  작은 차이
    (≤margin)는 데이터/측정 노이즈로 보고 관용한다(사용자 아이디어가 근소차로
    잘려나가지 않게).  한 번이라도 운영-margin 이상을 기록하면 제외하지 않는다.
    정확도 지표는 recall@1 우선, 없으면 기준선 일치율(agree1).  가속기 없는 CPU
    폴백 기록은 변별력이 없어 판정에서 제외한다.

    이걸로 '이전에 낮았던 항목은 이번 실험에서 스킵' 을 구현한다(명시 선택 시 예외)."""
    history = history if history is not None else iter_history()

    def _acc(run: dict):
        v = run.get("recall1")
        return v if v is not None else run.get("agree1")

    ever_ok: set = set()
    bad: Dict[str, float] = {}        # key → 관측된 최악(운영대비) 정확도 차
    for rec in history:
        runs = rec.get("runs") or []
        # 가속기 없는 CPU 폴백 기록은 모든 임베딩 레시피가 동일 CPU 결과로 폴백돼
        # 변별력이 없다 — 저성능 판정에서 제외(실제 GPU/NPU 측정만 신뢰).
        if not (rec.get("devices") or []):
            continue
        prod_key = rec.get("production_key") or _rx.PRODUCTION_SPEED_KEY
        prod = next((r for r in runs if r.get("key") == prod_key), None)
        floor = _acc(prod) if prod else None
        if floor is None:
            continue
        for r in runs:
            k = r.get("key")
            if not k or k == prod_key:
                continue
            # 폴백/스킵된 항목은 그 회차에서 변별 불가 → 판정 근거에서 제외.
            if r.get("fell_back_classical") or r.get("skipped"):
                continue
            a = _acc(r)
            if a is None or not r.get("ok", True):
                continue
            if a + 1e-9 >= floor - margin:
                ever_ok.add(k)
            else:
                bad[k] = max(bad.get(k, 0.0), floor - a)
    out: Dict[str, str] = {}
    for k, gap in bad.items():
        if k in ever_ok:
            continue                  # 다른 회차에서 운영 이상 → 저성능으로 단정 안 함
        out[k] = f"과거 실험에서 운영 대비 정확도 {gap*100:.1f}%p 낮음 → 건너뜀"
    return out


def result_payload(suite: SuiteResult, ds: Dataset) -> dict:
    """``result.json`` 으로 직렬화할 dict — 부분 저장(체크포인트)과 최종 보고서가 공유."""
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ref_root": ds.ref_root,
        "val_root": ds.val_root,
        "n_slots": len(ds.tasks),
        "n_images": ds.n_images(),
        "n_pairs": ds.n_pairs(),
        "has_ground_truth": suite.has_gt,
        "devices": suite.devices,
        "baseline_key": suite.baseline_key,
        "production_key": suite.production_key,
        "recommended_key": suite.recommended_key,
        "speedup_vs_production": suite.speedup_vs_production,
        "runs": [asdict(r) for r in suite.runs],
    }


def write_result_json(run_dir: Path, suite: SuiteResult, ds: Dataset) -> Path:
    """``run_dir/result.json`` 만 쓴다(레시피마다 갱신되는 부분 저장에 사용)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "result.json"
    p.write_text(json.dumps(result_payload(suite, ds), ensure_ascii=False,
                            indent=2), encoding="utf-8")
    return p


def write_report(suite: SuiteResult, ds: Dataset, out_dir: Optional[Path] = None,
                 *, run_dir: Optional[Path] = None) -> Path:
    """스위트 결과를 JSON+MD 로 저장하고 폴더를 반환.

    ``run_dir`` 가 주어지면 그 폴더에 그대로 쓰고(부분 저장과 같은 폴더에 최종 확정),
    없으면 ``out_dir/<timestamp>/`` 를 새로 만든다."""
    if run_dir is None:
        out_dir = Path(out_dir) if out_dir else default_out_dir()
        run_dir = out_dir / time.strftime("%Y%m%d-%H%M%S")
    run_dir = Path(run_dir)
    write_result_json(run_dir, suite, ds)
    (run_dir / "report.md").write_text(render_markdown(suite, ds), encoding="utf-8")
    return run_dir


def render_markdown(suite: SuiteResult, ds: Dataset) -> str:
    L: List[str] = []
    L.append("# 매칭 가속 조합 벤치마크 결과\n")
    L.append(f"- 측정 시각: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"- 데이터: slot {len(ds.tasks)}개 · 이미지 {ds.n_images()}장 · "
             f"쌍 {ds.n_pairs():,}개")
    L.append(f"- 감지 가속 장치: {', '.join(suite.devices) or '없음(CPU만)'}")
    L.append(f"- 정확도 기준: {'정답 라벨 recall@1' if suite.has_gt else 'CPU 고전 전수 대비 top-1 일치율'}")
    L.append("- 캐시: **우회(처음 매칭처럼 측정)**\n")

    def acc_str(r: RecipeRun) -> str:
        if r.recall1 is not None:
            return f"{r.recall1*100:.1f}%"
        if r.agree1 is not None:
            return f"{r.agree1*100:.1f}%"
        return "-"

    measured = [r for r in suite.runs if not getattr(r, "skipped", False)]
    skipped = [r for r in suite.runs if getattr(r, "skipped", False)]

    L.append("| 레시피 | 총시간(s) | 임베딩(s) | 재채점(s) | img/s | 피크MB | 정확도 | 비고 |")
    L.append("|---|--:|--:|--:|--:|--:|--:|---|")
    for r in sorted(measured, key=lambda x: (not x.ok, x.total_sec or 1e9)):
        mark = " ⭐" if r.key == suite.recommended_key else ""
        peak = f"{r.peak_mb:.0f}" if r.peak_mb else "-"
        note = r.note or ("폴백" if r.fell_back_classical else "")
        L.append(f"| {r.name}{mark} | {r.total_sec:.2f} | {r.embed_sec:.2f} | "
                 f"{r.score_sec:.2f} | {r.img_per_sec:.1f} | {peak} | "
                 f"{acc_str(r)} | {note} |")

    # 임베딩 후보 recall — GPU/NPU 가 추린 순위에서 정답이 몇 위에 있나.  검증셋이 커질수록
    # (수백 장) 정답이 밀릴 수 있어, topk 재채점이 정답을 자르지 않는지 판단하는 핵심 표.
    cand = [r for r in measured if r.embed_recall and r.cand_n]
    if cand:
        L.append("\n### 임베딩 후보 recall — 정답이 GPU/NPU 순위 몇 위에 있나(CPU 재채점 전)")
        L.append("> `worst순위`=정답의 최악 순위(=**놓치지 않을 최소 topk**).  검증셋이 크면 "
                 "이 값이 커질 수 있으니, 운영 topk(=후보의 최소 절반·최소 40)가 이보다 큰지 본다.")
        ks = sorted(int(k) for k in (cand[0].embed_recall or {}).keys())
        L.append("| 임베딩 모델 · 레시피 | "
                 + " | ".join(f"@{k}" for k in ks) + " | worst순위 | 쿼리수 |")
        L.append("|---|" + "--:|" * (len(ks) + 2))
        for r in cand:
            cells = " | ".join(
                (f"{(r.embed_recall.get(str(k)) or 0)*100:.0f}%") for k in ks)
            L.append(f"| {r.name} | {cells} | "
                     f"{r.worst_correct_rank if r.worst_correct_rank else '-'} | {r.cand_n} |")

    # 불필요해서 측정하지 않은(건너뛴) 레시피 — 사유와 함께 투명하게 남긴다.
    if skipped:
        L.append("\n### 건너뛴 레시피(불필요 — 측정 안 함)")
        L.append("| 레시피 | 사유 |")
        L.append("|---|---|")
        for r in sorted(skipped, key=lambda x: x.name):
            L.append(f"| {r.name} (`{r.key}`) | {r.note} |")

    # 임베딩 후보 recall — 정답이 GPU/NPU 순위 몇 위에 있나(CPU 재채점 전).  검증셋이
    # 커지면(수백 장) 정답이 밀릴 수 있어, topk 재채점이 정답을 자르지 않는지 판단하는 표.
    cand = [r for r in measured if r.embed_recall and r.cand_n]
    if cand:
        ks = sorted(int(k) for k in (cand[0].embed_recall or {}).keys())
        L.append("\n### 임베딩 후보 recall — 정답이 GPU/NPU 순위 몇 위에(CPU 재채점 전)")
        L.append("> `worst순위`=정답의 최악 순위(=**놓치지 않을 최소 topk**). 운영 재채점은 "
                 "후보의 최소 절반(≥40)이라, 이 값이 그보다 작으면 안전.")
        L.append("| 레시피 | " + " | ".join(f"@{k}" for k in ks) + " | worst순위 | 쿼리수 |")
        L.append("|---|" + "--:|" * (len(ks) + 2))
        for r in cand:
            cells = " | ".join(f"{(r.embed_recall.get(str(k)) or 0)*100:.0f}%" for k in ks)
            L.append(f"| {r.name} | {cells} | "
                     f"{r.worst_correct_rank if r.worst_correct_rank else '-'} | {r.cand_n} |")

    # NPU 사용량 — 'NPU 를 얼마나 바쁘게 굴렸나'(드라이브 설정 + 추론시간/처리량/가동률).
    npu = [r for r in measured if r.npu_used]
    if npu:
        L.append("\n### NPU 사용량(가동률 프록시)")
        L.append("> 진짜 HW% 는 플랫폼 의존이라, 드라이브 설정과 NPU 추론 시간/처리량/가동률로 본다.")
        L.append("| 레시피 | NPU시간(s) | NPU img/s | 가동률 | 드라이브 설정 |")
        L.append("|---|--:|--:|--:|---|")
        for r in npu:
            bf = f"{r.npu_busy_frac*100:.0f}%" if r.npu_busy_frac is not None else "-"
            L.append(f"| {r.name} | {r.npu_sec:.2f} | {r.npu_throughput:.1f} | {bf} | "
                     f"{r.npu_drive} |")

    L.append("")
    rec = next((r for r in suite.runs if r.key == suite.recommended_key), None)
    if rec is not None:
        L.append(f"## 추천: **{rec.name}**")
        L.append(f"- 이유: 운영 대비 정확도를 보존하면서 가장 빠름.")
        if suite.speedup_vs_production:
            L.append(f"- 현행(`{suite.production_key}`) 대비 속도: "
                     f"**×{suite.speedup_vs_production}**")
        L.append(f"- 연산 방식: {rec.desc}")
    L.append("\n## 각 레시피의 연산 방식")
    for r in suite.runs:
        L.append(f"- **{r.name}** (`{r.key}`): {r.desc}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# 자기검증용 — 기준 폴더에서 정답이 있는 검증셋을 합성(증강)
# ---------------------------------------------------------------------------
def synthesize_val(ref_root, out_root, *, copies: int = 1) -> dict:
    """``ref_root`` 의 각 사진을 라벨 보존 증강해 ``out_root`` 에 검증셋을 만든다.

    같은 (slot, 파일) 의 증강본이 정답이 되도록 GT 라벨(dict)을 함께 돌려준다.
    가속 장치/2호기 데이터가 없어도 **실제 이미지로 정확도를 측정**하기 위한
    자기검증 모드.  cv2/numpy 만 사용한다."""
    import cv2
    import numpy as np
    ref_root = Path(ref_root)
    out_root = Path(out_root)
    labels: dict = {}
    exts = (".jpg", ".jpeg", ".png", ".bmp")
    slot_dirs = [d for d in sorted(ref_root.iterdir()) if d.is_dir()]
    if not slot_dirs:                       # 평면 폴더면 그 자체를 한 slot 으로
        slot_dirs = [ref_root]
    rng = np.random.default_rng(12345)
    for sd in slot_dirs:
        slot = sd.name if sd is not ref_root else ref_root.name
        dst_dir = out_root / slot
        dst_dir.mkdir(parents=True, exist_ok=True)
        refmap: dict = {}
        for img in sorted(sd.iterdir()):
            if img.suffix.lower() not in exts:
                continue
            arr = cv2.imread(str(img))
            if arr is None:
                continue
            ref_path = str(img)
            correct = []
            for c in range(max(1, copies)):
                aug = _augment(arr, rng)
                dst = dst_dir / f"{img.stem}__aug{c}{img.suffix}"
                cv2.imwrite(str(dst), aug)
                correct.append(str(dst))
            refmap[ref_path] = correct
        if refmap:
            labels[slot] = refmap
    return labels


def _augment(arr, rng):
    """라벨을 바꾸지 않는 약한 증강 — 밝기/대비/노이즈/미세 회전(호기 차 모사)."""
    import cv2
    import numpy as np
    h, w = arr.shape[:2]
    alpha = float(rng.uniform(0.9, 1.1))           # 대비
    beta = float(rng.uniform(-12, 12))             # 밝기
    out = cv2.convertScaleAbs(arr, alpha=alpha, beta=beta)
    ang = float(rng.uniform(-2.0, 2.0))            # 미세 회전
    M = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    out = cv2.warpAffine(out, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    noise = rng.normal(0, 3.0, out.shape).astype(np.float32)
    out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    return out


# ---------------------------------------------------------------------------
# 프로세스 격리 실행 — 레시피 실행(네이티브 크래시 위험)을 자식 프로세스로 돌리고,
# 자식이 죽으면 범인 레시피를 빼고 이어서 다시 띄운다.  부모(GUI)는 안 죽는다.
# 아래는 spawn 주입이 가능한 '순수 오케스트레이션'이라 헤드리스로 단위 테스트된다.
# ---------------------------------------------------------------------------
@dataclass
class ChildOutcome:
    """자식 한 번 실행의 결과."""
    returncode: int                       # 0=정상 완료, 그 외=크래시/중단
    last_started_key: str = ""            # 마지막으로 '시작'된 레시피 키(크래시 범인 추정)
    payload: Optional[dict] = None        # 자식이 남긴 result.json 내용(부분 저장 포함)


def reconstruct_run(d: dict) -> RecipeRun:
    """result.json 의 run dict 를 RecipeRun 으로 복원(여분 키는 무시)."""
    from dataclasses import fields
    allowed = {f.name for f in fields(RecipeRun)}
    return RecipeRun(**{k: v for k, v in d.items() if k in allowed})


def _run_rank(r: RecipeRun) -> int:
    """merge 시 같은 키 중 무엇을 남길지 — 실제 측정된 것을 실패/스킵보다 우선."""
    if r.ok and not r.skipped and (r.total_sec or 0) > 0:
        return 2
    if not r.skipped:
        return 1
    return 0


def merge_suite(payloads: List[dict],
                extra_runs: Optional[List[RecipeRun]] = None) -> SuiteResult:
    """여러 자식의 result.json(payloads) + 부모가 만든 크래시 run 을 합쳐 한 스위트로.

    같은 키가 여러 번 나오면(재실행으로 기준선 중복 등) '실제 측정' 쪽을 남기고,
    추천/배속은 합본 기준으로 다시 계산한다."""
    best: Dict[str, RecipeRun] = {}
    order: List[str] = []
    meta: Dict[str, object] = {}

    def _consider(run: RecipeRun) -> None:
        if run.key not in best:
            best[run.key] = run
            order.append(run.key)
        elif _run_rank(run) > _run_rank(best[run.key]):
            best[run.key] = run

    for p in payloads or []:
        for mk in ("baseline_key", "production_key", "has_ground_truth", "devices"):
            if mk not in meta and p.get(mk) is not None:
                meta[mk] = p[mk]
        for d in p.get("runs", []):
            _consider(reconstruct_run(d))
    for run in extra_runs or []:
        _consider(run)

    suite = SuiteResult(
        baseline_key=str(meta.get("baseline_key", _rx.BASELINE_ACCURACY_KEY)),
        production_key=str(meta.get("production_key", _rx.PRODUCTION_SPEED_KEY)),
        has_gt=bool(meta.get("has_ground_truth", False)),
        devices=list(meta.get("devices", []) or []),
        runs=[best[k] for k in order],
    )
    suite.recommended_key = recommend(suite.runs)
    _fill_speedup(suite)
    return suite


def _crashed_run(key: str) -> RecipeRun:
    """자식 프로세스를 죽인 것으로 추정되는 레시피의 실패 run(표/기록용)."""
    try:
        rc = _rx.by_key(key)
        name, desc = rc.name, rc.desc
    except Exception:
        name, desc = key, ""
    return RecipeRun(key=key, name=name, ok=False,
                     note="워커 프로세스가 종료됨(네이티브 크래시 추정) — 제외하고 계속",
                     desc=desc)


def drive_isolated_suite(keys: List[str], *, spawn: Callable[[List[str]], ChildOutcome],
                         stop: Optional[Callable[[], bool]] = None,
                         max_respawns: int = 20) -> SuiteResult:
    """``keys`` 를 자식으로 실행하되, 크래시면 범인을 빼고 살아남은 것만 이어서 측정.

    ``spawn(keys_subset) -> ChildOutcome`` 가 실제 자식 실행(주입점, 테스트는 가짜로 대체).
    반환은 모든 자식 결과 + 크래시 표시를 합본한 ``SuiteResult``."""
    payloads: List[dict] = []
    crashed: List[RecipeRun] = []
    done: set = set()
    remaining = list(dict.fromkeys(keys))     # 순서 보존 + 중복 제거
    guard = 0
    while remaining:
        if stop and stop():
            break
        guard += 1
        if guard > max_respawns:
            break
        outcome = spawn(remaining)
        if outcome.payload:
            payloads.append(outcome.payload)
            for d in outcome.payload.get("runs", []):
                if d.get("key"):
                    done.add(d["key"])
        if outcome.returncode == 0:
            break                              # 정상 완료
        # 비정상 종료(크래시/멈춤) — 범인을 빼고 살아남은 키로 재시도.
        culprit = outcome.last_started_key
        if culprit and culprit not in done:
            crashed.append(_crashed_run(culprit))
            done.add(culprit)
        new_remaining = [k for k in remaining if k not in done]
        if new_remaining == remaining:
            # 아무 진행도 없었다(첫 키에서 즉사 등) → 무한루프 방지 위해 첫 키를 버린다.
            if remaining:
                crashed.append(_crashed_run(remaining[0]))
                new_remaining = remaining[1:]
        remaining = new_remaining
    return merge_suite(payloads, crashed)


# ---------------------------------------------------------------------------
# CLI — 헤드리스 실험·기록
# ---------------------------------------------------------------------------
def _print_table(suite: SuiteResult, ds: Dataset) -> None:
    print(render_markdown(suite, ds))


def main(argv=None) -> int:
    import argparse
    import tempfile
    ap = argparse.ArgumentParser(
        description="매칭 가속 조합(CPU/GPU/NPU) 벤치마크 — 캐시 우회, 정확도 보존")
    ap.add_argument("--ref", help="기준(reference) 최상위 폴더")
    ap.add_argument("--val", help="검증(validation) 최상위 폴더")
    ap.add_argument("--labels", help="정답 라벨 JSON 경로(없으면 기준선 일치율 사용)")
    ap.add_argument("--self-test", action="store_true",
                    help="--ref 의 사진을 증강해 정답 있는 검증셋을 합성(2호기 불필요)")
    ap.add_argument("--recipes", default="top5",
                    help="콤마 구분 레시피 키 또는 프리셋: top5(기본·최종 후보)·final(고전2회+TOP5)"
                         "·all+(아카이브 전부)·그룹명(보존)")
    ap.add_argument("--max-slots", type=int, default=0, help="서브샘플: slot 수 상한")
    ap.add_argument("--max-images", type=int, default=0,
                    help="서브샘플: 측당 이미지 수 상한")
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="레시피별 타임아웃(초, 0=무제한)")
    ap.add_argument("--threshold", type=float, default=0.0)
    ap.add_argument("--out", help="기록 폴더(기본: 캐시/dev_bench/<host>)")
    ap.add_argument("--all-recipes", action="store_true",
                    help="불필요 스킵 해제 — 함정/대조·폴백중복·과거저성능까지 전부 측정")
    ap.add_argument("--explicit",
                    help="스킵 면제(개별 명시) 키 콤마목록 — 미지정 시 --recipes 에서 추론"
                         "(GUI 가 '개별 체크' 와 '그룹 펼침' 을 구분해 넘길 때 사용)")
    ap.add_argument("--list", action="store_true", help="레시피 목록만 출력")
    ap.add_argument("--npu-embed-diag", action="store_true",
                    help="NPU 배치 정확도 붕괴 원인 규명 — GPU vs NPU(b1) vs NPU(b16) "
                         "임베딩 일치도(코사인/L2) 측정.  --ref 사진 사용.")
    ap.add_argument("--miss-report", action="store_true",
                    help="recall@1 이 놓친 '그 쿼리'를 짚어낸다 — gold(전수)는 맞히고 "
                         "--recipes 가 놓치는 쿼리를 슬롯·파일명·정답순위로 출력(--labels 필요).")
    ap.add_argument("--emit-progress", action="store_true",
                    help="레시피 시작/완료를 기계가 읽는 '@@AOI_PROG' 줄로 출력"
                         "(GUI 가 별도 프로세스로 실행하며 진행/크래시 추적에 사용)")
    ap.add_argument("--make-labels-template",
                    help="정답 라벨 빈 템플릿 JSON 을 생성할 경로(--ref/--val 필요)")
    ap.add_argument("--labels-stats",
                    help="정답 라벨 JSON 의 통계(정답수/없음/복수)만 출력")
    args = ap.parse_args(argv)

    if args.list:
        for r in _rx.REGISTRY:
            print(f"{r.key:24s} {r.name}\n    {r.desc}")
        return 0

    if args.npu_embed_diag:
        if not args.ref:
            ap.error("--npu-embed-diag 는 --ref 가 필요합니다")
        exts = {".png", ".jpg", ".jpeg", ".bmp"}
        imgs = sorted(p for p in Path(args.ref).rglob("*") if p.suffix.lower() in exts)
        rep = diagnose_npu_embedding(imgs, max_images=args.max_images or 24)
        if rep.get("error"):
            print("진단 불가:", rep["error"])
            return 2
        print(f"NPU 임베딩 진단 — model={rep['model']} · 이미지 {rep['n_images']}장")
        if rep.get("errors"):
            print("  (건너뜀)", rep["errors"])
        print(f"  {'비교(설정쌍)':22} {'코사인평균':>9} {'코사인최소':>9} {'L2평균':>9}")
        for pair, s in rep["summary"].items():
            print(f"  {pair:22} {s['cos_mean']:9.4f} {s['cos_min']:9.4f} {s['l2_mean']:9.4f}")
        print("해석: npu_b1|npu_b16 코사인이 1.0 에서 멀수록 '배치가 임베딩을 바꿔' "
              "정확도가 깨지는 직접 증거(배치=후보선정 손상).")
        return 0

    from . import labels as _lab

    if args.labels_stats:
        st = _lab.stats(_lab.load(args.labels_stats))
        print(f"라벨 통계 — 기준 {st['refs']}개 · 정답있음 {st['labeled']}개 · "
              f"정답없음 {st['none']}개 · 복수정답 {st['multi']}개")
        return 0

    if args.make_labels_template:
        if not args.ref or not args.val:
            ap.error("--make-labels-template 에는 --ref 와 --val 이 필요합니다")
        tmpl = _lab.make_template(args.ref, args.val,
                                  max_slots=args.max_slots,
                                  max_images_per_side=args.max_images)
        out = _lab.save(args.make_labels_template, tmpl)
        st = _lab.stats(tmpl)
        print(f"라벨 템플릿 저장: {out}  (기준 {st['refs']}개 — 각 항목에 "
              f"정답 검증사진 경로를 채우세요. 정답 없음은 빈 리스트 []로 둡니다.)")
        return 0

    if not args.ref:
        ap.error("--ref 가 필요합니다 (또는 --list)")

    labels = None
    val_root = args.val
    tmp = None
    if args.self_test:
        tmp = tempfile.mkdtemp(prefix="aoi_bench_val_")
        print(f"[self-test] 증강 검증셋 생성 → {tmp}")
        labels = synthesize_val(args.ref, tmp)
        val_root = tmp
    elif args.labels:
        labels = _lab.load(args.labels)

    if not val_root:
        ap.error("--val 또는 --self-test 가 필요합니다")

    ds = build_dataset(args.ref, val_root, labels=labels,
                       max_slots=args.max_slots,
                       max_images_per_side=args.max_images)
    if not ds.tasks:
        print("공통 slot 이 없습니다(폴더 구조 확인).")
        return 2
    print(f"데이터: slot {len(ds.tasks)} · 이미지 {ds.n_images()} · 쌍 {ds.n_pairs()}")

    if args.miss_report:
        if not ds.gt:
            print("정답 라벨이 없어 실패 분석 불가 — --labels 를 주세요(self-test 도 가능).")
            return 2
        devices = detect_devices()
        gold = _rx.by_key(_rx.BASELINE_ACCURACY_KEY)
        print(f"[miss-report] gold({gold.key}) 채점 중…")
        gold_res, _ = run_recipe(ds, gold, threshold=args.threshold, devices=devices)
        gold_ok = {(f["slot"], f["query"]): f["ok"]
                   for f in query_failures(gold_res, ds.gt)}
        # 비교 대상 — gold 제외한 --recipes(없으면 추천 winner 후보).
        targets = [r for r in _rx.select(args.recipes)
                   if r.key != _rx.BASELINE_ACCURACY_KEY]
        for recipe in targets:
            res, _ = run_recipe(ds, recipe, threshold=args.threshold, devices=devices)
            fails = [f for f in query_failures(res, ds.gt) if not f["ok"]]
            print(f"\n=== {recipe.key} — 놓친 쿼리 {len(fails)}개 ===")
            for f in sorted(fails, key=lambda x: (x["slot"], x["query"])):
                gok = gold_ok.get((f["slot"], f["query"]))
                rank = f["correct_rank"]
                print(f"  · slot={f['slot']}  쿼리={Path(f['query']).name}")
                print(f"      오답 top1={Path(f['top1']).name if f['top1'] else '∅'}"
                      f"  · 정답 순위={rank if rank else '후보밖'}/{f['n_correct']}정답"
                      f"  · gold(전수) 맞힘={gok}")
            if not fails:
                print("  (놓친 쿼리 없음 — 이 레시피는 전부 정답)")
        return 0

    # 격리 실행이면 결과 폴더를 미리 만들고 부모에게 알린다(크래시해도 부분 결과를 그 폴더에서 읽음).
    run_dir = None
    if args.emit_progress:
        out_dir = Path(args.out) if args.out else default_out_dir()
        run_dir = out_dir / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        print(f"@@AOI_RUNDIR\t{run_dir}", flush=True)

    def _prog(name, done, total, key=""):
        print(f"  [{done}/{total}] {name}")
        if args.emit_progress:
            # 한 줄 = 한 레시피 '시작'.  부모(GUI)는 마지막 시작 키를 추적해, 자식이
            # 네이티브 크래시로 죽으면 그 키를 범인으로 보고 나머지를 이어서 측정한다.
            # flush 필수 — 파이프 버퍼링으로 진행이 지연되면 안 됨.
            tag = "done" if name == "완료" else "start"
            print(f"@@AOI_PROG\t{tag}\t{done}\t{total}\t{key}\t{name}", flush=True)

    # 레시피마다 부분 result.json 저장 — 자식이 도중에 죽어도 거기까지는 복구된다.
    _ckpt = (lambda s: write_result_json(run_dir, s, ds)) if run_dir else None

    explicit = (_rx.explicit_keys(args.explicit) if args.explicit
                else _rx.explicit_keys(args.recipes))
    suite = run_suite(ds, _rx.select(args.recipes), threshold=args.threshold,
                      per_recipe_timeout=args.timeout,
                      include_diagnostic=args.all_recipes,
                      skip_redundant=not args.all_recipes,
                      skip_low_history=not args.all_recipes,
                      explicit_keys=explicit,
                      progress=_prog, checkpoint=_ckpt)
    _print_table(suite, ds)
    run_dir = write_report(suite, ds, Path(args.out) if args.out else None,
                           run_dir=run_dir)
    print(f"기록 저장: {run_dir}")
    return 0


if __name__ == "__main__":          # pragma: no cover
    raise SystemExit(main())
