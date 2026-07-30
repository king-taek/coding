"""고전 채점 경로의 CNN 임베딩 — 현재 **항상 비활성**.

활성화되려면 학습된 head(``.pt``)가 있고 ``registry`` 의 active 모델이 ``basic`` 이
아니어야 하는데, 학습 기능이 제거되면서 **``.pt`` 를 만들 수단도, ``set_active()`` 를
부르는 앱 코드도 없다**.  그래서 ``is_available()`` 은 항상 False 이고, 호출부는
phash/ORB/SSIM 만으로 채점한다 — 지금까지의 동작 그대로다.

※ 고효율 모드의 GPU 임베딩은 **여기가 아니다**.  그쪽은
  ``learning/embedder_openvino.py`` 가 동봉 IR + OpenVINO 로 따로 처리한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


def is_available() -> bool:
    return False


def compute_embedding(src: Path) -> Optional[np.ndarray]:
    return None


def cosine_similarity(a: Optional[np.ndarray],
                      b: Optional[np.ndarray]) -> float:
    """L2 정규화된 두 임베딩의 코사인 유사도를 [0,1] 로 사상.

    ``Feature.cnn`` 은 항상 정규화된 벡터라 내적이 곧 코사인이다(정규화 재계산 안 함).
    """
    if a is None or b is None or a.size == 0 or b.size == 0:
        return 0.0
    if a.shape != b.shape:
        return 0.0
    dot = float(np.dot(a, b))
    return max(0.0, min(1.0, (dot + 1.0) / 2.0))
