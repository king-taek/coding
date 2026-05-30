"""모델 주머니(model zoo) — 임베딩/매칭 백본의 메타데이터·가용성·빌더 훅.

개발자 벤치마크가 다양한 모델(MobileNetV3·ResNet 계열뿐 아니라 SuperPoint·
LightGlue·PatchCore·PaDiM·CAE·U-Net·MobileViT 등)을 실험할 수 있도록, 각 모델의

  - **family**     : 연산 방식(임베딩 CNN / ViT / 오토인코더 / 키포인트 / 이상탐지)
  - **desc**       : '어느 장치에서 어떻게' 계산하는지(사람이 읽는 설명)
  - **needs**      : 대상 장비에 필요한 추가 패키지/가중치(비면 torch/torchvision 만으로 동작)
  - **build_backbone** : OpenVINO 로 컴파일 가능한 임베딩 백본을 만드는 훅

을 한곳에 모은다.  torch/torchvision/timm/kornia/anomalib 이 **없어도 이 모듈을
import** 할 수 있어야 하므로(헤드리스 테스트), 무거운 의존성은 전부 함수 안에서
지연 import 하고 실패하면 가용성 False + 사유 문자열로 보고한다(절대 예외 전파 안 함).

정확도는 백본 종류가 아니라 CPU 고전 융합이 좌우한다는 결론(NPU 효율성 보고서)은
유효하다.  따라서 이 모델들은 주로 *'NPU 로 데이터를 더 잘/빨리 뽑는'* 후보 선별
단계의 변형이며, 키포인트/이상탐지 계열은 별도 채점기로 라우팅된다(미설치 시 폴백).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# 연산 계열(family)
FAMILY_CNN = "cnn"              # torchvision CNN → 임베딩(OpenVINO 컴파일)
FAMILY_VIT = "vit"             # MobileViT(timm) → 임베딩
FAMILY_AE = "autoencoder"       # CAE / U-Net 인코더 병목 → 임베딩(가중치 필요)
FAMILY_KEYPOINT = "keypoint"    # SuperPoint + LightGlue → 매칭 점수(키포인트 정합)
FAMILY_ANOMALY = "anomaly"      # PatchCore / PaDiM → 패치 특징 거리

# 임베딩 컴파일이 가능한 계열(나머지는 전용 채점기로 라우팅).
_EMBED_FAMILIES = {FAMILY_CNN, FAMILY_VIT, FAMILY_AE}


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    family: str
    desc: str
    needs: str = ""            # 대상 장비에 필요한 추가 패키지/가중치(비면 기본 의존성으로 동작)
    embed_dim: int = 0         # 문서용(인덱스는 차원 무관)


# ---------------------------------------------------------------------------
# 레지스트리 — model_id → ModelSpec
# ---------------------------------------------------------------------------
REGISTRY: Dict[str, ModelSpec] = {
    # CNN 임베딩(torchvision — torch/torchvision 만으로 NPU/GPU 컴파일 가능) -----
    "mobilenet_v3_small": ModelSpec(
        "mobilenet_v3_small", FAMILY_CNN,
        "MobileNetV3-Small 백본으로 임베딩 추출(경량·기본). NPU/GPU 컴파일.", embed_dim=576),
    "mobilenet_v3_large": ModelSpec(
        "mobilenet_v3_large", FAMILY_CNN,
        "MobileNetV3-Large — 조금 더 큰 용량의 임베딩(정확도 여유↑, 추출비용↑).", embed_dim=960),
    "resnet18": ModelSpec(
        "resnet18", FAMILY_CNN,
        "ResNet18 백본 임베딩(중간 용량). NPU 대조 모델.", embed_dim=512),
    "resnet50": ModelSpec(
        "resnet50", FAMILY_CNN,
        "ResNet50 — 더 깊은 임베딩(표현력↑, NPU 추출비용↑).", embed_dim=2048),
    "mobilenet_v2": ModelSpec(
        "mobilenet_v2", FAMILY_CNN,
        "MobileNetV2 임베딩(경량 대조군).", embed_dim=1280),
    "squeezenet1_1": ModelSpec(
        "squeezenet1_1", FAMILY_CNN,
        "SqueezeNet1.1 — 초경량(최소 추출비용, 정확도 대조).", embed_dim=512),
    "efficientnet_b0": ModelSpec(
        "efficientnet_b0", FAMILY_CNN,
        "EfficientNet-B0 — 효율형 임베딩(정확도/비용 균형).", embed_dim=1280),
    "shufflenet_v2_x1_0": ModelSpec(
        "shufflenet_v2_x1_0", FAMILY_CNN,
        "ShuffleNetV2 — 모바일 경량 임베딩(NPU 친화 대조).", embed_dim=1024),
    # ViT 임베딩(timm) ------------------------------------------------------
    "mobilevit_s": ModelSpec(
        "mobilevit_s", FAMILY_VIT,
        "MobileViT-S — CNN+트랜스포머 혼합 백본 임베딩. NPU 에서 ViT 계열 성능 확인.",
        needs="timm", embed_dim=640),
    "mobilevit_xs": ModelSpec(
        "mobilevit_xs", FAMILY_VIT,
        "MobileViT-XS — 더 가벼운 MobileViT(추출비용↓).", needs="timm", embed_dim=384),
    # 오토인코더/세그멘테이션 인코더(가중치 필요) ---------------------------
    "cae": ModelSpec(
        "cae", FAMILY_AE,
        "합성곱 오토인코더(CAE)의 병목 벡터를 임베딩으로 사용. 결함 재구성 특징에 민감.",
        needs="학습 가중치(미학습 시 임의 초기화 — 정확도 무의미)", embed_dim=256),
    "unet": ModelSpec(
        "unet", FAMILY_AE,
        "U-Net 인코더 병목을 임베딩으로 사용(세그멘테이션 백본). 국소 결함 표현에 유리.",
        needs="학습 가중치", embed_dim=512),
    "attention_unet": ModelSpec(
        "attention_unet", FAMILY_AE,
        "Attention U-Net — 어텐션 게이트로 결함 영역에 집중한 인코더 병목 임베딩.",
        needs="학습 가중치", embed_dim=512),
    # 키포인트 정합(SuperPoint + LightGlue) ---------------------------------
    "superpoint_lightglue": ModelSpec(
        "superpoint_lightglue", FAMILY_KEYPOINT,
        "SuperPoint 로 키포인트/디스크립터를 NPU 추출 → LightGlue 로 정합, 정합 수를 "
        "유사도로. 회로 패턴의 국소 대응에 강함.",
        needs="kornia(LightGlue/SuperPoint)"),
    # 이상탐지 패치 특징(PatchCore / PaDiM) ---------------------------------
    "patchcore": ModelSpec(
        "patchcore", FAMILY_ANOMALY,
        "PatchCore — 기준 패치 특징 메모리뱅크와의 최근접 거리로 매칭/이상 점수.",
        needs="anomalib"),
    "padim": ModelSpec(
        "padim", FAMILY_ANOMALY,
        "PaDiM — 패치 위치별 가우시안 분포의 마할라노비스 거리로 매칭/이상 점수.",
        needs="anomalib"),
}


# torchvision 생성자명 매핑(가중치 enum 은 IMAGENET1K_V1 우선 시도).
_TORCHVISION_CTORS = {
    "mobilenet_v3_small": "mobilenet_v3_small",
    "mobilenet_v3_large": "mobilenet_v3_large",
    "resnet18": "resnet18",
    "resnet50": "resnet50",
    "mobilenet_v2": "mobilenet_v2",
    "squeezenet1_1": "squeezenet1_1",
    "efficientnet_b0": "efficientnet_b0",
    "shufflenet_v2_x1_0": "shufflenet_v2_x1_0",
}


def spec(model_id: str) -> Optional[ModelSpec]:
    return REGISTRY.get(str(model_id))


def is_embedding_model(model_id: str) -> bool:
    s = spec(model_id)
    return bool(s and s.family in _EMBED_FAMILIES)


def family(model_id: str) -> str:
    s = spec(model_id)
    return s.family if s else FAMILY_CNN


def needs(model_id: str) -> str:
    s = spec(model_id)
    return s.needs if s else ""


def availability(model_id: str) -> Tuple[bool, str]:
    """``(가용여부, 사유)`` — 대상 장비에서 이 모델로 실제 추론이 되는지 탐지.

    torch/torchvision/timm/kornia/anomalib 존재를 지연 import 로 확인한다.
    헤드리스(의존성 없음)에서는 False + 사유를 돌려주고, 벤치마크는 폴백한다."""
    s = spec(model_id)
    if s is None:
        return False, f"알 수 없는 모델: {model_id}"
    fam = s.family
    if fam == FAMILY_CNN:
        try:
            import torch  # noqa: F401
            import torchvision  # noqa: F401
        except Exception:
            return False, "torch/torchvision 미설치"
        if model_id not in _TORCHVISION_CTORS:
            return False, "torchvision 생성자 없음"
        return True, ""
    if fam == FAMILY_VIT:
        try:
            import torch  # noqa: F401
            import timm  # noqa: F401
        except Exception:
            return False, "timm 미설치(pip install timm)"
        return True, ""
    if fam == FAMILY_AE:
        try:
            import torch  # noqa: F401
        except Exception:
            return False, "torch 미설치"
        # 구조는 만들 수 있으나 학습 가중치가 없으면 임베딩이 무의미.
        return True, "임의 초기화(학습 가중치 필요 — 정확도 측정엔 부적합)"
    if fam == FAMILY_KEYPOINT:
        try:
            import kornia  # noqa: F401
            from kornia.feature import LightGlue  # noqa: F401
        except Exception:
            return False, "kornia(LightGlue) 미설치(pip install kornia)"
        return True, ""
    if fam == FAMILY_ANOMALY:
        try:
            import anomalib  # noqa: F401
        except Exception:
            return False, "anomalib 미설치(pip install anomalib)"
        return True, ""
    return False, "지원하지 않는 family"


# ---------------------------------------------------------------------------
# OpenVINO 컴파일용 백본 빌더 — 임베딩 계열(CNN/ViT/AE)만.
# embedder_openvino._build_ov_model 이 호출한다.  torch 없으면 None.
# ---------------------------------------------------------------------------
def build_backbone(model_id: str, *, input_px: int = 256):
    """``model_id`` 의 임베딩 백본(``torch.nn.Module``, eval) 또는 None.

    분류 헤드를 제거해 raw 임베딩만 내도록 한다.  CNN=torchvision, ViT=timm,
    AE=내장 경량 구조.  실패하면 None(호출부가 폴백)."""
    try:
        import torch
        import torch.nn as nn
    except Exception:
        return None
    fam = family(model_id)
    try:
        if fam == FAMILY_CNN:
            return _build_torchvision(model_id)
        if fam == FAMILY_VIT:
            import timm
            m = timm.create_model(model_id, pretrained=True, num_classes=0)
            m.eval()
            return m
        if fam == FAMILY_AE:
            m = _build_autoencoder_encoder(model_id, input_px)
            if m is not None:
                m.eval()
            return m
    except Exception:
        return None
    return None


def _build_torchvision(model_id: str):
    import torch.nn as nn
    import torchvision.models as M
    ctor = getattr(M, _TORCHVISION_CTORS[model_id])
    try:
        net = ctor(weights="IMAGENET1K_V1")
    except Exception:
        net = ctor(weights=None)
    # 분류 헤드 → Identity (raw 임베딩).
    for attr in ("classifier", "fc"):
        if hasattr(net, attr):
            setattr(net, attr, nn.Identity())
    net.eval()
    return net


def _build_autoencoder_encoder(model_id: str, input_px: int):
    """CAE / U-Net / Attention U-Net 의 **인코더 병목**을 임베딩으로 내는 경량 모듈.

    학습 가중치가 없으면 임의 초기화라 정확도는 무의미하지만, NPU 추출/처리량
    측정과 파이프라인 연결을 검증할 수 있다(가중치 로드는 대상 장비의 몫)."""
    import torch
    import torch.nn as nn

    class _ConvEncoder(nn.Module):
        def __init__(self, depth: int = 4):
            super().__init__()
            chans = [3, 32, 64, 128, 256][: depth + 1]
            blocks = []
            for i in range(depth):
                blocks += [nn.Conv2d(chans[i], chans[i + 1], 3, 2, 1),
                           nn.BatchNorm2d(chans[i + 1]), nn.ReLU(inplace=True)]
            self.enc = nn.Sequential(*blocks)
            self.pool = nn.AdaptiveAvgPool2d(1)

        def forward(self, x):
            return torch.flatten(self.pool(self.enc(x)), 1)

    # cae=4단계, unet/attention_unet=5단계(더 깊은 인코더). 어텐션은 구조 단순화.
    depth = 5 if model_id in ("unet", "attention_unet") else 4
    return _ConvEncoder(depth=depth)
