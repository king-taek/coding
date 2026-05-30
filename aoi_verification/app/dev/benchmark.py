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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import recipes as _rx
from .recipes import (RECALL_CPU, RECALL_GPU, RECALL_GPU_NPU, RECALL_NONE,
                      RECALL_NPU, SCORE_CLASSICAL, SCORE_EMBED_ONLY,
                      SCORE_FUSION, Recipe)

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
                                        jobs=cap, batch=batch, progress_cb=progress)
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
                                jobs=cap, batch=batch, progress_cb=progress)
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
    desc: str = ""


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
    run = RecipeRun(key=recipe.key, name=recipe.name, desc=recipe.desc,
                    n_images=ds.n_images(), n_pairs=ds.n_pairs())
    results: Results = {}
    embed_t = 0.0
    score_t = 0.0

    def stopped() -> bool:
        return bool(stop and stop())

    def _store(slot, ref_path, ranked):
        ranked = sorted(ranked, key=lambda x: -x[1])[:RESULT_KEEP]
        results[(slot, str(ref_path))] = [(str(p), float(s)) for p, s in ranked]

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
            for r in refs:
                if stopped():
                    break
                cands = score_ref_classical(r, vals, threshold=threshold, cfg=cfg,
                                            stop_cb=stopped)
                _store(slot, r.path, [(c.item.path, c.score) for c in cands])
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
            for r in refs:
                if stopped():
                    break
                cands = score_ref_classical(r, vals, threshold=threshold, cfg=cfg,
                                            stop_cb=stopped)
                _store(slot, r.path, [(c.item.path, c.score) for c in cands])
            score_t += time.perf_counter() - t0
            continue

        run.used_embedding = True
        index, val_paths = built
        # ref 임베딩
        t0 = time.perf_counter()
        ref_emb = _embed_paths([Path(r.path) for r in refs], recipe, cfg, devices)
        embed_t += time.perf_counter() - t0

        t0 = time.perf_counter()
        for r in refs:
            if stopped():
                break
            remb = ref_emb.get(Path(r.path))
            if remb is None:
                cands = score_ref_classical(r, vals, threshold=threshold, cfg=cfg,
                                            stop_cb=stopped)
                _store(slot, r.path, [(c.item.path, c.score) for c in cands])
                continue
            hits = index.query(remb, len(val_paths))          # [(label, cos)] desc
            ordered = [(val_paths[lab], float(cos)) for lab, cos in hits
                       if 0 <= lab < len(val_paths)]
            if built_b is not None:                            # 앙상블: 두 코사인 평균
                ordered = _ensemble_merge(ordered, built_b, ref_emb_b_get(r))
            if recipe.scoring == SCORE_EMBED_ONLY:
                _store(slot, r.path, [(vp, _cos_to_unit(c)) for vp, c in ordered])
                continue
            # fusion — 상위 topk 를 CPU 고전 재채점 후 z-융합
            topk = max(int(recipe.fusion_topk), 1)
            top = ordered[:topk]
            items = [by_path.get(Path(vp)) for vp, _ in top]
            valid = [it for it in items if it is not None]
            cls = (score_ref_classical(r, valid, threshold=0.0, cfg=cfg,
                                       stop_cb=stopped) if valid else [])
            cls_map = {str(c.item.path): float(c.score) for c in cls}
            emb_scores = [c for _, c in top]
            cls_scores = [cls_map.get(str(vp), 0.0) for vp, _ in top]
            mapped = map_score(zfuse(emb_scores, cls_scores))
            head = list(zip([vp for vp, _ in top], mapped))
            tail = [(vp, _cos_to_unit(c)) for vp, c in ordered[topk:]]
            _store(slot, r.path, head + tail)
        score_t += time.perf_counter() - t0

    run.total_sec = round(time.perf_counter() - t_total, 3)
    run.embed_sec = round(embed_t, 3)
    run.score_sec = round(score_t, 3)
    run.timed_out = stopped()
    n = run.n_images
    run.img_per_sec = round(n / run.total_sec, 1) if run.total_sec > 1e-6 else 0.0
    if run.fell_back_classical and recipe.uses_embedding():
        run.note = "가속 장치 미가용 → CPU 고전 폴백(속도는 CPU 기준)"
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
              progress: Optional[Callable[[str, int, int], None]] = None,
              stop: Optional[Callable[[], bool]] = None) -> SuiteResult:
    """레시피들을 **순차** 실행(로딩 안전)하고 정확도/속도/추천을 산출한다."""
    recipes = recipes or list(_rx.REGISTRY)
    devices = detect_devices()
    suite = SuiteResult(baseline_key=_rx.BASELINE_ACCURACY_KEY,
                        production_key=_rx.PRODUCTION_SPEED_KEY,
                        has_gt=bool(ds.gt), devices=sorted(devices))

    # 정확도 기준선(GT 없을 때 일치율 산정용)은 항상 먼저 한 번 확보한다.
    baseline_recipe = _rx.by_key(_rx.BASELINE_ACCURACY_KEY)
    if baseline_recipe not in recipes:
        recipes = [baseline_recipe] + list(recipes)

    all_results: Dict[str, Results] = {}
    total = len(recipes)
    for i, recipe in enumerate(recipes, start=1):
        if stop and stop():
            break
        if progress:
            progress(recipe.name, i - 1, total)

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
            run = RecipeRun(key=recipe.key, name=recipe.name, ok=False,
                            note=f"실패: {e}", desc=recipe.desc)
            suite.runs.append(run)
            continue
        if out is None:
            run = RecipeRun(key=recipe.key, name=recipe.name, ok=False,
                            timed_out=timed_out,
                            note="타임아웃" if timed_out else "결과 없음",
                            desc=recipe.desc)
            suite.runs.append(run)
            continue
        res, run = out
        run.timed_out = run.timed_out or timed_out
        if timed_out:
            run.note = (run.note + " · 타임아웃 중단").strip(" ·")
            run.ok = False
        all_results[recipe.key] = res
        suite.runs.append(run)

    # 정확도 평가 — GT recall@K + 기준선(고전 전수) 대비 일치율.
    baseline_results = all_results.get(_rx.BASELINE_ACCURACY_KEY, {})
    for run in suite.runs:
        res = all_results.get(run.key, {})
        if not res:
            continue
        r1, r5, _n = evaluate(res, ds.gt)
        run.recall1, run.recall5 = r1, r5
        if run.key != _rx.BASELINE_ACCURACY_KEY:
            run.agree1 = agreement(res, baseline_results)
        else:
            run.agree1 = 1.0

    suite.recommended_key = recommend(suite.runs)
    _fill_speedup(suite)
    if progress:
        progress("완료", total, total)
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


def write_report(suite: SuiteResult, ds: Dataset, out_dir: Optional[Path] = None) -> Path:
    """스위트 결과를 ``out_dir/<timestamp>/`` 에 JSON+MD 로 저장하고 폴더를 반환."""
    out_dir = Path(out_dir) if out_dir else default_out_dir()
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
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
    (run_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
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

    L.append("| 레시피 | 총시간(s) | 임베딩(s) | 재채점(s) | img/s | 피크MB | 정확도 | 비고 |")
    L.append("|---|--:|--:|--:|--:|--:|--:|---|")
    for r in sorted(suite.runs, key=lambda x: (not x.ok, x.total_sec or 1e9)):
        mark = " ⭐" if r.key == suite.recommended_key else ""
        peak = f"{r.peak_mb:.0f}" if r.peak_mb else "-"
        note = r.note or ("폴백" if r.fell_back_classical else "")
        L.append(f"| {r.name}{mark} | {r.total_sec:.2f} | {r.embed_sec:.2f} | "
                 f"{r.score_sec:.2f} | {r.img_per_sec:.1f} | {peak} | "
                 f"{acc_str(r)} | {note} |")

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
    ap.add_argument("--recipes", default="all",
                    help="콤마 구분 레시피 키 또는 'all'")
    ap.add_argument("--max-slots", type=int, default=0, help="서브샘플: slot 수 상한")
    ap.add_argument("--max-images", type=int, default=0,
                    help="서브샘플: 측당 이미지 수 상한")
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="레시피별 타임아웃(초, 0=무제한)")
    ap.add_argument("--threshold", type=float, default=0.0)
    ap.add_argument("--out", help="기록 폴더(기본: 캐시/dev_bench/<host>)")
    ap.add_argument("--list", action="store_true", help="레시피 목록만 출력")
    args = ap.parse_args(argv)

    if args.list:
        for r in _rx.REGISTRY:
            print(f"{r.key:24s} {r.name}\n    {r.desc}")
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
        labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))

    if not val_root:
        ap.error("--val 또는 --self-test 가 필요합니다")

    ds = build_dataset(args.ref, val_root, labels=labels,
                       max_slots=args.max_slots,
                       max_images_per_side=args.max_images)
    if not ds.tasks:
        print("공통 slot 이 없습니다(폴더 구조 확인).")
        return 2
    print(f"데이터: slot {len(ds.tasks)} · 이미지 {ds.n_images()} · 쌍 {ds.n_pairs()}")

    def _prog(name, done, total):
        print(f"  [{done}/{total}] {name}")

    suite = run_suite(ds, _rx.select(args.recipes), threshold=args.threshold,
                      per_recipe_timeout=args.timeout, progress=_prog)
    _print_table(suite, ds)
    run_dir = write_report(suite, ds, Path(args.out) if args.out else None)
    print(f"기록 저장: {run_dir}")
    return 0


if __name__ == "__main__":          # pragma: no cover
    raise SystemExit(main())
