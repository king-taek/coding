"""추론 wrapper — 활성화된 모델(또는 basic 모드)에 따라 임베딩을 만든다.

도메인 특화 전처리 적용 (#9):
- 중심 ROI + CLAHE + 가벼운 Gaussian blur (image_io.preprocessed_roi_gray)
- 1-채널 gray 를 3-채널로 복제하여 ImageNet 통계로 정규화
- 입력 해상도 256 (백본 224 보다 약간 키워 디테일 보존)

배치 추론(#12):
- ``compute_embeddings([paths])`` 가 32 씩 배치로 forward 해서 CPU 속도 5~10×.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Tuple

import numpy as np

from ..utils import image_io
from . import registry, triplet_model

try:  # pragma: no cover — optional
    import torch
    from torchvision import models
    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore
    models = None  # type: ignore
    _HAS_TORCH = False


_INPUT_PX = 256
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_DEFAULT_BATCH = 32
# MobileNetV3-Small 의 features 출력 채널 수 — classifier=Identity 이후 backbone(x) 의 dim.
BACKBONE_OUT_DIM = 576


def is_available() -> bool:
    return _HAS_TORCH and triplet_model.is_available()


# ---------------------------------------------------------------------------
# Active mode
# ---------------------------------------------------------------------------
def get_active_mode() -> str:
    return registry.get_active()


def set_active_mode(name: str) -> None:
    registry.set_active(name)
    _load_head_for.cache_clear()


# ---------------------------------------------------------------------------
# Backbone (lru_cache singleton)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_backbone():  # pragma: no cover — heavy
    if not _HAS_TORCH:
        raise RuntimeError("torch 가 설치되어 있지 않습니다")
    weights = models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
    backbone = models.mobilenet_v3_small(weights=weights)
    backbone.classifier = torch.nn.Identity()
    backbone.eval()
    return backbone


def load_backbone():  # pragma: no cover — heavy
    """학습 워커가 backbone 인스턴스를 공유하기 위한 공개 진입점."""
    return _load_backbone()


@lru_cache(maxsize=8)
def _load_head_for(model_name: str):  # pragma: no cover — heavy
    if model_name == registry.BASIC:
        return None
    info = registry.find(model_name)
    if info is None or not info.weights_path.exists():
        return None
    try:
        head = triplet_model.load_head(info.weights_path)
    except Exception:
        return None
    # 백본 출력 차원과 head 의 입력 차원이 다르면 안전하게 무시 (basic 으로 fallback).
    # 과거 1280 으로 잘못 저장된 .pt 가 있어도 추론을 깨뜨리지 않게 방어.
    try:
        in_dim = int(head.dims[0])
    except Exception:
        in_dim = -1
    if in_dim != BACKBONE_OUT_DIM:
        return None
    head.eval()
    return head


# ---------------------------------------------------------------------------
# Tensor preparation — domain-preprocessed gray-3ch input
# ---------------------------------------------------------------------------
def _make_input_tensor(path: Path):  # pragma: no cover
    """1장의 도메인 전처리 텐서를 만든다 (3, _INPUT_PX, _INPUT_PX) float32.

    원본 ROI 의 aspect ratio 가 제각각이라 그대로 두면 ``torch.stack`` 시
    크기 불일치 에러 발생.  중앙 zero-pad 로 정사각형 강제.
    """
    if not _HAS_TORCH:
        return None
    try:
        gray = image_io.preprocessed_roi_gray(path, long_edge=_INPUT_PX)
    except Exception:
        return None
    h, w = gray.shape
    # 정사각형 zero-padded 캔버스에 중앙 배치 → 모든 텐서가 (3, _INPUT_PX, _INPUT_PX).
    canvas = np.zeros((_INPUT_PX, _INPUT_PX), dtype=np.uint8)
    y0 = max(0, (_INPUT_PX - h) // 2)
    x0 = max(0, (_INPUT_PX - w) // 2)
    h_use = min(h, _INPUT_PX)
    w_use = min(w, _INPUT_PX)
    canvas[y0:y0 + h_use, x0:x0 + w_use] = gray[:h_use, :w_use]
    arr = np.repeat(canvas[None, :, :], 3, axis=0).astype(np.float32) / 255.0
    for c, (mean, std) in enumerate(zip(_IMAGENET_MEAN, _IMAGENET_STD)):
        arr[c] = (arr[c] - mean) / std
    return torch.from_numpy(arr)


# ---------------------------------------------------------------------------
# Public — single image
# ---------------------------------------------------------------------------
def compute_embedding(src: Path) -> Optional[np.ndarray]:
    """현재 활성 모델로 한 이미지의 임베딩(unit 정규화 1-D 벡터)을 만든다."""
    if not is_available():
        return None
    mode = get_active_mode()
    if mode == registry.BASIC:
        return None
    out = compute_embeddings([src])
    return out.get(Path(src))


def compute_embeddings(paths: Iterable[Path],
                       *,
                       batch_size: int = _DEFAULT_BATCH
                       ) -> dict[Path, np.ndarray]:
    """여러 이미지의 임베딩을 배치로 계산. basic 모드면 빈 dict."""
    out: dict[Path, np.ndarray] = {}
    if not is_available():
        return out
    mode = get_active_mode()
    if mode == registry.BASIC:
        return out

    backbone = _load_backbone()
    head = _load_head_for(mode)

    items = [Path(p) for p in paths]
    if not items:
        return out

    pending: list[Tuple[Path, "torch.Tensor"]] = []

    def _flush_batch() -> None:
        if not pending:
            return
        keys = [p for p, _ in pending]
        tensors = [t for _, t in pending]
        x = torch.stack(tensors)
        with torch.no_grad():
            feat = backbone(x)
            if head is not None:
                feat = head(feat)
            feat = feat.cpu().numpy()
        # L2 정규화
        norms = np.linalg.norm(feat, axis=1, keepdims=True) + 1e-9
        feat = (feat / norms).astype(np.float32)
        for k, v in zip(keys, feat):
            out[k] = v
        pending.clear()

    for p in items:
        t = _make_input_tensor(p)
        if t is None:
            continue
        pending.append((p, t))
        if len(pending) >= batch_size:
            _flush_batch()
    _flush_batch()
    return out


def cosine_similarity(a: Optional[np.ndarray],
                      b: Optional[np.ndarray]) -> float:
    if a is None or b is None or a.size == 0 or b.size == 0:
        return 0.0
    if a.shape != b.shape:
        return 0.0
    dot = float(np.dot(a, b))
    return max(0.0, min(1.0, (dot + 1.0) / 2.0))


def invalidate_caches() -> None:
    """모델 파일이 새로 학습/리네임된 후 호출."""
    _load_head_for.cache_clear()


def make_input_tensor(path: Path):  # pragma: no cover — 외부 노출(트레이너용)
    return _make_input_tensor(path)
