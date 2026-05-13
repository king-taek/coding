"""추론 wrapper — 활성화된 모델(또는 basic 모드)에 따라 임베딩을 만든다.

- ``get_active_mode()`` 가 ``"basic"`` 이면 임베딩 자체를 만들지 않음
  (pipeline.score() 가 CNN 항 가중치를 0 으로 무력화함).
- 그 외 (모델 이름) 이면 MobileNetV3-Small 백본 → 해당 head.pt → L2 정규화.
- 모든 모델 로딩은 ``@lru_cache`` 로 캐싱.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from . import registry, triplet_model

try:  # pragma: no cover — optional
    import torch
    from torchvision import models, transforms
    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore
    models = None  # type: ignore
    transforms = None  # type: ignore
    _HAS_TORCH = False


def is_available() -> bool:
    return _HAS_TORCH and triplet_model.is_available()


# ---------------------------------------------------------------------------
# Active mode
# ---------------------------------------------------------------------------
def get_active_mode() -> str:
    """``"basic"`` 이거나 모델 이름. registry 에서 active 를 읽음."""
    return registry.get_active()


def set_active_mode(name: str) -> None:
    registry.set_active(name)
    # 백본/헤드 캐시는 그대로, 헤드 캐시만 무효화 (모델 바뀐 경우 대비)
    _load_head_for.cache_clear()


# ---------------------------------------------------------------------------
# Backbone + transforms (lru_cache 싱글톤)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_backbone() -> Tuple[object, object]:  # pragma: no cover — heavy
    if not _HAS_TORCH:
        raise RuntimeError("torch 가 설치되어 있지 않습니다")
    weights = models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
    backbone = models.mobilenet_v3_small(weights=weights)
    # avgpool 까지 통과시켜 1280-d feature 를 얻고 classifier 제거
    backbone.classifier = torch.nn.Identity()
    backbone.eval()
    return backbone, weights.transforms()


@lru_cache(maxsize=8)
def _load_head_for(model_name: str):  # pragma: no cover — heavy
    if model_name == registry.BASIC:
        return None
    info = registry.find(model_name)
    if info is None or not info.weights_path.exists():
        return None
    try:
        head = triplet_model.load_head(info.weights_path)
        head.eval()
        return head
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Embedding 계산
# ---------------------------------------------------------------------------
def compute_embedding(src: Path) -> Optional[np.ndarray]:
    """현재 활성 모델로 한 이미지의 임베딩(unit 정규화 1-D 벡터)을 만든다.

    - basic 모드 또는 torch 미설치 → ``None`` 반환 (pipeline 이 CNN 항을 0 처리).
    - 학습된 head 가 활성 → 백본 → head → L2 정규화 (128-d).
    - head 가 없지만 모델 이름이 활성 → 백본 1280-d 출력을 그대로 정규화.
    """
    if not is_available():
        return None

    mode = get_active_mode()
    if mode == registry.BASIC:
        return None

    from PIL import Image
    try:
        img = Image.open(str(src)).convert("RGB")
    except Exception:
        return None

    backbone, tfm = _load_backbone()
    head = _load_head_for(mode)

    with torch.no_grad():
        x = tfm(img).unsqueeze(0)
        feat = backbone(x)                       # (1, 1280)
        if head is not None:
            feat = head(feat)                    # (1, 128)
        feat = feat.cpu().numpy().flatten()
    n = np.linalg.norm(feat) + 1e-9
    return (feat / n).astype(np.float32)


def cosine_similarity(a: Optional[np.ndarray],
                      b: Optional[np.ndarray]) -> float:
    """두 unit-정규화 벡터의 cosine 유사도를 0~1 로 매핑."""
    if a is None or b is None or a.size == 0 or b.size == 0:
        return 0.0
    if a.shape != b.shape:
        return 0.0
    dot = float(np.dot(a, b))
    return max(0.0, min(1.0, (dot + 1.0) / 2.0))


def invalidate_caches() -> None:
    """모델 파일이 새로 학습/리네임된 후 호출."""
    _load_head_for.cache_clear()
