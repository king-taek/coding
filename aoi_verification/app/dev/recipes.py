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
    embed_model: str = ""          # MODEL_* (recall 이 임베딩일 때)
    embed_batch: int = 1           # 정적 배치 B (GPU 는 16 권장)
    fusion_topk: int = 40          # 고전 재채점 깊이(fusion 일 때)
    center_crop: bool = False      # 고전 재채점 시 중앙 30% crop
    concurrency: int = 32          # 동시 in-flight 추론 상한
    ensemble: bool = False         # gpu+npu 를 '분담' 대신 '앙상블'로(대조군)
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
            top_k=int(self.fusion_topk),
            persist_scores=False,            # 벤치마크는 점수 영속 캐시도 끔
            accel_concurrency=int(self.concurrency),
            use_cpu=True,
            use_gpu=self.recall in (RECALL_GPU, RECALL_GPU_NPU),
            use_npu=self.recall in (RECALL_NPU, RECALL_GPU_NPU),
            embed_batch=int(self.embed_batch),
            bench_no_cache=bool(bench_no_cache),
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
        embed_model=MODEL_MOBILENET_V3, embed_batch=16,
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
        ensemble=True,
        desc=("GPU 와 NPU 가 '각각 전체'를 임베딩해 두 코사인을 평균(앙상블). "
              "보고서상 정확도 이득 0 · 시간 약 2배인 안티패턴 — 분담과 대조."),
    ),
]

# 추천/대조의 기준이 되는 레시피 키.
BASELINE_ACCURACY_KEY = "cpu_classical_full"   # 정확도의 정답 기준선
PRODUCTION_SPEED_KEY = "gpu_fusion_b16"        # 현행(속도 3배 목표의 분모)


def by_key(key: str) -> Recipe:
    for r in REGISTRY:
        if r.key == key:
            return r
    raise KeyError(key)


def all_keys() -> List[str]:
    return [r.key for r in REGISTRY]


def select(keys=None) -> List[Recipe]:
    """``keys`` (리스트/콤마문자열/None=전체) 로 레시피 부분집합을 고른다."""
    if keys is None or keys == "all" or keys == ["all"]:
        return list(REGISTRY)
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    want = list(keys)
    return [by_key(k) for k in want]
