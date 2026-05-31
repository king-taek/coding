"""매칭 가속 조합(레시피) 레지스트리 — '각 연산을 어느 장치에서 어떻게'.

한 레시피는 매칭 단계를 두 연산으로 나눠 정의한다.

  (1) **후보 선별(recall)** — 이미지에서 임베딩(특징 벡터)을 '뽑아내고' 코사인
      유사도로 상위 후보를 추리는 단계.  ``recall`` 장치가 담당한다.
        none      : 임베딩 없음(고전 전수 비교)
        cpu       : CPU(OpenVINO)로 임베딩 추출
        gpu       : Intel GPU로 임베딩 추출
        npu       : Intel NPU로 임베딩 추출  ← '데이터를 NPU로 뽑아낸다'
        gpu+npu   : 임베딩 작업을 두 장치에 분담(또는 앙상블)

  (2) **정밀 재채점/계산(scoring)** — pHash+ORB+SSIM 고전 점수로 후보를 다시
      매기고 임베딩 코사인과 z-융합하는 단계.  **항상 CPU** 가 담당한다
      ('NPU 로 뽑고 CPU 로 계산' 이 곧 ``recall=npu, scoring=fusion``).
        classical : 모든 후보를 CPU 고전 전수 비교(임베딩 미사용)
        embed_only: 임베딩 코사인 순위만 사용(재채점 없음 — 최속/정확도↓)
        fusion    : 임베딩 recall + CPU 고전 재채점 + z-융합(정확도 최상)

정확도는 백본(임베딩 모델) 종류가 아니라 **CPU 고전 융합**이 좌우한다
(docs/NPU 효율성 분석 보고서).  그래서 대부분의 실전 레시피는 ``fusion`` 이고,
장치 조합은 주로 '임베딩을 누가/어떻게 더 빨리 뽑느냐'의 속도 문제다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set

# 임베딩 백본 식별자(embedder_openvino 와 동일 문자열) — torch/openvino 미설치
# 환경에서도 이 모듈을 import 할 수 있도록 문자열 상수로 직접 정의한다.
MODEL_MOBILENET_V3 = "mobilenet_v3_small"   # GPU 기본(576-d)
MODEL_RESNET18 = "resnet18"                 # NPU 대조 모델(512-d)

# 연산 단계 라벨(상수) — 오타 방지.
RECALL_NONE = "none"
RECALL_CPU = "cpu"
RECALL_GPU = "gpu"
RECALL_NPU = "npu"
RECALL_GPU_NPU = "gpu+npu"

SCORE_CLASSICAL = "classical"
SCORE_EMBED_ONLY = "embed_only"
SCORE_FUSION = "fusion"


@dataclass(frozen=True)
class Recipe:
    """매칭 가속 조합 한 가지.  ``desc`` 가 '각 연산을 어떻게'를 사람이 읽게 설명."""

    key: str                       # 안정적 식별자(파일/기록 키)
    name: str                      # 짧은 한국어 라벨
    recall: str                    # RECALL_*
    scoring: str                   # SCORE_*
    embed_model: str = ""          # MODEL_* / model_zoo id (recall 이 임베딩일 때)
    embed_batch: int = 1           # 정적 배치 B (GPU 는 16 권장)
    fusion_topk: int = 40          # 고전 재채점 깊이(fusion 일 때)
    center_crop: bool = False      # 고전 재채점 시 중앙 30% crop
    concurrency: int = 32          # 동시 in-flight 추론 상한(병렬 수준)
    ensemble: bool = False         # gpu+npu 를 '분담' 대신 '앙상블'로(대조군)
    # ── NPU 사용 방식 노브(병렬 수준/멀티스레드/다중 동시 작업) ─────────────
    perf_hint: str = "THROUGHPUT"  # OpenVINO PERFORMANCE_HINT (THROUGHPUT/LATENCY/CUMULATIVE_THROUGHPUT)
    streams: int = 0               # NUM_STREAMS (0=자동) — 다중 동시 추론 스트림
    preprocess_threads: int = 0    # 전처리(디코드/리사이즈) 멀티스레드 수(0=자동)
    input_px: int = 0              # 입력 해상도(0=기본 256) — 처리량↔표현력
    # ── CPU 재채점(rerank) 고속화 노브 ─────────────────────────────────────
    rerank: str = "classical"      # classical / phash / phash_ssim / orb_ssim / phash_orb / orb / ssim
    rerank_workers: int = 0        # 재채점 병렬 워커 수(0=직렬) — CPU 멀티코어 활용
    orb_nfeatures: int = 0         # ORB 검출 특징 수(0=기본 500) — 줄이면 ORB 비용↓
    orb_center_weight: float = 0.0 # 중앙-가중 ORB(0=끔) — defect 정중앙 매치에 가중
    npu_defect_assist: bool = False # NPU 병렬 보조기 — 중앙(defect) 임베딩을 3번째 융합 신호로
    # ── 중앙-인식(center-aware) 재채점 — defect 이 정중앙인 특성 활용 ──────────
    #   region_fusion: 중앙(defect) crop 점수 + 풀 ROI(주변 패턴) 점수를 가중 융합.
    #   cascade: 중앙 점수로 거칠게 추려(coarse) 풀 ROI 로 정밀 재채점(fine)만 — 속도↑.
    region_fusion: bool = False
    cascade: bool = False
    center_ratio: float = 0.0      # 중앙 crop 비율(0=0.3) — defect 신호 영역 크기
    center_weight: float = 0.0     # region_fusion 시 중앙(defect) 가중(0=0.6)
    cascade_keep: int = 0          # cascade 시 coarse 후 남길 후보 수(0=8)
    # ── 모델 주머니(키포인트/이상탐지 등 전용 채점기 라우팅) ───────────────
    method: str = ""               # "" / model_zoo family (keypoint/anomaly 등)
    needs: str = ""                # 대상 장비에 필요한 추가 패키지/가중치(폴백 안내용)
    tag: str = "core"              # 그룹(core / npu_sweep / npu_only / fast_rerank / model_zoo)
    diagnostic: bool = False       # 함정/대조용(평소엔 불필요) — 기본 실험에서 제외
    desc: str = ""                 # 각 연산을 어느 장치에서 어떻게 하는지

    # ------------------------------------------------------------------
    def required_devices(self) -> Set[str]:
        """이 레시피가 실제 측정되려면 있어야 하는 가속 장치 집합(없으면 폴백)."""
        req: Set[str] = set()
        if self.recall in (RECALL_GPU, RECALL_GPU_NPU):
            req.add("GPU")
        if self.recall in (RECALL_NPU, RECALL_GPU_NPU):
            req.add("NPU")
        return req

    def uses_embedding(self) -> bool:
        return self.recall in (RECALL_CPU, RECALL_GPU, RECALL_NPU, RECALL_GPU_NPU)

    def to_cfg(self, base_cfg=None, *, bench_no_cache: bool = True):
        """이 레시피에 대응하는 ``SimilarityConfig`` 생성.

        벤치마크는 항상 ``bench_no_cache=True`` 로 '처음 매칭처럼' 측정한다.
        ``base_cfg`` 가 주어지면 그 값(예: 임계치 무관 필드)을 출발점으로 삼는다.
        """
        from .. import config as _config
        engine = "basic" if self.scoring == SCORE_CLASSICAL else "efficiency"
        return _config.SimilarityConfig(
            engine=engine,
            center_crop=bool(self.center_crop),
            center_ratio=float(self.center_ratio or 0.0),
            top_k=int(self.fusion_topk),
            persist_scores=False,            # 벤치마크는 점수 영속 캐시도 끔
            accel_concurrency=int(self.concurrency),
            use_cpu=True,
            use_gpu=self.recall in (RECALL_GPU, RECALL_GPU_NPU),
            use_npu=self.recall in (RECALL_NPU, RECALL_GPU_NPU),
            embed_batch=int(self.embed_batch),
            bench_no_cache=bool(bench_no_cache),
            orb_nfeatures=int(self.orb_nfeatures),
            orb_center_weight=float(self.orb_center_weight or 0.0),
        )


# ---------------------------------------------------------------------------
# 레지스트리 — 최소 10가지 이상.  '최적이라 생각되는' 조합 + 대조/스윕.
# ---------------------------------------------------------------------------
REGISTRY: List[Recipe] = [
    # ── 정확도 기준선(GOLD) ────────────────────────────────────────────
    Recipe(
        key="cpu_classical_full", name="CPU 고전 전수(기준 정확도)",
        recall=RECALL_NONE, scoring=SCORE_CLASSICAL,
        desc=("CPU 가 모든 (기준,검증) 쌍을 pHash+ORB+SSIM 으로 전수 비교한다. "
              "임베딩/가속 미사용 — 가장 느리지만 정확도의 '정답 기준선'."),
    ),
    # ── 현행 운영(속도 기준선) ─────────────────────────────────────────
    Recipe(
        key="gpu_fusion_b16", name="GPU 융합 batch16 (현행)",
        recall=RECALL_GPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=40,
        desc=("Intel GPU(MobileNetV3)가 임베딩을 batch16 으로 뽑아 코사인 후보를 "
              "추리고, 상위 40개를 CPU 고전으로 재채점해 z-융합한다. 현행 고효율 모드."),
    ),
    # ── 사용자 아이디어: NPU 로 뽑고 CPU 로 계산 ───────────────────────
    Recipe(
        key="npu_extract_cpu_fuse", name="NPU 추출+CPU 계산(ResNet18)",
        recall=RECALL_NPU, scoring=SCORE_FUSION,
        embed_model=MODEL_RESNET18, embed_batch=1, fusion_topk=40,
        desc=("Intel NPU(ResNet18)가 이미지 임베딩을 '뽑아내고', CPU 가 고전 점수 "
              "계산 + z-융합을 맡는다. 사용자 제안 조합(데이터=NPU, 계산=CPU)."),
    ),
    Recipe(
        key="npu_mbnet_cpu_fuse", name="NPU 추출+CPU 계산(MobileNet)",
        recall=RECALL_NPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=1, fusion_topk=40,
        desc=("NPU 가 GPU 와 동일 백본(MobileNetV3)으로 임베딩을 뽑고 CPU 가 융합. "
              "GPU 와 같은 모델이라 분담/대조에 적합."),
    ),
    # ── 가속기 없는 PC 대비책 ──────────────────────────────────────────
    Recipe(
        key="cpu_embed_fusion", name="CPU 임베딩+CPU 융합",
        recall=RECALL_CPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=8, fusion_topk=40,
        desc=("GPU/NPU 가 없을 때 CPU(OpenVINO)로 임베딩 추출 후 같은 CPU 가 고전 "
              "융합. 가속기 부재 환경의 폴백 성능 측정용."),
    ),
    # ── 임베딩 단독(최속/정확도 한계 확인) ─────────────────────────────
    Recipe(
        key="gpu_embed_only", name="GPU 임베딩 단독(재채점 없음)",
        recall=RECALL_GPU, scoring=SCORE_EMBED_ONLY,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, diagnostic=True,
        desc=("GPU 임베딩 코사인 순위만으로 매칭(CPU 재채점 생략). 가장 빠르지만 "
              "정확도가 낮아 '왜 융합이 필요한가'를 보여주는 대조군."),
    ),
    # ── 3장치 '분담'(속도 핵심 시도) ───────────────────────────────────
    Recipe(
        key="gpu_npu_split_fusion", name="GPU+NPU 분담 임베딩+CPU 융합",
        recall=RECALL_GPU_NPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=40,
        ensemble=False,
        desc=("임베딩 작업을 GPU 와 NPU 에 '절반씩 분담'해 동시에 뽑아 추출 처리량을 "
              "올리고(중복 아님), CPU 가 융합. 3장치를 속도에 활용하는 핵심 조합."),
    ),
    # ── 재채점 깊이 스윕(속도↔정확도) ──────────────────────────────────
    Recipe(
        key="gpu_fusion_topk20", name="GPU 융합 topk20(얕은 재채점)",
        recall=RECALL_GPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=20,
        desc=("GPU 임베딩 후 상위 20개만 CPU 재채점. CPU 단계가 짧아 더 빠르나 "
              "정답이 21위 밖이면 놓칠 수 있어 정확도 검증 필수."),
    ),
    Recipe(
        key="gpu_fusion_topk60", name="GPU 융합 topk60(깊은 재채점)",
        recall=RECALL_GPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=60,
        desc=("상위 60개를 CPU 재채점. 더 깊게 보장하나 CPU 비용↑. 정확도 여유가 "
              "필요한 어려운 웨이퍼용."),
    ),
    # ── 중앙 crop(교차 호기 정확도) ────────────────────────────────────
    Recipe(
        key="gpu_fusion_crop", name="GPU 융합+중앙30%crop",
        recall=RECALL_GPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=40,
        center_crop=True,
        desc=("고전 재채점을 사진 중앙 30% 로 한정. 호기 간 외곽 차이를 줄여 "
              "교차 호기 정확도를 높이는 변형(보고서 권고)."),
    ),
    # ── 배치 스윕(GPU 처리량 함정 확인) ────────────────────────────────
    Recipe(
        key="gpu_fusion_b1", name="GPU 융합 batch1(함정 재현)",
        recall=RECALL_GPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=1, fusion_topk=40,
        diagnostic=True,
        desc=("GPU batch=1 — 보고서상 처리량이 ~1 img/s 로 폭락(멈춤)하는 함정 "
              "조합. 운영에서 피해야 함을 수치로 보이는 대조군."),
    ),
    Recipe(
        key="gpu_fusion_b4", name="GPU 융합 batch4",
        recall=RECALL_GPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=4, fusion_topk=40,
        desc=("GPU batch=4 — 처리량이 정상화되기 시작하는 지점. batch16 과 속도 비교용."),
    ),
    # ── 3장치 '앙상블'(보고서 안티패턴 대조) ───────────────────────────
    Recipe(
        key="gpu_npu_ensemble_fusion", name="GPU+NPU 앙상블+CPU 융합(대조)",
        recall=RECALL_GPU_NPU, scoring=SCORE_FUSION,
        embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=40,
        ensemble=True, diagnostic=True,
        desc=("GPU 와 NPU 가 '각각 전체'를 임베딩해 두 코사인을 평균(앙상블). "
              "보고서상 정확도 이득 0 · 시간 약 2배인 안티패턴 — 분담과 대조."),
    ),
]

# 추천/대조의 기준이 되는 레시피 키.
BASELINE_ACCURACY_KEY = "cpu_classical_full"   # 정확도의 정답 기준선
PRODUCTION_SPEED_KEY = "gpu_fusion_b16"        # 현행(속도 3배 목표의 분모)

# 실측으로 **정확도 보존(97.6%, 현행 동률) 확인된 '생존자'** 재채점 레시피.  3배 달성은
# `rr_parallel`(×3.95)로 확정됐고, 이 묶음만 추가 실험 대상으로 남긴다.  나머지(임베딩 장치
# 교체 ×1.02·ORB 제거 시 정확도 붕괴·NPU 배치 손상·center-aware 비효율·모델주머니)는
# 옵션에서 내리고 아카이브(`all+`/그룹)로만 둔다.
SURVIVOR_KEYS: List[str] = [
    "rr_parallel",            # 재채점 항 동일·16스레드 병렬 — ×3.95 @97.6% (추천)
    "cpu_rr_orb_only",        # ORB 단독 — 97.6%
    "cpu_rr_phash_orb",       # pHash+ORB(SSIM 뺌) — 97.6%
    "cpu_rr_parallel8",       # 병렬8 — 97.6%
    "cpu_rr_parallel16",      # 병렬16 — 97.6%
    "cpu_rr_parallel32",      # 병렬32 — 97.6%
    "cpu_rr_orb128",          # ORB 특징 128 — 97.6%
    "cpu_rr_orb256",          # ORB 특징 256 — 97.6%
    "rr_orb_ssim",            # ORB+SSIM — 97.6%
    "cpu_rr_orb256_parallel", # ORB256+병렬 — 95.1%(1장 차·빠름)
]

# 중앙-가중 ORB 신규 실험(단일 패스) — defect 정중앙 활용.  옵션에 노출해 측정한다.
CENTER_ORB_KEYS: List[str] = ["rr_orb_center50", "rr_orb_center70", "rr_fusion_center50"]
# NPU 병렬 보조기 신규 실험(3신호 융합).
NPU_ASSIST_KEYS: List[str] = ["npu_assist_r25", "npu_assist_r20"]

# 개발자 모드 '메인 옵션' = 앵커(gold·현행) + 생존자 + 중앙-가중 ORB + NPU 보조 신규 실험.
MAIN_KEYS: List[str] = ([BASELINE_ACCURACY_KEY, PRODUCTION_SPEED_KEY]
                        + SURVIVOR_KEYS + CENTER_ORB_KEYS + NPU_ASSIST_KEYS)

# '빠른' 프리셋(기본 선택) — 앵커 + 핵심 생존자 소수(빠른 반복용).  현행을 **항상 포함**해
# 추천 엔진이 production 대비 speedup 을 정상 계산한다.
QUICK_KEYS: List[str] = [
    BASELINE_ACCURACY_KEY,         # 정확도 gold(100%) — 일치율 기준선
    PRODUCTION_SPEED_KEY,          # 현행 anchor(97.6%) = '3배'의 분모
    "rr_parallel",                 # ×3.95 @97.6% (추천)
    "cpu_rr_orb_only",             # 97.6% 생존자
    "cpu_rr_phash_orb",            # 97.6% 생존자
    "cpu_rr_parallel16",           # 97.6% 생존자
]

# 동일-런 head-to-head — 현행 + 재채점 생존자(반복 측정으로 분산까지 확인).
FACEOFF_KEYS: List[str] = [
    BASELINE_ACCURACY_KEY, PRODUCTION_SPEED_KEY,
    "rr_parallel", "cpu_rr_orb_only", "cpu_rr_phash_orb", "cpu_rr_parallel16",
]


# ===========================================================================
# (A) NPU 사용 방식 스윕 — 모델/배치/병렬수준/스트림/멀티스레드/해상도 ≥20가지.
#     추천(NPU MobileNet) 주변에서 한 축씩만 바꿔 원인 귀속을 명확히 한다.
#     모든 항목 recall=NPU, scoring=fusion(정확도 보존).  대상 장비의 NPU 에서 측정.
# ===========================================================================
def _npu(key, name, desc, **kw) -> Recipe:
    base = dict(recall=RECALL_NPU, scoring=SCORE_FUSION,
                embed_model=MODEL_MOBILENET_V3, embed_batch=8, fusion_topk=40,
                concurrency=32, tag="npu_sweep")
    base.update(kw)
    return Recipe(key=key, name=name, desc=desc, **base)


def _build_npu_sweep() -> List[Recipe]:
    out: List[Recipe] = []
    # 1) 정적 배치 B 스윕 — NPU 처리량에 배치가 주는 영향.
    for b in (1, 4, 8, 16, 32):
        out.append(_npu(f"npu_b{b}", f"NPU 배치{b}",
                        f"NPU(MobileNet) 임베딩 배치={b}. 정적 배치가 NPU 처리량에 "
                        f"주는 영향 측정(NPU 는 보고서상 배치 이득이 작음).",
                        embed_batch=b))
    # 2) 동시 추론 수(병렬 수준) 스윕 — AsyncInferQueue in-flight 요청 수.
    for c in (1, 2, 4, 8, 16, 32, 64, 96):
        out.append(_npu(f"npu_c{c}", f"NPU 동시추론{c}",
                        f"NPU 동시 in-flight 추론 {c}개(병렬 수준). 다중 동시 작업으로 "
                        f"NPU 파이프라인을 채워 유휴를 줄이는 효과 측정.",
                        concurrency=c))
    # 3) 성능 힌트 — 처리량 vs 지연 vs 누적 처리량.
    for h in ("THROUGHPUT", "LATENCY", "CUMULATIVE_THROUGHPUT"):
        out.append(_npu(f"npu_hint_{h.lower()}", f"NPU 힌트 {h}",
                        f"OpenVINO PERFORMANCE_HINT={h}. 처리량/지연 트레이드오프를 "
                        f"NPU 에서 비교.",
                        perf_hint=h))
    # 4) 스트림 수 — NUM_STREAMS(다중 동시 작업 스트림).
    for s in (1, 2, 4):
        out.append(_npu(f"npu_streams{s}", f"NPU 스트림{s}",
                        f"NPU NUM_STREAMS={s}. 여러 추론 스트림을 동시에 돌려 처리량을 "
                        f"올리는 효과(메모리 여유 필요).",
                        streams=s))
    # 5) 전처리 멀티스레드 — 디코드/리사이즈 CPU 병렬이 NPU 공급을 따라가는지.
    for t in (2, 4, 8):
        out.append(_npu(f"npu_prep{t}", f"NPU 전처리{t}스레드",
                        f"전처리(디코드/리사이즈)를 {t}스레드로 — NPU 가 굶지 않게 텐서 "
                        f"공급을 멀티스레드로 채운다.",
                        preprocess_threads=t))
    # 6) 입력 해상도 — 처리량↔표현력.
    for px in (224, 256):
        out.append(_npu(f"npu_px{px}", f"NPU 입력{px}px",
                        f"입력 해상도 {px}px. 작을수록 NPU 처리량↑(표현력 약간↓).",
                        input_px=px))
    # 7) NPU 위 모델 변형 — 같은 NPU 에서 백본만 교체.
    for m in ("mobilenet_v3_small", "resnet18", "mobilenet_v2",
              "squeezenet1_1", "efficientnet_b0"):
        out.append(_npu(f"npu_model_{m}", f"NPU 모델 {m}",
                        f"NPU 에서 {m} 백본으로 임베딩 추출(추출비용↔표현력 비교).",
                        embed_model=m, needs=_zoo_needs(m)))
    return out


# ===========================================================================
# (C) NPU 단독 채점 — CPU 재채점 없이 NPU 임베딩 코사인만으로 매칭.
#     'NPU 단독이 빠르고 정확하면 CPU 불필요'를 직접 검증.  scoring=embed_only.
# ===========================================================================
def _build_npu_only() -> List[Recipe]:
    out: List[Recipe] = []
    for m, b in (("mobilenet_v3_small", 8), ("resnet18", 8),
                 ("mobilevit_s", 8), ("efficientnet_b0", 8)):
        out.append(Recipe(
            key=f"npu_only_{m}", name=f"NPU 단독 {m}",
            recall=RECALL_NPU, scoring=SCORE_EMBED_ONLY,
            embed_model=m, embed_batch=b, concurrency=32, tag="npu_only",
            needs=_zoo_needs(m),
            desc=(f"NPU 가 {m} 임베딩을 뽑아 코사인 순위만으로 매칭한다(CPU 재채점 "
                  f"없음). NPU 단독으로 충분히 빠르고 정확하면 CPU 가 불필요함을 검증.")))
    # NPU 단독 + 가벼운 pHash 보정(임베딩은 NPU, 최소 CPU 한 항목만) — 단독에 근접.
    out.append(Recipe(
        key="npu_only_mbnet_phash", name="NPU 단독+pHash 보정",
        recall=RECALL_NPU, scoring=SCORE_FUSION, embed_model=MODEL_MOBILENET_V3,
        embed_batch=8, fusion_topk=10, rerank="phash", rerank_workers=8,
        concurrency=32, tag="npu_only",
        desc=("거의 NPU 단독 — 상위 10개만 CPU pHash(가장 싼 1개 항목)로 살짝 보정. "
              "완전 단독과 전수 융합의 중간(CPU 부하 최소).")))
    return out


# ===========================================================================
# (D) CPU 재채점 고속화 — 병목인 고전 재채점을 싸게/병렬로.  scoring=fusion.
#     pHash 는 사전계산 해시 비교라 매우 싸고, ORB(디스크립터 정합)·SSIM 이 비싸다.
#     → ORB/SSIM 를 빼거나 병렬화해 재채점 시간을 줄인다(정확도 검증 필수).
# ===========================================================================
def _build_fast_rerank() -> List[Recipe]:
    g = dict(recall=RECALL_GPU, scoring=SCORE_FUSION,
             embed_model=MODEL_MOBILENET_V3, embed_batch=16, fusion_topk=40,
             tag="fast_rerank")
    return [
        Recipe(key="rr_phash", name="고속재채점 pHash단독",
               rerank="phash", rerank_workers=8, **g,
               desc=("재채점을 pHash(사전계산 해시 비교)만으로 — ORB/SSIM 생략. "
                     "가장 싼 재채점. 정확도가 유지되면 CPU 시간 대폭↓.")),
        Recipe(key="rr_phash_ssim", name="고속재채점 pHash+SSIM",
               rerank="phash_ssim", rerank_workers=8, **g,
               desc=("ORB(디스크립터 정합, 가장 비쌈)만 빼고 pHash+SSIM 으로 재채점. "
                     "구조 유사도는 남겨 정확도 손실을 줄이며 속도↑.")),
        Recipe(key="rr_orb_ssim", name="고속재채점 ORB+SSIM",
               rerank="orb_ssim", rerank_workers=8, **g,
               desc=("pHash 만 빼고 ORB+SSIM. pHash 영향 분리용 대조.")),
        Recipe(key="rr_parallel", name="고속재채점 병렬(전체)",
               rerank="classical", rerank_workers=16, **g,
               desc=("재채점 항목은 그대로(정확도 동일)지만 ref 들을 16스레드로 병렬 "
                     "채점해 CPU 멀티코어로 시간↓. 정확도 100% 보존하며 속도만↑.")),
        Recipe(key="rr_phash_topk20", name="고속재채점 pHash+topk20",
               rerank="phash", rerank_workers=8, fusion_topk=20,
               recall=RECALL_GPU, scoring=SCORE_FUSION,
               embed_model=MODEL_MOBILENET_V3, embed_batch=16, tag="fast_rerank",
               desc=("싼 pHash 재채점 + 상위 20개만 — 깊이와 비용을 동시에 줄인 최속 "
                     "융합 후보. 정확도 검증 필수.")),
        Recipe(key="rr_npu_phash_parallel", name="NPU추출+pHash병렬재채점",
               recall=RECALL_NPU, scoring=SCORE_FUSION,
               embed_model=MODEL_MOBILENET_V3, embed_batch=8, fusion_topk=40,
               rerank="phash_ssim", rerank_workers=16, tag="fast_rerank",
               desc=("NPU 가 임베딩을 뽑고, CPU 는 ORB 를 뺀 pHash+SSIM 을 16스레드 "
                     "병렬로 재채점 — 추출=NPU·계산=경량/병렬 CPU 의 결합 최적 후보.")),
    ] + _build_cpu_rerank()


# ===========================================================================
# (D-2) CPU 매치 단계 고속화 — '끝까지 CPU'(recall=CPU 임베딩 후보 + CPU 재채점)로
#   매치 단계를 빠르게 만드는 ≥10가지 방법을 한 축씩 바꿔 측정한다.  병목인 CPU
#   재채점(특히 ORB)을 (1) 항 빼기 (2) 병렬화 (3) ORB 특징 수 줄이기 (4) 깊이 줄이기
#   (5) 중앙 crop 으로 공략한다.  전부 정확도 검증이 전제(떨어지면 추천 안 함).
# ===========================================================================
def _build_cpu_rerank() -> List[Recipe]:
    # CPU 끝까지 — CPU(OpenVINO)로 임베딩 후보를 추리고 CPU 가 고전 재채점.
    c = dict(recall=RECALL_CPU, scoring=SCORE_FUSION,
             embed_model=MODEL_MOBILENET_V3, embed_batch=8, fusion_topk=40,
             tag="fast_rerank")
    return [
        # (1) 항 빼기 — pHash 는 사전계산이라 매우 싸고, ORB(정합)·SSIM 이 비싸다.
        Recipe(key="cpu_rr_phash", name="CPU재채점 pHash단독",
               rerank="phash", rerank_workers=8, **c,
               desc=("CPU 후보 + 재채점을 pHash 만으로(ORB·SSIM 생략). 가장 싼 재채점 — "
                     "CPU 매치 단계 최속 후보. 정확도 유지되면 채택.")),
        Recipe(key="cpu_rr_phash_ssim", name="CPU재채점 pHash+SSIM(ORB뺌)",
               rerank="phash_ssim", rerank_workers=8, **c,
               desc=("가장 비싼 ORB 만 빼고 pHash+SSIM. 구조 유사도를 남겨 정확도 손실을 "
                     "줄이면서 ORB 비용을 없앤다.")),
        Recipe(key="cpu_rr_phash_orb", name="CPU재채점 pHash+ORB(SSIM뺌)",
               rerank="phash_orb", rerank_workers=8, **c,
               desc=("SSIM 만 빼고 pHash+ORB. SSIM(전 픽셀 비교) 비용을 없애 속도↑. "
                     "ORB 가 정확도에 기여하는지 분리 측정.")),
        Recipe(key="cpu_rr_orb_only", name="CPU재채점 ORB단독",
               rerank="orb", rerank_workers=8, **c,
               desc=("ORB 단독 재채점 — ORB 만의 변별력·비용을 분리해 본다(대조).")),
        Recipe(key="cpu_rr_ssim_only", name="CPU재채점 SSIM단독",
               rerank="ssim", rerank_workers=8, **c,
               desc=("SSIM 단독 재채점 — 구조 유사도만의 변별력·비용을 분리해 본다(대조).")),
        # (2) 병렬화 — 항은 그대로(정확도 동일) ref 를 멀티코어로 동시 채점.
        Recipe(key="cpu_rr_parallel8", name="CPU재채점 병렬8(전체)",
               rerank="classical", rerank_workers=8, **c,
               desc=("전체 항(pHash+ORB+SSIM) 그대로, ref 를 8스레드 병렬 채점 — "
                     "정확도 보존하며 멀티코어로 시간↓.")),
        Recipe(key="cpu_rr_parallel16", name="CPU재채점 병렬16(전체)",
               rerank="classical", rerank_workers=16, **c,
               desc=("전체 항 그대로 16스레드 병렬 — 코어 많을수록 이득. 정확도 100% 보존.")),
        Recipe(key="cpu_rr_parallel32", name="CPU재채점 병렬32(전체)",
               rerank="classical", rerank_workers=32, **c,
               desc=("전체 항 그대로 32스레드 병렬 — 과도구독 한계점 확인(코어 수 초과 시 이득 둔화).")),
        # (3) ORB 특징 수 줄이기 — ORB 검출/정합 비용은 특징 수에 비례.
        Recipe(key="cpu_rr_orb256", name="CPU재채점 ORB특징256",
               rerank="classical", rerank_workers=16, orb_nfeatures=256, **c,
               desc=("전체 융합이되 ORB 특징을 256개로(기본 500↓) — 검출/정합 비용을 줄여 "
                     "정확도를 크게 안 깎고 속도↑.")),
        Recipe(key="cpu_rr_orb128", name="CPU재채점 ORB특징128",
               rerank="classical", rerank_workers=16, orb_nfeatures=128, **c,
               desc=("ORB 특징 128개 — 더 공격적으로 ORB 비용↓. 정확도 한계 확인용.")),
        # (4) 재채점 깊이 줄이기 — 상위 K 만 정밀 채점.
        Recipe(key="cpu_rr_topk10", name="CPU재채점 깊이10",
               rerank="classical", rerank_workers=16, fusion_topk=10,
               recall=RECALL_CPU, scoring=SCORE_FUSION,
               embed_model=MODEL_MOBILENET_V3, embed_batch=8, tag="fast_rerank",
               desc=("임베딩 상위 10개만 CPU 정밀 재채점 — 깊이를 얕게 해 재채점 횟수↓. "
                     "정답이 10위 밖이면 놓치므로 정확도 검증 필수.")),
        Recipe(key="cpu_rr_topk20", name="CPU재채점 깊이20",
               rerank="classical", rerank_workers=16, fusion_topk=20,
               recall=RECALL_CPU, scoring=SCORE_FUSION,
               embed_model=MODEL_MOBILENET_V3, embed_batch=8, tag="fast_rerank",
               desc=("상위 20개만 정밀 재채점 — 깊이10 보다 안전, 전수보다 빠름.")),
        # (5) 중앙 crop — 재채점 영역(면적)을 줄여 ORB·SSIM 비용↓.
        Recipe(key="cpu_rr_crop", name="CPU재채점 중앙crop",
               rerank="classical", rerank_workers=16, center_crop=True, **c,
               desc=("재채점을 사진 중앙 30% 로 한정 — 비교 면적이 작아 ORB·SSIM 이 빨라지고, "
                     "호기 간 외곽 차이도 줄여 교차호기 정확도에 도움될 수 있음.")),
        # 실용 결합 후보 — 위 레버를 합쳐 '정확도 보존 + 최속' 을 노린다.
        Recipe(key="cpu_rr_light_parallel", name="CPU재채점 경량+병렬(추천후보)",
               rerank="phash_ssim", rerank_workers=16, fusion_topk=20,
               recall=RECALL_CPU, scoring=SCORE_FUSION,
               embed_model=MODEL_MOBILENET_V3, embed_batch=8, tag="fast_rerank",
               desc=("ORB 제거(pHash+SSIM) + 16스레드 병렬 + 상위 20개 — CPU 매치 단계를 "
                     "여러 레버로 동시에 줄인 실용 최속 후보.")),
        Recipe(key="cpu_rr_orb256_parallel", name="CPU재채점 ORB256+병렬(정확도보존형)",
               rerank="classical", rerank_workers=16, orb_nfeatures=256, fusion_topk=30,
               recall=RECALL_CPU, scoring=SCORE_FUSION,
               embed_model=MODEL_MOBILENET_V3, embed_batch=8, tag="fast_rerank",
               desc=("전체 항을 쓰되 ORB 특징만 256개로 줄이고 16스레드 병렬 — 정확도를 "
                     "최대한 지키면서 ORB 비용을 깎는 균형형 후보.")),
    ]


# ===========================================================================
# (B) 모델 주머니 — SuperPoint/LightGlue·PatchCore·PaDiM·CAE·U-Net·MobileViT.
#     주로 NPU 추출 + 전용 채점기(미설치 시 폴백+안내).  대상 장비에 패키지 필요.
# ===========================================================================
def _zoo_needs(model_id: str) -> str:
    try:
        from . import model_zoo as _mz
        return _mz.needs(model_id)
    except Exception:
        return ""


def _build_model_zoo() -> List[Recipe]:
    from . import model_zoo as _mz
    out: List[Recipe] = []
    # 임베딩 계열(ViT/AE) — NPU 추출 + CPU 융합.
    for m in ("mobilevit_s", "mobilevit_xs", "cae", "unet", "attention_unet"):
        sp = _mz.spec(m)
        out.append(Recipe(
            key=f"zoo_{m}", name=f"모델 {m}(NPU추출+융합)",
            recall=RECALL_NPU, scoring=SCORE_FUSION, embed_model=m,
            embed_batch=8, fusion_topk=40, concurrency=32, tag="model_zoo",
            needs=_mz.needs(m), method=_mz.family(m),
            desc=(sp.desc if sp else "") + " NPU 추출 + CPU 융합."))
    # 키포인트 정합(SuperPoint+LightGlue) — 전용 채점기.
    sp = _mz.spec("superpoint_lightglue")
    out.append(Recipe(
        key="zoo_superpoint_lightglue", name="SuperPoint+LightGlue(NPU)",
        recall=RECALL_NPU, scoring=SCORE_EMBED_ONLY,
        embed_model="superpoint_lightglue", method=_mz.FAMILY_KEYPOINT,
        needs=_mz.needs("superpoint_lightglue"), tag="model_zoo",
        desc=(sp.desc if sp else "")))
    # 이상탐지(PatchCore/PaDiM) — 전용 채점기.
    for m in ("patchcore", "padim"):
        sp = _mz.spec(m)
        out.append(Recipe(
            key=f"zoo_{m}", name=f"{m}(이상탐지 거리)",
            recall=RECALL_NPU, scoring=SCORE_EMBED_ONLY, embed_model=m,
            method=_mz.FAMILY_ANOMALY, needs=_mz.needs(m), tag="model_zoo",
            desc=(sp.desc if sp else "")))
    return out


# ===========================================================================
# (C) 중앙-인식(center-aware) — defect 이 정중앙인 특성 활용.  사용자 제안:
#     "주변 패턴 유사도를 먼저 보고 defect 유사도를 다시 본다."
#       · region_fusion: 중앙(defect) + 풀ROI(주변) 점수를 가중 융합(한 패스).
#       · cascade: 중앙 점수로 거칠게 추려(coarse) → 풀ROI 정밀 재채점(fine)만(속도↑).
#     베이스는 현행과 동일(GPU MobileNetV3 b16, topk40, fusion) — 정확도 비교 공정.
# ===========================================================================
def _build_center_aware() -> List[Recipe]:
    base = dict(recall=RECALL_GPU, scoring=SCORE_FUSION,
                embed_model=MODEL_MOBILENET_V3, embed_batch=16,
                fusion_topk=40, tag="center")
    out: List[Recipe] = [
        # ── A. 영역분해 융합 — 중앙 비율/가중 스윕 ──────────────────────
        Recipe(key="center_fusion_r25_w60", name="중앙융합 r0.25·w0.6",
               region_fusion=True, center_ratio=0.25, center_weight=0.6, **base,
               desc=("defect(중앙 25%) 유사도와 주변 패턴(풀 ROI) 유사도를 따로 "
                     "계산해 중앙에 0.6 가중으로 융합 — 사용자 2단계 아이디어(한 패스).")),
        Recipe(key="center_fusion_r25_w70", name="중앙융합 r0.25·w0.7",
               region_fusion=True, center_ratio=0.25, center_weight=0.7, **base,
               desc="중앙(defect) 가중을 0.7 로 — defect 비중을 더 키운 변형."),
        Recipe(key="center_fusion_r20_w60", name="중앙융합 r0.20·w0.6",
               region_fusion=True, center_ratio=0.20, center_weight=0.6, **base,
               desc="중앙 crop 을 더 좁혀(20%) defect 에 집중한 변형."),
        Recipe(key="center_fusion_r30_w50", name="중앙융합 r0.30·w0.5",
               region_fusion=True, center_ratio=0.30, center_weight=0.5, **base,
               desc="중앙 30%·동등 가중 — 주변 맥락을 더 보존한 변형."),
        # 정확도 보존 + 속도(병렬) 결합 — region_fusion 에 16스레드 병렬.
        Recipe(key="center_fusion_r25_w60_par", name="중앙융합 r0.25·w0.6·병렬16",
               region_fusion=True, center_ratio=0.25, center_weight=0.6,
               rerank_workers=16, **base,
               desc="중앙융합(r0.25·w0.6) + ref 16스레드 병렬 — 정확도·속도 동시 노림."),
        # ── B. 거친→정밀 캐스케이드 — coarse(중앙)로 추리고 fine(풀) 정밀 ──
        Recipe(key="center_cascade_r25_k8", name="캐스케이드 r0.25·keep8",
               cascade=True, center_ratio=0.25, cascade_keep=8, **base,
               desc=("①중앙(defect) 점수로 topk 를 8개로 거칠게 추리고 "
                     "②풀 ROI 고전을 그 8개에만 — 비싼 풀 재채점을 줄여 속도↑.")),
        Recipe(key="center_cascade_r25_k12", name="캐스케이드 r0.25·keep12",
               cascade=True, center_ratio=0.25, cascade_keep=12, **base,
               desc="캐스케이드 keep 를 12 로 — 정밀 단계 후보를 더 남긴 변형."),
        Recipe(key="center_cascade_r25_k8_par", name="캐스케이드 r0.25·keep8·병렬16",
               cascade=True, center_ratio=0.25, cascade_keep=8,
               rerank_workers=16, **base,
               desc="캐스케이드(keep8) + 16스레드 병렬 — center-aware 속도 최적 후보."),
    ]
    return out


CENTER_AWARE: List[Recipe] = _build_center_aware()


# ===========================================================================
# (C2) 중앙-가중 ORB — defect 정중앙 특성을 **단일 패스**로 활용(영역분해 A 의 실패를
#      교정).  ROI 를 384px 로 재정규화하는 파이프라인 특성상 중앙 crop 은 비용을 못
#      줄였으므로, 추가 패스 없이 ORB 매치를 '중앙 근접도'로 가중한다(비용 거의 동일).
#      베이스는 현행과 동일(GPU MobileNetV3 b16·topk40·병렬16) — 정확도 비교 공정.
# ===========================================================================
def _build_center_orb() -> List[Recipe]:
    base = dict(recall=RECALL_GPU, scoring=SCORE_FUSION,
                embed_model=MODEL_MOBILENET_V3, embed_batch=16,
                fusion_topk=40, rerank_workers=16, tag="orb_center")
    return [
        Recipe(key="rr_orb_center50", name="ORB중앙가중0.5(단독·병렬)",
               rerank="orb", orb_center_weight=0.5, **base,
               desc=("ORB 단독 재채점 + 중앙(defect) 근접 매치에 0.5 가중 — 배경(반복 "
                     "패턴) 매치 영향을 줄여 defect 판별력↑.  단일 패스(추가 비용 없음).")),
        Recipe(key="rr_orb_center70", name="ORB중앙가중0.7(단독·병렬)",
               rerank="orb", orb_center_weight=0.7, **base,
               desc="ORB 단독 + 중앙 가중 0.7 — defect 비중을 더 키운 변형."),
        Recipe(key="rr_fusion_center50", name="융합+ORB중앙가중0.5(병렬)",
               rerank="classical", orb_center_weight=0.5, **base,
               desc="전체 고전(pHash+ORB+SSIM) 융합에서 ORB 항만 중앙 가중 0.5 — 현행 "
                    "정확도를 유지하며 defect 신호를 강화하는 보수적 변형."),
    ]


CENTER_ORB: List[Recipe] = _build_center_orb()


# ===========================================================================
# (C3) NPU 병렬 보조기 — 재채점(CPU) 동안 노는 NPU 에 '진짜 일'을 준다: 상위 후보의
#      **중앙(defect) crop 임베딩을 NPU(batch=1, 배치 정확도 버그 회피)** 로 인코딩해
#      (임베딩 코사인 + CPU 고전 + NPU defect) **3신호 z-융합**.  GPU 임베딩 recall 은
#      그대로(=현행)고, NPU 는 defect 신호만 더한다.  NPU 없으면 2신호로 자동 폴백.
# ===========================================================================
def _build_npu_assist() -> List[Recipe]:
    base = dict(recall=RECALL_GPU, scoring=SCORE_FUSION,
                embed_model=MODEL_MOBILENET_V3, embed_batch=16,
                fusion_topk=40, rerank_workers=16, npu_defect_assist=True,
                tag="npu_assist")
    return [
        Recipe(key="npu_assist_r25", name="NPU보조 defect임베딩 r0.25(3신호)",
               center_ratio=0.25, **base,
               desc=("CPU 재채점과 병행해 NPU 가 상위 후보의 중앙 25%(defect) 임베딩을 "
                     "batch=1 로 뽑아 3번째 신호로 융합 — 3장치(GPU·CPU·NPU) 동시 활용.")),
        Recipe(key="npu_assist_r20", name="NPU보조 defect임베딩 r0.20(3신호)",
               center_ratio=0.20, **base,
               desc="NPU defect 임베딩 영역을 더 좁혀(20%) defect 에 집중한 변형."),
    ]


NPU_ASSIST: List[Recipe] = _build_npu_assist()


# ===========================================================================
# (D2) GPU 임베딩 모델 선택 — NPU 없는 PC 는 GPU 로 임베딩한다.  어떤 백본이 정답을
#      후보 상위에 잘 올리는지(후보 recall) 비교해 고르기 위한 묶음.  embed_only 는
#      순수 임베딩 순위(=후보 recall)를, fusion 은 재채점 포함 최종 정확도를 본다.
# ===========================================================================
def _build_gpu_models() -> List[Recipe]:
    return [
        Recipe(key="gpu_embed_resnet18", name="GPU 임베딩 ResNet18(후보순위)",
               recall=RECALL_GPU, scoring=SCORE_EMBED_ONLY,
               embed_model=MODEL_RESNET18, embed_batch=16, tag="gpu_models",
               desc=("GPU(ResNet18) 임베딩 코사인 순위만 — 재채점 없이 '정답을 후보 상위에 "
                     "올리는 능력'(후보 recall)을 MobileNetV3 와 비교.")),
        Recipe(key="gpu_fusion_resnet18", name="GPU 융합 ResNet18(병렬)",
               recall=RECALL_GPU, scoring=SCORE_FUSION,
               embed_model=MODEL_RESNET18, embed_batch=16, fusion_topk=40,
               rerank_workers=16, tag="gpu_models",
               desc="GPU(ResNet18) 임베딩 + CPU 병렬 재채점 — MobileNetV3(현행)과 최종 정확도 비교."),
    ]


GPU_MODELS: List[Recipe] = _build_gpu_models()
# 모델 선택 비교 프리셋 — MobileNetV3(현행) vs ResNet18, 후보순위 + 최종.
GPU_MODEL_KEYS: List[str] = ["gpu_embed_only", "gpu_fusion_b16",
                             "gpu_embed_resnet18", "gpu_fusion_resnet18"]


NPU_SWEEP: List[Recipe] = _build_npu_sweep()
NPU_ONLY: List[Recipe] = _build_npu_only()
FAST_RERANK: List[Recipe] = _build_fast_rerank()
MODEL_ZOO: List[Recipe] = _build_model_zoo()

# 확장 그룹(이름 → 레시피 리스트).  'core' = 기본 레지스트리.
#   center      = 중앙-인식(사용자 제안) 신규 실험군.
#   npu-sweep/npu-only/fast-rerank/model-zoo = opt-in 아카이브(대부분 사패 입증) —
#     기본 프리셋에는 없고, 필요할 때만 그룹/`all+` 로 펼쳐 측정한다.
GROUPS = {
    "core": REGISTRY,
    "center": CENTER_AWARE,
    "orb-center": CENTER_ORB,
    "npu-assist": NPU_ASSIST,
    "npu-sweep": NPU_SWEEP,
    "npu-only": NPU_ONLY,
    "fast-rerank": FAST_RERANK,
    "model-zoo": MODEL_ZOO,
}
# 'gpu-models' 는 그룹이 아니라 **비교 프리셋**(MobileNetV3 현행 + ResNet18) — select() 에서 처리.
ALL_EXTENDED: List[Recipe] = (REGISTRY + CENTER_AWARE + CENTER_ORB + NPU_ASSIST
                              + GPU_MODELS + NPU_SWEEP + NPU_ONLY + FAST_RERANK
                              + MODEL_ZOO)
_BY_KEY = {r.key: r for r in ALL_EXTENDED}


def by_key(key: str) -> Recipe:
    if key in _BY_KEY:
        return _BY_KEY[key]
    raise KeyError(key)


def all_keys() -> List[str]:
    return [r.key for r in REGISTRY]


def all_extended_keys() -> List[str]:
    return [r.key for r in ALL_EXTENDED]


def group(name: str) -> List[Recipe]:
    return list(GROUPS.get(name, []))


def quick_recipes() -> List[Recipe]:
    """'빠른' 프리셋 레시피(``QUICK_KEYS`` 순서)."""
    return [by_key(k) for k in QUICK_KEYS if k in _BY_KEY]


def main_recipes() -> List[Recipe]:
    """개발자 모드에 **노출하는 메인 옵션** = 앵커 + 생존자(``MAIN_KEYS``).

    입증된 사패는 옵션에서 내렸다(아카이브는 ``all+``/그룹으로만)."""
    return [by_key(k) for k in MAIN_KEYS if k in _BY_KEY]


def explicit_keys(keys=None) -> Set[str]:
    """사용자가 **개별 레시피 키로 직접 고른** 것만 추출(그룹명/전체 토큰 제외).

    ``select`` 와 같은 입력을 받되, ``all``/``all+``/그룹명은 '개별 명시'가 아니므로
    뺀다.  벤치마크가 '이 키는 스킵하지 말고 그대로 측정' 판단에 쓴다.

    ``"quick"`` 은 핵심 소수를 **개별 명시**한 것으로 보고 QUICK_KEYS 로 펼친다(그래야
    fast-rerank 후보가 대상 장비에서 스킵되지 않고 그대로 측정된다)."""
    if keys is None:
        return set()
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    special = set(GROUPS) | {"all", "all+", "everything", "quick", "faceoff",
                             "main", "survivors", "gpu-models"}
    out = {k for k in keys if k not in special and k in _BY_KEY}
    if "quick" in keys:
        out |= {k for k in QUICK_KEYS if k in _BY_KEY}
    if "faceoff" in keys:
        out |= {k for k in FACEOFF_KEYS if k in _BY_KEY}
    if "main" in keys or "survivors" in keys:
        out |= {k for k in MAIN_KEYS if k in _BY_KEY}
    if "gpu-models" in keys:
        out |= {k for k in GPU_MODEL_KEYS if k in _BY_KEY}
    return out


def select(keys=None) -> List[Recipe]:
    """레시피 부분집합 선택.

    - ``None`` / ``"all"`` → 핵심 13가지(``REGISTRY``).
    - ``"quick"`` → '빠른' 프리셋(``QUICK_KEYS``) — 3배 판단에 필요한 핵심 소수.
    - ``"all+"`` / ``"everything"`` → 확장 포함 전부(``ALL_EXTENDED``).
    - 그룹명(``"npu-sweep"`` / ``"npu-only"`` / ``"fast-rerank"`` / ``"model-zoo"``
      / ``"core"``) → 그 그룹.  여러 그룹/키를 콤마로 섞을 수 있다.
    - 그 외 → 개별 레시피 키.
    """
    if keys is None or keys == "all" or keys == ["all"]:
        return list(REGISTRY)
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    if list(keys) in (["all+"], ["everything"]):
        return list(ALL_EXTENDED)
    out: List[Recipe] = []
    seen: Set[str] = set()
    for k in keys:
        if k in ("all+", "everything"):
            picked = ALL_EXTENDED
        elif k == "quick":
            picked = quick_recipes()
        elif k in ("main", "survivors"):
            picked = main_recipes()
        elif k == "faceoff":
            picked = [by_key(x) for x in FACEOFF_KEYS if x in _BY_KEY]
        elif k == "gpu-models":          # 모델 비교(MobileNetV3 + ResNet18)
            picked = [by_key(x) for x in GPU_MODEL_KEYS if x in _BY_KEY]
        elif k in GROUPS:
            picked = GROUPS[k]
        else:
            picked = [by_key(k)]
        for r in picked:
            if r.key not in seen:
                seen.add(r.key)
                out.append(r)
    return out
