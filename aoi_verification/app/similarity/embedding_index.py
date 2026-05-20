"""근사/정확 최근접(ANN) 인덱스 — 고속 모드의 후보 검색.

대용량에서 ref 마다 전 val 과 정밀 비교하는 O(N×M) SSIM 을 피하려고, CNN
임베딩으로 top-K 후보만 추린 뒤 기존 ``pipeline.score()`` 로 재정렬한다.

검색 백엔드는 두 가지:
- ``hnswlib`` 가 설치돼 있으면 그것을 사용 (수십만 벡터 이상에서 sub-linear).
- 없으면 **NumPy 브루트포스 cosine**(폴백).  슬롯당 수천~수만 벡터 규모에서는
  단일 행렬곱이라 정확하면서도 충분히 빠르다 — hnswlib 설치가 불가능한
  (컴파일러/네트워크 제약) 환경에서도 고속 모드가 그대로 동작한다.

임베딩은 embedder 가 L2 정규화해 주지만, 폴백 인덱스는 안전하게 한 번 더
정규화한다 (cosine = 내적).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


def hnswlib_available() -> bool:
    try:
        import hnswlib  # noqa: F401
        return True
    except Exception:
        return False


def is_available() -> bool:
    """ANN 검색 가용 여부.  NumPy 브루트포스 폴백이 있어 항상 True
    (NumPy 는 핵심 의존성).  hnswlib 는 대용량 가속용 옵션일 뿐이다."""
    return True


def _normalize(mat: np.ndarray) -> np.ndarray:
    arr = np.ascontiguousarray(mat, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
    return arr / norms


# ---------------------------------------------------------------------------
# hnswlib 백엔드 (옵션)
# ---------------------------------------------------------------------------
class EmbeddingIndex:
    """L2 정규화된 임베딩에 대한 cosine ANN 인덱스 (hnswlib)."""

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


# ---------------------------------------------------------------------------
# NumPy 브루트포스 백엔드 (폴백 — hnswlib 불가 환경)
# ---------------------------------------------------------------------------
class BruteForceIndex:
    """NumPy cosine 브루트포스 검색.

    슬롯당 수천~수만 벡터에서 단일 행렬곱(q·Mᵀ)으로 정확한 top-K 를 구한다.
    hnswlib 없이도 고속 모드가 동작하도록 하는 폴백이며, 이 앱 규모에서는
    검색 비용이 임베딩 추출 비용에 비해 무시할 수준이다.
    """

    def __init__(self, dim: int, space: str = "cosine") -> None:
        self._dim = int(dim)
        self._mat: Optional[np.ndarray] = None     # (N, D) L2-정규화
        self._count = 0

    def init(self, max_elements: int, **_kw) -> None:    # API 호환용 no-op
        pass

    def add(self, ids: List[int], vecs: np.ndarray) -> None:
        # build_from 이 라벨 0..N-1 을 순서대로 주므로 행 순서가 곧 라벨.
        self._mat = _normalize(vecs)
        self._count = self._mat.shape[0]

    def query(self, vec: np.ndarray, k: int) -> List[Tuple[int, float]]:
        if self._count == 0 or self._mat is None:
            return []
        k = max(1, min(int(k), self._count))
        q = _normalize(vec)[0]                          # (D,)
        sims = self._mat @ q                            # (N,) cosine
        if k < self._count:
            cand = np.argpartition(-sims, k - 1)[:k]
        else:
            cand = np.arange(self._count)
        cand = cand[np.argsort(-sims[cand])]            # 점수 내림차순
        return [(int(i), float(sims[i])) for i in cand]

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.save(str(path), self._mat if self._mat is not None
                else np.zeros((0, self._dim), dtype=np.float32))

    @classmethod
    def load(cls, path: Path, dim: int, max_elements: int,
             space: str = "cosine") -> "BruteForceIndex":
        obj = cls(dim, space=space)
        mat = np.load(str(path))
        obj._mat = np.ascontiguousarray(mat, dtype=np.float32)
        obj._count = obj._mat.shape[0]
        return obj


def build_from(embeddings: dict) -> Optional[Tuple[object, list]]:
    """{path: vec} → (인덱스, 라벨순 경로 리스트).  벡터 없으면 None.

    hnswlib 가 있으면 그 인덱스를, 없으면 NumPy 브루트포스 인덱스를 만든다.
    라벨 i 는 반환된 경로 리스트의 i 번째에 대응한다.
    """
    if not embeddings:
        return None
    paths = [p for p, v in embeddings.items() if v is not None and v.size > 0]
    if not paths:
        return None
    dim = int(embeddings[paths[0]].shape[-1])
    mat = np.stack([embeddings[p].astype(np.float32) for p in paths])
    if hnswlib_available():
        idx: object = EmbeddingIndex(dim)
        idx.init(len(paths))
    else:
        idx = BruteForceIndex(dim)
    idx.add(list(range(len(paths))), mat)
    return idx, paths
