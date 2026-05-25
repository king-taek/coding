"""개발자 벤치마크 (진단용·임시) — CPU/GPU/NPU 를 *각각 따로* 돌려 개선안 변형들의
매치 결과 + 도달 시간을 한 번에 자동 기록.

- 자동화 모드 기반: 사용자 리뷰 없음.  모델의 매치(top-K)만 남긴다(정답은 별도 보유).
- 장치는 동시(work-stealing)가 아니라 **순차 단독** 실행 — 장치별 정확도·시간을 분리 측정.

변형(최소 5개 초과):
  classical    CPU 고전(pHash+ORB+SSIM) — 정확도 기준점
  raw          임베딩 cosine (현행)
  whiten-mean  슬롯 평균 제거 후 cosine
  whiten-pc1   평균 + top-1 주성분 제거(all-but-top1)
  hybrid       임베딩 top-K → 고전 재채점
  margin       임베딩 margin 작을 때만 고전 재채점(그 외 임베딩 top1)
  center-crop  중앙 30% crop 입력으로 임베딩(raw)

출력: ``결과/레퍼런스/dev_benchmark_{ts}.jsonl`` (모든 run 을 한 파일에).
실패는 run 단위로 격리(한 장치/변형이 죽어도 나머지는 계속).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

# 임베딩 변형 7종(+classical) — UI/문서에서 공유.
EMBED_VARIANTS = ["raw", "whiten-mean", "whiten-pc1", "hybrid", "margin", "center-crop"]
ALL_VARIANTS = ["classical"] + EMBED_VARIANTS

TOPK_LOG = 10          # 기록할 상위 후보 수
HYBRID_K = 20          # 고전 재채점할 임베딩 상위 후보 수
MARGIN_EPS = 0.02      # 이 미만이면 '애매' → margin 변형에서 고전 재채점


# ---------------------------------------------------------------------------
# 순수 변형 함수 (numpy) — 헤드리스 단위 테스트 대상
# ---------------------------------------------------------------------------
def _l2n(M: np.ndarray) -> np.ndarray:
    return M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)


def whiten_fit(val_mat: np.ndarray, n_pc: int = 0):
    """val 임베딩 기준 (mu, comps) 반환.  comps = 제거할 상위 주성분(단위, 행)."""
    mu = val_mat.mean(axis=0)
    comps = np.zeros((0, val_mat.shape[1]), dtype=np.float32)
    if n_pc > 0 and val_mat.shape[0] > n_pc:
        Vc = val_mat - mu
        try:
            _u, _s, Wt = np.linalg.svd(Vc, full_matrices=False)
            comps = Wt[:n_pc].astype(np.float32)
        except np.linalg.LinAlgError:
            pass
    return mu.astype(np.float32), comps


def whiten_apply(M: np.ndarray, mu: np.ndarray, comps: np.ndarray) -> np.ndarray:
    """평균/주성분 제거 후 재정규화."""
    X = M - mu
    for c in comps:
        X = X - np.outer(X @ c, c)
    return _l2n(X)


def cosine_order(ref_vec: np.ndarray, val_mat: np.ndarray):
    """ref 벡터 vs val 행렬 cosine 내림차순 인덱스 + 점수."""
    sims = val_mat @ ref_vec
    order = np.argsort(-sims)
    return order, sims


def rerank_topk_classical(order: np.ndarray, val_names: List[str], k: int,
                          cscore: Callable[[str], float]):
    """임베딩 순위 상위 k 를 고전 점수로 재정렬, 나머지는 임베딩 순서 유지."""
    head = list(order[:k])
    scored = sorted(head, key=lambda i: -cscore(val_names[i]))
    rest = [i for i in order[k:]]
    return scored + rest


# ---------------------------------------------------------------------------
# 워커
# ---------------------------------------------------------------------------
class _BenchSignals(QObject):
    progress = pyqtSignal(str)
    done = pyqtSignal(str)        # 출력 jsonl 경로
    failed = pyqtSignal(str)


class BenchmarkWorker(QThread):
    """CPU/GPU/NPU × 변형 전체를 순차 실행하고 jsonl 한 파일에 기록."""

    def __init__(self,
                 tasks: List[Tuple[str, list, list]],
                 *,
                 cfg,
                 threshold: float,
                 use_gpu: bool = True,
                 use_npu: bool = True,
                 session_id: str = "",
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._tasks = [(s, list(r), list(v)) for s, r, v in tasks]
        self._cfg = cfg
        self._threshold = float(threshold)
        self._use_gpu = bool(use_gpu)
        self._use_npu = bool(use_npu)
        self._session_id = session_id or time.strftime("%Y%m%d_%H%M%S")
        self.signals = _BenchSignals()
        self._fh = None

    # -- logging ------------------------------------------------------
    def _log(self, obj: dict) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._fh.flush()
        except Exception:
            pass

    def _emit(self, msg: str) -> None:
        self.signals.progress.emit(msg)

    # -- classical helpers -------------------------------------------
    def _feat_fn(self):
        """side 별 고전 Feature 캐시 함수 (pipeline.extract 재사용)."""
        from ..similarity import pipeline
        cache: dict = {}

        def feat(path, side):
            key = (str(path), side)
            f = cache.get(key)
            if f is None:
                f = pipeline.extract(Path(path), cfg=self._cfg, side=side)
                cache[key] = f
            return f
        return feat, pipeline

    # -- run ----------------------------------------------------------
    def run(self) -> None:  # noqa: C901
        try:
            from ..utils import paths
            out_dir = paths.results_dir() / "레퍼런스"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"dev_benchmark_{self._session_id}.jsonl"
            self._fh = open(out_path, "w", encoding="utf-8")
            self._log({
                "type": "bench_config", "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "session_id": self._session_id,
                "engine": getattr(self._cfg, "engine", "?"),
                "center_crop": bool(getattr(self._cfg, "center_crop", False)),
                "threshold": self._threshold,
                "n_slots": len(self._tasks),
                "variants": ALL_VARIANTS,
                "hybrid_k": HYBRID_K, "margin_eps": MARGIN_EPS, "topk_log": TOPK_LOG,
            })

            # 어떤 임베딩 장치가 가용한가
            avail = []
            try:
                from ..learning import embedder_openvino as _ov
                units = _ov.available_units()        # ["GPU","NPU"] 중 존재분
            except Exception:
                _ov = None
                units = []
            emb_devices = []
            if _ov is not None:
                if self._use_gpu and "GPU" in units:
                    emb_devices.append(("gpu", "GPU", _ov.MODEL_MOBILENET_V3))
                if self._use_npu and "NPU" in units:
                    emb_devices.append(("npu", "NPU", _ov.MODEL_RESNET18))

            # 1) CPU classical (항상)
            self._run_classical()

            # 2) 임베딩 장치 각각 따로
            for tag, ov_dev, model_kind in emb_devices:
                self._run_embed_device(_ov, tag, ov_dev, model_kind)

            self._log({"type": "bench_done", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._fh.close(); self._fh = None
            self.signals.done.emit(str(out_path))
        except Exception as exc:    # 절대 UI 크래시 금지
            try:
                if self._fh is not None:
                    self._fh.close()
            except Exception:
                pass
            self.signals.failed.emit(repr(exc))

    # -- CPU classical -----------------------------------------------
    def _run_classical(self) -> None:
        self._emit("CPU 고전(classical) 벤치마크…")
        feat, pipeline = self._feat_fn()
        t_pre = 0.0; t_rank = 0.0; n_refs = 0; npool = 0
        results = []
        for slot, refs, vals in self._tasks:
            t0 = time.perf_counter()
            vfeats = [(v, feat(v.path, "val")) for v in vals]    # extract(=precompute)
            t_pre += time.perf_counter() - t0
            npool = max(npool, len(vals))
            for r in refs:
                t0 = time.perf_counter()
                rf = feat(r.path, "ref")
                scored = sorted(((v, pipeline.score(rf, vf)) for v, vf in vfeats),
                                key=lambda x: -x[1])
                t_rank += time.perf_counter() - t0
                n_refs += 1
                results.append((slot, r, scored))
        self._log({"type": "run", "device": "cpu", "variant": "classical",
                   "precompute_s": round(t_pre, 2), "decide_s": round(t_rank, 2),
                   "n_refs": n_refs, "val_pool_max": npool})
        for slot, r, scored in results:
            self._log_result("cpu", "classical", slot, r,
                             [(v.path.name, s) for v, s in scored])

    # -- embedding device --------------------------------------------
    def _run_embed_device(self, _ov, tag, ov_dev, model_kind) -> None:
        self._emit(f"{tag.upper()} 임베딩 벤치마크…")
        cfg = self._cfg
        jobs = getattr(cfg, "accel_concurrency", 32)
        batch = max(1, int(getattr(cfg, "embed_batch", 1)))
        feat, pipeline = self._feat_fn()

        # 슬롯별 임베딩(비크롭/크롭) 미리 계산 + 시간 측정
        per_slot = {}          # slot -> dict
        t_embed = 0.0; t_embed_crop = 0.0
        for slot, refs, vals in self._tasks:
            try:
                t0 = time.perf_counter()
                ve = _ov.device_embed([v.path for v in vals], model_kind=model_kind,
                                      device=ov_dev, cfg=cfg, jobs=jobs, batch=batch)
                re = _ov.device_embed([r.path for r in refs], model_kind=model_kind,
                                      device=ov_dev, cfg=cfg, jobs=jobs, batch=batch)
                t_embed += time.perf_counter() - t0
                # center-crop 변형용(중앙 30%) — side 전달
                from .. import config as _cfgmod
                cfg_crop = _cfgmod.SimilarityConfig(
                    engine=getattr(cfg, "engine", "efficiency"), center_crop=True,
                    use_cpu=cfg.use_cpu, use_gpu=cfg.use_gpu, use_npu=cfg.use_npu,
                    accel_concurrency=jobs, embed_batch=batch)
                t0 = time.perf_counter()
                vec = _ov.device_embed([v.path for v in vals], model_kind=model_kind,
                                       device=ov_dev, cfg=cfg_crop, jobs=jobs,
                                       batch=batch, side="val")
                rec = _ov.device_embed([r.path for r in refs], model_kind=model_kind,
                                       device=ov_dev, cfg=cfg_crop, jobs=jobs,
                                       batch=batch, side="ref")
                t_embed_crop += time.perf_counter() - t0
            except Exception as exc:
                self._log({"type": "run", "device": tag, "variant": "ERROR",
                           "slot": slot, "error": repr(exc)})
                continue
            per_slot[slot] = dict(refs=refs, vals=vals, ve=ve, re=re, vec=vec, rec=rec)

        if not per_slot:
            self._log({"type": "run", "device": tag, "variant": "ERROR",
                       "error": "임베딩 결과 없음(컴파일/추론 실패 가능)"})
            return

        # 각 변형별로 랭킹 산출 + 기록
        for variant in EMBED_VARIANTS:
            t_rank = 0.0; n_refs = 0; npool = 0; rows = []
            for slot, d in per_slot.items():
                refs, vals = d["refs"], d["vals"]
                use_crop = (variant == "center-crop")
                vemb, remb = (d["vec"], d["rec"]) if use_crop else (d["ve"], d["re"])
                # 임베딩 성공한 val 만 풀에 포함
                vnames = [v.path.name for v in vals if v.path in vemb]
                vmat = np.stack([vemb[v.path] for v in vals if v.path in vemb]) \
                    if vnames else np.zeros((0, 1), np.float32)
                if vmat.shape[0] == 0:
                    continue
                npool = max(npool, vmat.shape[0])
                # 변형별 행렬 준비
                if variant in ("whiten-mean", "whiten-pc1"):
                    mu, comps = whiten_fit(vmat, n_pc=(1 if variant == "whiten-pc1" else 0))
                    vmat_t = whiten_apply(vmat, mu, comps)
                else:
                    mu = comps = None
                    vmat_t = _l2n(vmat)
                for r in refs:
                    if r.path not in remb:
                        continue
                    rvec = remb[r.path]
                    t0 = time.perf_counter()
                    if variant in ("whiten-mean", "whiten-pc1"):
                        rv = whiten_apply(rvec[None, :], mu, comps)[0]
                    else:
                        rv = rvec / (np.linalg.norm(rvec) + 1e-9)
                    order, sims = cosine_order(rv, vmat_t)
                    if variant in ("hybrid", "margin"):
                        do_rerank = True
                        if variant == "margin":
                            s = np.sort(sims)[::-1]
                            margin = float(s[0] - s[1]) if len(s) >= 2 else 1.0
                            do_rerank = margin < MARGIN_EPS
                        if do_rerank:
                            rf = feat(r.path, "ref")

                            def cscore(vn, _rf=rf):
                                vobj = next(v for v in vals if v.path.name == vn)
                                return pipeline.score(_rf, feat(vobj.path, "val"))
                            order = rerank_topk_classical(order, vnames, HYBRID_K, cscore)
                    t_rank += time.perf_counter() - t0
                    n_refs += 1
                    topk = [(vnames[i], float(sims[i])) for i in list(order)[:TOPK_LOG]]
                    rows.append((slot, r, topk))
            pre = t_embed_crop if variant == "center-crop" else t_embed
            self._log({"type": "run", "device": tag, "variant": variant,
                       "precompute_s": round(pre, 2), "decide_s": round(t_rank, 2),
                       "n_refs": n_refs, "val_pool_max": npool})
            for slot, r, topk in rows:
                self._log_result(tag, variant, slot, r, topk)
            self._emit(f"{tag.upper()} / {variant} 완료")

    # -- per-ref result line -----------------------------------------
    def _log_result(self, device, variant, slot, ref_item, scored) -> None:
        topk = [{"rank": i, "filename": Path(n).name, "score": round(float(s), 4)}
                for i, (n, s) in enumerate(scored[:TOPK_LOG])]
        self._log({
            "type": "result", "device": device, "variant": variant,
            "slot": slot, "ref_filename": Path(ref_item.path).name,
            "topk": topk,
        })
