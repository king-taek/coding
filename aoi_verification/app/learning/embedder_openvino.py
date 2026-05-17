"""OpenVINO 기반 추론 가속 — Intel NPU / Intel GPU 자동 활용.

PyTorch native 디바이스 (torch.xpu) 가 Iris Xe / Arc GPU 까진 잡지만,
Intel AI Boost **NPU** (Meteor Lake+ 노트북 SoC) 는 PyTorch 가 직접
지원하지 않는다.  OpenVINO 가 NPU 플러그인을 통해 가속하므로, ``openvino``
패키지가 설치되어 있고 NPU/GPU 가 인식되면 이 모듈을 우선 사용한다.

설계:
- ``is_available()`` — openvino import + NPU/GPU 디바이스 존재 확인.
- ``_compile_backbone()`` — MobileNetV3-Small backbone 을 OpenVINO IR 로
  변환 + 선택된 디바이스 (NPU > GPU > 미사용) 에 컴파일 (lazy, 1 회).
- ``compute_embeddings(paths)`` — 배치 단위로 OpenVINO 컴파일 모델 추론
  → 결과를 PyTorch head (작은 Linear) 에 통과시켜 최종 임베딩.
- 디바이스 우선 순위 — NPU 가 있으면 NPU, 없으면 GPU, 둘 다 없으면
  ``is_available()`` 가 False 를 반환해 PyTorch 경로로 폴백.

설치: ``pip install openvino`` (대략 200MB).  Intel 노트북에선
NPU 플러그인 (``openvino-tokenizers`` 는 불필요) 이 함께 설치되어 NPU 가
``core.available_devices`` 에 노출된다.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Optional dependencies — torch + openvino 가 모두 있어야 의미 있음.
# ---------------------------------------------------------------------------
try:  # pragma: no cover — 옵션
    import torch
    from torchvision import models
    _HAS_TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore
    models = None  # type: ignore
    _HAS_TORCH = False

try:  # pragma: no cover — 옵션
    import openvino as ov
    _HAS_OPENVINO = True
except Exception:  # pragma: no cover
    ov = None  # type: ignore
    _HAS_OPENVINO = False


_INPUT_PX = 256
_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


# ---------------------------------------------------------------------------
# Device 감지
# ---------------------------------------------------------------------------
def _list_ov_devices() -> List[str]:  # pragma: no cover — 환경 의존
    if not _HAS_OPENVINO:
        return []
    try:
        return list(ov.Core().available_devices)
    except Exception:
        return []


def _pick_target() -> Optional[str]:  # pragma: no cover — 환경 의존
    """OpenVINO 디바이스 우선 순위: NPU > GPU.  CPU 는 PyTorch 와 차이가
    크지 않으므로 OpenVINO 경로를 강제 사용하지 않는다."""
    devs = _list_ov_devices()
    for cand in ("NPU", "GPU"):
        if any(d == cand or d.startswith(cand + ".") for d in devs):
            return cand
    return None


def is_available() -> bool:
    """OpenVINO + (NPU 또는 GPU) + torch 모두 있을 때만 사용 가능."""
    return _HAS_TORCH and _HAS_OPENVINO and _pick_target() is not None


def target_device() -> Optional[str]:
    return _pick_target() if is_available() else None


def device_label() -> str:  # pragma: no cover — 환경 의존
    """상태바 표시용 — 사용 가능할 때만 비어있지 않은 문자열 반환."""
    if not is_available():
        return ""
    t = target_device()
    if t == "NPU":
        return "NPU 가속 (Intel AI Boost — OpenVINO)"
    if t == "GPU":
        return "Intel GPU 가속 (OpenVINO)"
    return f"OpenVINO 가속 ({t})"


# ---------------------------------------------------------------------------
# Backbone 컴파일 (lazy)
# ---------------------------------------------------------------------------
# 주의: OpenVINO NPU 플러그인은 dynamic shape 지원이 제한적이라 (특히
# Meteor Lake 1세대) 고정 배치 = 1 로 컴파일하고 한 장씩 추론하는 게
# 가장 호환성 높다.  GPU/CPU 는 dynamic 도 가능하지만 단순성 위해 통일.
# 한 번 컴파일한 ``compiled_model`` 은 InferRequest 를 재사용해 여러 장을
# 빠르게 처리.
@lru_cache(maxsize=1)
def _compile_backbone():  # pragma: no cover — 환경 의존
    """MobileNetV3-Small backbone 을 OpenVINO 로 변환 후 NPU/GPU 컴파일.

    Thread-safe: ``functools.lru_cache`` 는 CPython 에서 내부 락으로 보호.
    """
    if not is_available():
        return None
    weights = models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
    backbone = models.mobilenet_v3_small(weights=weights)
    backbone.classifier = torch.nn.Identity()
    backbone.eval()
    example = torch.randn(1, 3, _INPUT_PX, _INPUT_PX)
    ov_model = ov.convert_model(backbone, example_input=example)
    core = ov.Core()
    primary = target_device()
    # NPU 가 실패하면 GPU 로 자동 fallback (모델/op 미지원 등).
    for cand in (primary, "GPU", "CPU"):
        if cand is None:
            continue
        try:
            return core.compile_model(ov_model, cand)
        except Exception:
            continue
    return None


def invalidate_caches() -> None:
    """모델 변경 등으로 컴파일 캐시를 무효화해야 할 때 호출.

    ``embedder.invalidate_caches()`` 가 학습 / 모델 교체 후 함께 호출.
    """
    _compile_backbone.cache_clear()


# ---------------------------------------------------------------------------
# 입력 텐서 만들기 (PyTorch 와 동일 전처리 — 결과 호환 보장)
# ---------------------------------------------------------------------------
def _make_input_array(path: Path) -> Optional[np.ndarray]:  # pragma: no cover
    """``(3, _INPUT_PX, _INPUT_PX)`` float32 NumPy 배열."""
    from ..utils import image_io
    try:
        gray = image_io.preprocessed_roi_gray(path, long_edge=_INPUT_PX)
    except Exception:
        return None
    h, w = gray.shape
    canvas = np.zeros((_INPUT_PX, _INPUT_PX), dtype=np.uint8)
    y0 = max(0, (_INPUT_PX - h) // 2)
    x0 = max(0, (_INPUT_PX - w) // 2)
    h_use = min(h, _INPUT_PX)
    w_use = min(w, _INPUT_PX)
    canvas[y0:y0 + h_use, x0:x0 + w_use] = gray[:h_use, :w_use]
    arr = np.repeat(canvas[None, :, :], 3, axis=0).astype(np.float32) / 255.0
    for c, (mean, std) in enumerate(zip(_IMAGENET_MEAN, _IMAGENET_STD)):
        arr[c] = (arr[c] - mean) / std
    return arr


# ---------------------------------------------------------------------------
# 공개 API — PyTorch embedder 에서 가용 시 호출
# ---------------------------------------------------------------------------
def compute_embeddings(paths: Iterable[Path],
                       *,
                       batch_size: int = 1,        # NPU 호환을 위해 사실상 1
                       head=None
                       ) -> Dict[Path, np.ndarray]:  # pragma: no cover
    """OpenVINO 백본 + (선택) PyTorch head 로 임베딩을 계산.

    NPU 플러그인의 dynamic shape 제약을 우회하기 위해 **한 장씩** 추론
    (``InferRequest`` 재사용으로 호출 오버헤드 최소화).  ``head`` 는
    ``triplet_model.ProjectionHead`` (CPU 실행, ~150K params 라 빠름).

    ``batch_size`` 파라미터는 시그니처 호환만 위해 유지하고 내부는 항상
    1 장씩 처리.  실패한 path 는 결과 dict 에서 빠지므로 ``embedder.
    compute_embeddings`` 가 PyTorch 경로로 보완할 수 있다 (partial-result
    fallback).
    """
    out: Dict[Path, np.ndarray] = {}
    if not is_available():
        return out
    compiled = _compile_backbone()
    if compiled is None:
        return out

    items = [Path(p) for p in paths]
    if not items:
        return out

    # InferRequest 재사용 — compile 비용은 1 회, 추론은 N 회.
    try:
        req = compiled.create_infer_request()
    except Exception:
        return out
    # 출력 텐서 키 (모델 변환 직후라 첫 output 만 있음).

    for p in items:
        arr = _make_input_array(p)
        if arr is None:
            continue
        # (1, 3, H, W) — NPU 는 고정 배치 1 만 안전하게 받음.
        x = arr[np.newaxis]
        try:
            res = req.infer({0: x})
        except Exception:
            continue
        # res 는 OrderedDict-like. 첫 output 의 (1, 576) 텐서.
        try:
            feat = list(res.values())[0]
        except Exception:
            continue
        if head is not None and _HAS_TORCH:
            try:
                with torch.no_grad():
                    feat = head(torch.from_numpy(feat)).cpu().numpy()
            except Exception:
                continue
        # L2 정규화 (PyTorch 경로와 동일 결과 보장).
        norm = float(np.linalg.norm(feat[0])) + 1e-9
        out[p] = (feat[0] / norm).astype(np.float32)
    return out
