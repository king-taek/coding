"""hnswlib 기반 근사 최근접(ANN) 인덱스 래퍼 — 고속 모드의 핵심.

대용량(예: 슬롯당 val 1 만 장 이상)에서 ref 마다 전 val 과 정밀 비교하는
O(N×M) 을 피하기 위해, CNN 임베딩을 한 번 추출해 인덱스를 만들고 ref 임베딩
으로 top-K 후보만 추린 뒤 기존 ``pipeline.score()`` 로 재정렬한다.

- hnswlib 는 **옵션 의존성** (pure-pip).  ``is_available()`` 로 가용 여부 확인.
- cosine space — embedder 의 L2 정규화 임베딩과 일치.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


def is_available() -> bool:
    """hnswlib import 가능 여부 (없으면 고속 모드 → 기본 폴백)."""
    try:
        import hnswlib  # noqa: F401
        return True
    except Exception:
        return False


class EmbeddingIndex:
    """L2 정규화된 임베딩에 대한 cosine ANN 인덱스.

    내부적으로 정수 라벨(0..N-1)을 사용하며, 호출자는 라벨↔경로 매핑을 따로
    관리한다 (``build_from`` 헬퍼 참고).
    """

    def __init__(self, dim: int, space: str = "cosine") -> None:
        import hnswlib
        self._dim = int(dim)
        self._index = hnswlib.Index(space=space, dim=self._dim)
        self._initialized = False
        self._count = 0

    def init(self, max_elements: int, *, ef_construction: int = 200,
             m: int = 16) -> None:
        self._index.init_index(
            max_elements=max(1, int(max_elements)),
            ef_construction=ef_construction, M=m,
        )
        self._initialized = True

    def add(self, ids: List[int], vecs: np.ndarray) -> None:
        if not self._initialized:
            self.init(len(ids))
        arr = np.ascontiguousarray(vecs, dtype=np.float32)
        self._index.add_items(arr, np.asarray(ids, dtype=np.int64))
        self._count += len(ids)

    def query(self, vec: np.ndarray, k: int) -> List[Tuple[int, float]]:
        """top-k (label, cosine_similarity) 내림차순 반환.

        hnswlib cosine space 의 distance = 1 - cosine_sim → sim = 1 - dist.
        """
        if self._count == 0:
            return []
        k = max(1, min(int(k), self._count))
        self._index.set_ef(max(k + 16, 50))
        q = np.ascontiguousarray(vec.reshape(1, -1), dtype=np.float32)
        labels, dists = self._index.knn_query(q, k=k)
        out: List[Tuple[int, float]] = []
        for lab, dist in zip(labels[0].tolist(), dists[0].tolist()):
            out.append((int(lab), float(1.0 - dist)))
        return out

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._index.save_index(str(path))

    @classmethod
    def load(cls, path: Path, dim: int, max_elements: int,
             space: str = "cosine") -> "EmbeddingIndex":
        obj = cls(dim, space=space)
        obj._index.load_index(str(path), max_elements=max(1, int(max_elements)))
        obj._initialized = True
        obj._count = max_elements
        return obj


def build_from(embeddings: dict) -> Optional[Tuple["EmbeddingIndex", list]]:
    """{path: vec} → (인덱스, 라벨순 경로 리스트).  벡터 없으면 None.

    라벨 i 는 반환된 경로 리스트의 i 번째에 대응한다.
    """
    if not is_available() or not embeddings:
        return None
    paths = [p for p, v in embeddings.items() if v is not None and v.size > 0]
    if not paths:
        return None
    dim = int(embeddings[paths[0]].shape[-1])
    mat = np.stack([embeddings[p].astype(np.float32) for p in paths])
    idx = EmbeddingIndex(dim)
    idx.init(len(paths))
    idx.add(list(range(len(paths))), mat)
    return idx, paths
