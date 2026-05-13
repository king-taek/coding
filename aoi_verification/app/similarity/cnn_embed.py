"""(선택) 사전학습 CNN 임베딩 — torchvision MobileNetV3.

설치되어 있지 않으면 자동으로 비활성화된다. CONFIG.similarity.use_cnn 가
True 이고 torch 가 import 가능할 때만 실제로 호출된다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np

try:  # pragma: no cover — optional
    import torch
    from torch import nn
    from torchvision import models, transforms
    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore
    nn = None  # type: ignore
    models = None  # type: ignore
    transforms = None  # type: ignore
    _HAS_TORCH = False


def is_available() -> bool:
    return _HAS_TORCH


@lru_cache(maxsize=1)
def _load_backbone():  # pragma: no cover — heavy
    weights = models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
    m = models.mobilenet_v3_small(weights=weights)
    m.classifier = nn.Identity()
    m.eval()
    return m, weights.transforms()


def compute_embedding(src: Path) -> Optional[np.ndarray]:  # pragma: no cover
    if not _HAS_TORCH:
        return None
    from PIL import Image
    img = Image.open(str(src)).convert("RGB")
    model, tfm = _load_backbone()
    with torch.no_grad():
        x = tfm(img).unsqueeze(0)
        feat = model(x).cpu().numpy().flatten()
    n = np.linalg.norm(feat) + 1e-9
    return (feat / n).astype(np.float32)


def cosine_similarity(a: Optional[np.ndarray],
                      b: Optional[np.ndarray]) -> float:
    if a is None or b is None or a.size == 0 or b.size == 0:
        return 0.0
    dot = float(np.dot(a, b))
    # 둘 다 unit 정규화 되어있다고 가정
    return max(0.0, min(1.0, (dot + 1.0) / 2.0))
