"""호환용 wrapper — 실제 구현은 ``aoi_verification.app.learning.embedder`` 가 담당.

- ``compute_embedding(path)`` → 활성 모델이 ``basic`` 이면 ``None`` 반환.
- ``cosine_similarity`` 는 그대로 위임.
- torch 미설치 시 graceful degrade (모든 함수가 0/None 반환).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from ..learning import embedder as _emb


def is_available() -> bool:
    return _emb.is_available()


def compute_embedding(src: Path) -> Optional[np.ndarray]:
    return _emb.compute_embedding(Path(src))


def cosine_similarity(a: Optional[np.ndarray],
                      b: Optional[np.ndarray]) -> float:
    return _emb.cosine_similarity(a, b)
