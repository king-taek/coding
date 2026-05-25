"""개발자 벤치마크 (진단용·임시) — CPU/GPU/NPU 를 *각각 따로* 돌려 매칭 알고리즘
변형들의 매치 결과(top-K) + 도달 시간을 한 번에 자동 기록.  사용자 리뷰 없음.

모든 변형은 **라벨 미사용(unsupervised, 추론 시점 계산)** 이며 하이퍼파라미터는
문헌 표준값으로 고정 — 이 데이터에 맞춰 튜닝하지 않는다(과적합 방지).

변형 목록
  anchor:    classical(CPU) / raw / whiten-mean / hybrid / margin
  재채점:    rerank-geom(ORB+RANSAC) / rerank-ssim / rerank-ncc-masked
  융합:      fusion-rrf / fusion-zscore
  표현개선:  whiten-hybrid / aqe-hybrid / kreciprocal / mutualnn-hybrid
  1:1 배정:  assign-hungarian
  앙상블:    ensemble-rerank / ensemble-assign  (GPU+NPU 둘 다 가용 시)

출력: 결과/레퍼런스/dev_benchmark_{ts}.jsonl
실패는 (장치, 변형) 단위로 격리.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

# 장치별(임베딩) 변형 — anchor 4 + 신규 10 (앙상블·classical 제외).
EMBED_VARIANTS = [
    "raw", "whiten-mean", "hybrid", "margin",
    "rerank-geom", "rerank-ssim", "rerank-ncc-masked",
    "fusion-rrf", "fusion-zscore",
    "whiten-hybrid", "aqe-hybrid", "kreciprocal", "mutualnn-hybrid",
    "assign-hungarian",
]
ENSEMBLE_VARIANTS = ["ensemble-rerank", "ensemble-assign"]
ALL_VARIANTS = ["classical"] + EMBED_VARIANTS + ENSEMBLE_VARIANTS

# 고정 하이퍼파라미터(문헌 표준 — 데이터에 맞춰 탐색하지 않음).
TOPK_LOG = 10
RERANK_K = 20          # 재채점할 임베딩 상위 후보 수
FUSION_K = 40          # 융합 시 고전 점수 계산 범위
AQE_N = 5              # average query expansion 이웃 수
KRECIP_K = 10          # 문맥(k-reciprocal 풍) 이웃 집합 크기
RRF_K = 60             # reciprocal rank fusion 상수
MARGIN_EPS = 0.02
MUTUAL_R = 10          # mutual-NN 게이팅 시 val 의 상위 ref 수

# 동시추론수·배치 B 최적값 탐색 그리드 (속도만 측정; 정확도 불변).
TUNE_CONCURRENCY = [8, 16, 32, 64, 96, 128]
TUNE_BATCH = [1, 4, 8, 16]
TUNE_SAMPLE_CAP = 120


# ---------------------------------------------------------------------------
# 순수 함수 (numpy) — 헤드리스 테스트 대상
# ---------------------------------------------------------------------------
def _l2n(M: np.ndarray) -> np.ndarray:
    return M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-9)


def whiten_fit(val_mat: np.ndarray, n_pc: int = 0):
    mu = val_mat.mean(axis=0)
    comps = np.zeros((0, val_mat.shape[1]), dtype=np.float32)
    if n_pc > 0 and val_mat.shape[0] > n_pc:
        try:
            _u, _s, Wt = np.linalg.svd(val_mat - mu, full_matrices=False)
            comps = Wt[:n_pc].astype(np.float32)
        except np.linalg.LinAlgError:
            pass
    return mu.astype(np.float32), comps


def whiten_apply(M: np.ndarray, mu: np.ndarray, comps: np.ndarray) -> np.ndarray:
    X = M - mu
    for c in comps:
        X = X - np.outer(X @ c, c)
    return _l2n(X)


def cosine_order(ref_vec: np.ndarray, val_mat: np.ndarray):
    sims = val_mat @ ref_vec
    return np.argsort(-sims), sims


def rerank_topk(order: np.ndarray, k: int, scorer) -> List[int]:
    """임베딩 상위 k 인덱스를 scorer(i)->높을수록 좋음 로 재정렬, 나머지는 유지."""
    head = list(order[:k])
    head.sort(key=lambda i: -scorer(i))
    return head + list(order[k:])


def rrf_scores(orders: List[List[int]], n: int, k: int = RRF_K) -> np.ndarray:
    sc = np.zeros(n, dtype=np.float64)
    for order in orders:
        for rank, idx in enumerate(order):
            sc[idx] += 1.0 / (k + rank + 1)
    return sc


def aqe_query(qvec: np.ndarray, val_mat: np.ndarray, n: int = AQE_N) -> np.ndarray:
    sims = val_mat @ qvec
    idx = np.argsort(-sims)[:max(1, n)]
    newq = qvec + val_mat[idx].sum(axis=0)
    return newq / (np.linalg.norm(newq) + 1e-9)


def context_jaccard(sims_rv: np.ndarray, val_nbr_sets: List[set], k: int = KRECIP_K) -> np.ndarray:
    """ref 의 상위-k 이웃 집합과 각 val 의 상위-k 이웃 집합 간 Jaccard(=문맥 유사도).

    k-reciprocal 재순위화의 단순·강건 변형(이웃 집합 중첩).  값이 클수록 같은 문맥.
    """
    ref_top = set(np.argsort(-sims_rv)[:k].tolist())
    out = np.zeros(len(val_nbr_sets), dtype=np.float64)
    for j, s in enumerate(val_nbr_sets):
        if not s:
            continue
        inter = len(ref_top & s)
        out[j] = inter / (len(ref_top | s) or 1)
    return out


def hungarian_assign(score: np.ndarray) -> np.ndarray:
    """(nref, nval) 점수행렬 → ref별 배정 val 인덱스(없으면 -1).  높을수록 선호."""
    from scipy.optimize import linear_sum_assignment
    nref, nval = score.shape
    assign = np.full(nref, -1, dtype=int)
    if nref == 0 or nval == 0:
        return assign
    rows, cols = linear_sum_assignment(-score)
    for r, c in zip(rows, cols):
        assign[r] = c
    return assign


# ---------------------------------------------------------------------------
# 고전 점수 헬퍼 (Feature 캐시 + 쌍 점수 메모)
# ---------------------------------------------------------------------------
class _Scorers:
    def __init__(self, cfg) -> None:
        from ..similarity import pipeline, ssim as _ssim
        self._pipeline = pipeline
        self._ssim = _ssim
        self._cfg = cfg
        self._feat: Dict[tuple, object] = {}
        self._memo: Dict[tuple, float] = {}

    def feat(self, path, side):
        key = (str(path), side)
        f = self._feat.get(key)
        if f is None:
            f = self._pipeline.extract(Path(path), cfg=self._cfg, side=side)
            self._feat[key] = f
        return f

    def _m(self, kind, rp, vp, fn):
        key = (kind, str(rp), str(vp))
        v = self._memo.get(key)
        if v is None:
            try:
                v = float(fn())
            except Exception:
                v = 0.0
            self._memo[key] = v
        return v

    def cscore(self, rp, vp):
        return self._m("c", rp, vp,
                       lambda: self._pipeline.score(self.feat(rp, "ref"), self.feat(vp, "val")))

    def ssim(self, rp, vp):
        return self._m("s", rp, vp,
                       lambda: self._ssim.ssim_score(self.feat(rp, "ref").roi_gray,
                                                     self.feat(vp, "val").roi_gray))

    def orb_inliers(self, rp, vp):
        return self._m("g", rp, vp,
                       lambda: _orb_ransac_inliers(self.feat(rp, "ref").roi_gray,
                                                   self.feat(vp, "val").roi_gray))

    def ncc(self, rp, vp):
        return self._m("n", rp, vp,
                       lambda: _ncc_masked(self.feat(rp, "ref").roi_gray,
                                           self.feat(vp, "val").roi_gray))


def _orb_ransac_inliers(g1: np.ndarray, g2: np.ndarray) -> float:
    import cv2
    orb = cv2.ORB_create(500)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 4 or len(k2) < 4:
        return 0.0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    good = []
    for mm in bf.knnMatch(d1, d2, k=2):
        if len(mm) == 2 and mm[0].distance < 0.75 * mm[1].distance:
            good.append(mm[0])
    if len(good) < 4:
        return float(len(good))
    src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    return float(int(mask.sum())) if mask is not None else float(len(good))


def _ncc_masked(g1: np.ndarray, g2: np.ndarray, px: int = 128) -> float:
    import cv2
    a = cv2.resize(g1, (px, px)).astype(np.float32)
    b = cv2.resize(g2, (px, px)).astype(np.float32)
    m = (a > 15) & (a < 240)            # 큰 이물 덩어리(극단 밝기) 제외
    if m.sum() < px * px * 0.2:
        m = np.ones_like(a, dtype=bool)
    av = a[m] - a[m].mean()
    bv = b[m] - b[m].mean()
    denom = (np.linalg.norm(av) * np.linalg.norm(bv)) + 1e-9
    return float((av @ bv) / denom)


# ---------------------------------------------------------------------------
# 슬롯 단위 랭킹 — 변형별 ref→정렬 val 인덱스
# ---------------------------------------------------------------------------
def rank_slot(variant: str, refs, vals, V: np.ndarray, ref_vecs: List[np.ndarray],
              sc: _Scorers) -> Dict[int, List[int]]:
    """반환: ref 인덱스 → val 인덱스 리스트(상위부터).  V: (Nv,D) val 임베딩.
    단일 장치 변형 전용(앙상블은 워커에서 두 백본을 직접 융합)."""
    Nv = V.shape[0]
    Vn = _l2n(V)
    out: Dict[int, List[int]] = {}

    # 공통 준비물 (lazy)
    def cval(ri, j):  # 고전 점수 (ref i, val j)
        return sc.cscore(refs[ri].path, vals[j].path)

    if variant in ("whiten-mean", "whiten-hybrid"):
        mu, comps = whiten_fit(V, n_pc=0)
        Vw = whiten_apply(V, mu, comps)

    # val 별 이웃 집합 (kreciprocal 문맥용)
    if variant == "kreciprocal":
        sim_vv = Vn @ Vn.T
        val_nbr = [set(np.argsort(-sim_vv[j])[1:KRECIP_K + 1].tolist()) for j in range(Nv)]

    # mutual-NN: 각 val 의 상위 ref 집합
    if variant == "mutualnn-hybrid":
        Rmat = _l2n(np.stack(ref_vecs)) if ref_vecs else np.zeros((0, V.shape[1]))
        sim_vr = Vn @ Rmat.T if Rmat.shape[0] else np.zeros((Nv, 0))
        val_top_refs = [set(np.argsort(-sim_vr[j])[:MUTUAL_R].tolist()) for j in range(Nv)]

    # ---- 배정형(슬롯 전역 1:1) ----
    if variant == "assign-hungarian":
        nref = len(refs)
        S = np.full((nref, Nv), -1e9, dtype=np.float64)
        for ri in range(nref):
            rv = _l2n(ref_vecs[ri][None, :])[0]
            order, _sims = cosine_order(rv, Vn)
            for j in order[:RERANK_K]:
                S[ri, j] = cval(ri, j)
        assign = hungarian_assign(S)
        for ri in range(nref):
            order = list(np.argsort(-S[ri]))
            a = assign[ri]
            if a >= 0:
                order = [a] + [j for j in order if j != a]
            out[ri] = order
        return out

    # ---- 장치 단독 변형 ----
    for ri, rv0 in enumerate(ref_vecs):
        rv = _l2n(rv0[None, :])[0]
        order, sims = cosine_order(rv, Vn)

        if variant == "raw":
            out[ri] = list(order)
        elif variant == "whiten-mean":
            rw = whiten_apply(rv0[None, :], mu, comps)[0]
            o2, _ = cosine_order(rw, Vw)
            out[ri] = list(o2)
        elif variant == "hybrid":
            out[ri] = rerank_topk(order, RERANK_K, lambda j, _ri=ri: cval(_ri, j))
        elif variant == "margin":
            s = np.sort(sims)[::-1]
            mgn = float(s[0] - s[1]) if len(s) >= 2 else 1.0
            out[ri] = (rerank_topk(order, RERANK_K, lambda j, _ri=ri: cval(_ri, j))
                       if mgn < MARGIN_EPS else list(order))
        elif variant == "rerank-geom":
            out[ri] = rerank_topk(order, RERANK_K,
                                  lambda j, _ri=ri: sc.orb_inliers(refs[_ri].path, vals[j].path))
        elif variant == "rerank-ssim":
            out[ri] = rerank_topk(order, RERANK_K,
                                  lambda j, _ri=ri: sc.ssim(refs[_ri].path, vals[j].path))
        elif variant == "rerank-ncc-masked":
            out[ri] = rerank_topk(order, RERANK_K,
                                  lambda j, _ri=ri: sc.ncc(refs[_ri].path, vals[j].path))
        elif variant == "fusion-rrf":
            head = list(order[:FUSION_K])
            crank = sorted(head, key=lambda j, _ri=ri: -cval(_ri, j))
            fused = rrf_scores([list(order), crank + list(order[FUSION_K:])], Nv)
            out[ri] = list(np.argsort(-fused))
        elif variant == "fusion-zscore":
            head = list(order[:FUSION_K])
            emb = np.array([sims[j] for j in head])
            cls = np.array([cval(ri, j) for j in head])
            z = lambda x: (x - x.mean()) / (x.std() + 1e-9)
            f = z(emb) + z(cls)
            ranked = [head[t] for t in np.argsort(-f)]
            out[ri] = ranked + list(order[FUSION_K:])
        elif variant == "whiten-hybrid":
            rw = whiten_apply(rv0[None, :], mu, comps)[0]
            o2, _ = cosine_order(rw, Vw)
            out[ri] = rerank_topk(o2, RERANK_K, lambda j, _ri=ri: cval(_ri, j))
        elif variant == "aqe-hybrid":
            q = aqe_query(rv, Vn, AQE_N)
            o2, _ = cosine_order(q, Vn)
            out[ri] = rerank_topk(o2, RERANK_K, lambda j, _ri=ri: cval(_ri, j))
        elif variant == "kreciprocal":
            ctx = context_jaccard(sims, val_nbr, KRECIP_K)
            combined = sims + 0.5 * ctx        # 원거리 + 문맥(동일 가중 계열)
            o2 = np.argsort(-combined)
            out[ri] = rerank_topk(o2, RERANK_K, lambda j, _ri=ri: cval(_ri, j))
        elif variant == "mutualnn-hybrid":
            head = list(order[:RERANK_K])
            mutual = [j for j in head if ri in val_top_refs[j]]
            non = [j for j in head if ri not in val_top_refs[j]]
            mutual.sort(key=lambda j, _ri=ri: -cval(_ri, j))
            non.sort(key=lambda j, _ri=ri: -cval(_ri, j))
            out[ri] = mutual + non + list(order[RERANK_K:])
        else:
            out[ri] = list(order)
    return out


# ---------------------------------------------------------------------------
# 워커
# ---------------------------------------------------------------------------
class _BenchSignals(QObject):
    progress = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)


class BenchmarkWorker(QThread):
    def __init__(self, tasks, *, cfg, threshold: float, use_gpu: bool = True,
                 use_npu: bool = True, session_id: str = "", tune: bool = True,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tasks = [(s, list(r), list(v)) for s, r, v in tasks]
        self._cfg = cfg
        self._threshold = float(threshold)
        self._use_gpu = bool(use_gpu)
        self._use_npu = bool(use_npu)
        self._tune = bool(tune)
        self._session_id = session_id or time.strftime("%Y%m%d_%H%M%S")
        self.signals = _BenchSignals()
        self._fh = None
        self._sc = _Scorers(cfg)
        self._emb_cache: Dict[str, Dict[str, tuple]] = {}   # device -> slot -> (V,vnames,Rvecs,refs,vals)

    def _log(self, obj: dict) -> None:
        if self._fh is not None:
            try:
                self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n"); self._fh.flush()
            except Exception:
                pass

    def _emit(self, m): self.signals.progress.emit(m)

    # -- run ---------------------------------------------------------
    def run(self) -> None:
        try:
            from ..utils import paths
            out_dir = paths.results_dir() / "레퍼런스"; out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"dev_benchmark_{self._session_id}.jsonl"
            self._fh = open(out_path, "w", encoding="utf-8")
            self._log({"type": "bench_config", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                       "session_id": self._session_id,
                       "engine": getattr(self._cfg, "engine", "?"),
                       "center_crop": bool(getattr(self._cfg, "center_crop", False)),
                       "persist_scores": bool(getattr(self._cfg, "persist_scores", False)),
                       "threshold": self._threshold, "n_slots": len(self._tasks),
                       "variants": ALL_VARIANTS, "rerank_k": RERANK_K, "topk_log": TOPK_LOG})

            try:
                from ..learning import embedder_openvino as _ov
                units = _ov.available_units()
            except Exception:
                _ov, units = None, []
            emb_devices = []
            if _ov is not None:
                if self._use_gpu and "GPU" in units:
                    emb_devices.append(("gpu", "GPU", _ov.MODEL_MOBILENET_V3))
                if self._use_npu and "NPU" in units:
                    emb_devices.append(("npu", "NPU", _ov.MODEL_RESNET18))

            self._run_classical()

            sample = []
            if self._tasks:
                _, _r, _v = max(self._tasks, key=lambda t: len(t[2]))
                sample = [v.path for v in _v[:TUNE_SAMPLE_CAP]]

            for tag, ov_dev, mk in emb_devices:
                jobs = int(getattr(self._cfg, "accel_concurrency", 32))
                batch = max(1, int(getattr(self._cfg, "embed_batch", 1)))
                # 정확도 임베딩 전에 tune 을 먼저 돌려 최적 batch/concurrency 적용
                # → GPU 가 batch=1 에서 멈춘 듯 느려지는 문제 회피.
                if self._tune and sample:
                    best = self._run_tune(_ov, tag, ov_dev, mk, sample)
                    if best:
                        jobs, batch = best
                self._embed_all_slots(_ov, tag, ov_dev, mk, jobs=jobs, batch=batch)
                for variant in EMBED_VARIANTS:
                    self._run_embed_variant(tag, variant)

            # 앙상블 — GPU+NPU 둘 다 임베딩 성공 시
            if "gpu" in self._emb_cache and "npu" in self._emb_cache:
                for variant in ENSEMBLE_VARIANTS:
                    self._run_ensemble_variant(variant)

            self._log({"type": "bench_done", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._fh.close(); self._fh = None
            self.signals.done.emit(str(out_path))
        except Exception as exc:
            try:
                if self._fh is not None:
                    self._fh.close()
            except Exception:
                pass
            self.signals.failed.emit(repr(exc))

    # -- classical (anchor) ------------------------------------------
    def _run_classical(self) -> None:
        self._emit("CPU 고전(classical)…")
        t_pre = 0.0; t_rank = 0.0; n = 0; npool = 0; rows = []
        for slot, refs, vals in self._tasks:
            t0 = time.perf_counter()
            for v in vals:
                self._sc.feat(v.path, "val")
            t_pre += time.perf_counter() - t0
            npool = max(npool, len(vals))
            for r in refs:
                t0 = time.perf_counter()
                scored = sorted(vals, key=lambda v, _r=r: -self._sc.cscore(_r.path, v.path))
                t_rank += time.perf_counter() - t0; n += 1
                rows.append((slot, r, [(v.path.name, self._sc.cscore(r.path, v.path)) for v in scored[:TOPK_LOG]]))
        self._log({"type": "run", "device": "cpu", "variant": "classical",
                   "precompute_s": round(t_pre, 2), "decide_s": round(t_rank, 2),
                   "n_refs": n, "val_pool_max": npool})
        for slot, r, topk in rows:
            self._log_result("cpu", "classical", slot, r, topk)

    # -- embed all slots for a device (cache) ------------------------
    def _embed_all_slots(self, _ov, tag, ov_dev, mk, jobs=None, batch=None) -> None:
        cfg = self._cfg
        if jobs is None:
            jobs = int(getattr(cfg, "accel_concurrency", 32))
        if batch is None:
            batch = max(1, int(getattr(cfg, "embed_batch", 1)))
        self._emit(f"{tag.upper()} 임베딩 계산 (batch={batch}, conc={jobs})…")
        cache = {}; t_embed = 0.0
        for slot, refs, vals in self._tasks:
            try:
                t0 = time.perf_counter()
                ve = _ov.device_embed([v.path for v in vals], model_kind=mk, device=ov_dev,
                                      cfg=cfg, jobs=jobs, batch=batch)
                re = _ov.device_embed([r.path for r in refs], model_kind=mk, device=ov_dev,
                                      cfg=cfg, jobs=jobs, batch=batch)
                t_embed += time.perf_counter() - t0
            except Exception as exc:
                self._log({"type": "run", "device": tag, "variant": "ERROR",
                           "slot": slot, "error": repr(exc)})
                continue
            vkeep = [v for v in vals if v.path in ve]
            rkeep = [r for r in refs if r.path in re]
            if not vkeep or not rkeep:
                continue
            V = np.stack([ve[v.path] for v in vkeep]).astype(np.float32)
            Rvecs = [re[r.path].astype(np.float32) for r in rkeep]
            cache[slot] = (V, [v.path.name for v in vkeep], Rvecs, rkeep, vkeep)
        self._emb_cache[tag] = cache
        self._embed_s = getattr(self, "_embed_s", {}); self._embed_s[tag] = round(t_embed, 2)
        self._log({"type": "embed_precompute", "device": tag, "embed_s": round(t_embed, 2),
                   "n_slots": len(cache), "batch": batch, "concurrency": jobs})

    def _run_embed_variant(self, tag, variant) -> None:
        cache = self._emb_cache.get(tag, {})
        t0 = time.perf_counter(); n = 0; npool = 0; rows = []
        for slot, (V, vnames, Rvecs, refs, vals) in cache.items():
            npool = max(npool, V.shape[0])
            ranked = rank_slot(variant, refs, vals, V, Rvecs, self._sc)
            for ri, order in ranked.items():
                rows.append((slot, refs[ri], [(vnames[j], 0.0) for j in order[:TOPK_LOG]])); n += 1
        dt = time.perf_counter() - t0
        pre = getattr(self, "_embed_s", {}).get(tag, 0.0)   # 공유 임베딩 precompute
        self._log({"type": "run", "device": tag, "variant": variant,
                   "precompute_s": pre, "decide_s": round(dt, 2),
                   "n_refs": n, "val_pool_max": npool})
        for slot, r, topk in rows:
            self._log_result(tag, variant, slot, r, topk)
        self._emit(f"{tag.upper()} / {variant} 완료")

    def _run_ensemble_variant(self, variant) -> None:
        """GPU(MobileNet)+NPU(ResNet18) 두 백본을 융합 — 각 백본의 ref·val 임베딩으로
        ref별 순위를 내고 RRF 로 합친 뒤(=recall) 고전 재채점/배정(=정밀)."""
        gpu, npu = self._emb_cache["gpu"], self._emb_cache["npu"]
        t0 = time.perf_counter(); n = 0; npool = 0; rows = []
        for slot in gpu:
            if slot not in npu:
                continue
            Vg, vng, Rg, refs_g, vals_g = gpu[slot]
            Vn_, vnn, Rn, refs_n, vals_n = npu[slot]
            # 공통 val(이름 기준) + 두 백본 정렬
            nidx = {name: k for k, name in enumerate(vnn)}
            keep = [k for k, name in enumerate(vng) if name in nidx]
            if not keep:
                continue
            vnames = [vng[k] for k in keep]
            vitems = [vals_g[k] for k in keep]
            Vg_n = _l2n(Vg[keep]); Vn_n = _l2n(np.stack([Vn_[nidx[vng[k]]] for k in keep]))
            # 공통 ref(이름 기준) + 두 백본 ref 벡터
            rgi = {r.path.name: i for i, r in enumerate(refs_g)}
            rni = {r.path.name: i for i, r in enumerate(refs_n)}
            common_refs = [r for r in refs_g if r.path.name in rni]
            npool = max(npool, len(vnames))
            S = np.full((len(common_refs), len(vnames)), -1e9, dtype=np.float64)
            for ri, r in enumerate(common_refs):
                rg = _l2n(Rg[rgi[r.path.name]][None, :])[0]
                rn = _l2n(Rn[rni[r.path.name]][None, :])[0]
                o1, _ = cosine_order(rg, Vg_n)
                o2, _ = cosine_order(rn, Vn_n)
                fused = rrf_scores([list(o1), list(o2)], len(vnames))
                order = list(np.argsort(-fused))
                if variant == "ensemble-rerank":
                    ordered = rerank_topk(np.array(order), RERANK_K,
                                          lambda j, _r=r: self._sc.cscore(_r.path, vitems[j].path))
                    rows.append((slot, r, [(vnames[j], 0.0) for j in ordered[:TOPK_LOG]])); n += 1
                else:  # ensemble-assign — 점수행렬 채우고 슬롯 끝나면 배정
                    for j in order[:RERANK_K]:
                        S[ri, j] = self._sc.cscore(r.path, vitems[j].path)
            if variant == "ensemble-assign":
                assign = hungarian_assign(S)
                for ri, r in enumerate(common_refs):
                    order = list(np.argsort(-S[ri])); a = assign[ri]
                    if a >= 0:
                        order = [a] + [j for j in order if j != a]
                    rows.append((slot, r, [(vnames[j], 0.0) for j in order[:TOPK_LOG]])); n += 1
        dt = time.perf_counter() - t0
        self._log({"type": "run", "device": "ens", "variant": variant,
                   "precompute_s": 0.0, "decide_s": round(dt, 2),
                   "n_refs": n, "val_pool_max": npool})
        for slot, r, topk in rows:
            self._log_result("ens", variant, slot, r, topk)
        self._emit(f"ensemble / {variant} 완료")

    # -- concurrency/batch 튜닝 --------------------------------------
    def _run_tune(self, _ov, tag, ov_dev, mk, sample_paths):
        self._emit(f"{tag.upper()} 동시추론수·배치 최적화…")
        best = None
        for batch in TUNE_BATCH:
            try:
                _ov.device_embed(sample_paths[:4], model_kind=mk, device=ov_dev,
                                 cfg=self._cfg, jobs=8, batch=batch)
            except Exception:
                pass
            for conc in TUNE_CONCURRENCY:
                try:
                    t0 = time.perf_counter()
                    emb = _ov.device_embed(sample_paths, model_kind=mk, device=ov_dev,
                                           cfg=self._cfg, jobs=conc, batch=batch)
                    dt = time.perf_counter() - t0; nimg = len(emb)
                except Exception as exc:
                    self._log({"type": "tune", "device": tag, "concurrency": conc,
                               "batch": batch, "error": repr(exc)}); continue
                ips = (nimg / dt) if dt > 0 else 0.0
                self._log({"type": "tune", "device": tag, "concurrency": conc, "batch": batch,
                           "embed_s": round(dt, 3), "n_images": nimg, "img_per_s": round(ips, 2)})
                if nimg > 0 and (best is None or dt < best[2]):
                    best = (conc, batch, dt, ips)
        if best is not None:
            self._log({"type": "tune_best", "device": tag, "concurrency": best[0],
                       "batch": best[1], "embed_s": round(best[2], 3),
                       "img_per_s": round(best[3], 2), "n_images": len(sample_paths)})
            return (best[0], best[1])
        return None

    def _log_result(self, device, variant, slot, ref_item, scored) -> None:
        topk = [{"rank": i, "filename": Path(n).name, "score": round(float(s), 4)}
                for i, (n, s) in enumerate(scored[:TOPK_LOG])]
        self._log({"type": "result", "device": device, "variant": variant,
                   "slot": slot, "ref_filename": Path(ref_item.path).name, "topk": topk})
