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

# 고효율 모드(다중 유닛) 용 백본 종류 — 유닛별로 서로 다른 모델을 고정한다.
MODEL_MOBILENET_V3 = "mobilenet_v3_small"   # GPU 유닛 (현행 CNN)
MODEL_RESNET18 = "resnet18"                 # NPU 유닛 (다른 추론 모델)
EMBED_DIM = {MODEL_MOBILENET_V3: 576, MODEL_RESNET18: 512}  # 문서용 (인덱스는 dim 무관)


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
# Backbone 컴파일 (lazy) + 디바이스별 최적 처리량 hint
# ---------------------------------------------------------------------------
# NPU/GPU 활용도를 끌어올리려면 ‘여러 추론을 동시 in-flight’ 하게 해야 한다
# (AsyncInferQueue).  OpenVINO 에 PERFORMANCE_HINT=THROUGHPUT 을 주면
# 디바이스에 맞는 최적 스트림 수가 자동 설정됨.
# Batch 는 1 로 고정 — NPU 는 dynamic shape 미지원이 잦아 단순/호환성 우선.
@lru_cache(maxsize=1)
def _compile_backbone():  # pragma: no cover — 환경 의존
    """MobileNetV3-Small backbone 을 OpenVINO 로 변환 후 NPU/GPU 컴파일.

    Thread-safe: ``functools.lru_cache`` 는 CPython 에서 내부 락으로 보호.
    반환 — (compiled_model, target_device_str) 튜플.
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
    # NPU 가 실패하면 GPU → CPU 로 자동 fallback (op 미지원 등).
    for cand in (primary, "GPU", "CPU"):
        if cand is None:
            continue
        try:
            # THROUGHPUT 힌트 — 디바이스에 따라 적정 stream/infer-request 수
            # 를 OpenVINO 가 알아서 설정 → AsyncInferQueue 와 함께 NPU 최대 활용.
            compiled = core.compile_model(
                ov_model, cand,
                config={"PERFORMANCE_HINT": "THROUGHPUT"},
            )
            # 실제로 어떤 디바이스에 컴파일됐는지 로그로 검증 — Intel iGPU 가
            # 진짜 쓰이는지 확인 (CPU 로 조용히 폴백되는 상황 탐지용).
            _log_compiled_device(core, cand)
            return (compiled, cand)
        except Exception:
            continue
    return None


# 마지막으로 컴파일에 성공한 OpenVINO 디바이스 ("GPU"/"NPU"/"CPU") — UI 표시용.
_last_compiled_target: Optional[str] = None
_last_compiled_name: str = ""


def _log_compiled_device(core, cand: str) -> None:  # pragma: no cover - 환경 의존
    """컴파일된 실제 디바이스의 풀 네임을 로그로 남긴다 (iGPU 실사용 검증)."""
    global _last_compiled_target, _last_compiled_name
    import logging
    name = ""
    try:
        name = str(core.get_property(cand, "FULL_DEVICE_NAME"))
    except Exception:
        name = cand
    _last_compiled_target = cand
    _last_compiled_name = name
    logging.getLogger("aoi.openvino").info(
        "OpenVINO backbone compiled on %s (%s)", cand, name,
    )


def last_compiled_device() -> tuple:  # pragma: no cover - 환경 의존
    """(target, full_name) — 컴파일이 실제로 어디서 됐는지 (UI/디버그용)."""
    return (_last_compiled_target, _last_compiled_name)


def _optimal_streams(compiled) -> int:  # pragma: no cover — 환경 의존
    """디바이스가 권장하는 동시 추론 스트림 수 — AsyncInferQueue jobs."""
    try:
        n = compiled.get_property("OPTIMAL_NUMBER_OF_INFER_REQUESTS")
        return max(1, int(n))
    except Exception:
        return 4    # 합리적 기본값 (NPU 2~4 / GPU 4~8 사이).


def invalidate_caches() -> None:
    """모델 변경 등으로 컴파일 캐시를 무효화해야 할 때 호출.

    ``embedder.invalidate_caches()`` 가 학습 / 모델 교체 후 함께 호출.
    """
    _compile_backbone.cache_clear()
    compile_model_on.cache_clear()
    _compiled_units.clear()


# ---------------------------------------------------------------------------
# 입력 텐서 만들기 (PyTorch 와 동일 전처리 — 결과 호환 보장)
# ---------------------------------------------------------------------------
def _make_input_array(path: Path, cfg=None) -> Optional[np.ndarray]:  # pragma: no cover
    """``(3, _INPUT_PX, _INPUT_PX)`` float32 NumPy 배열.  ``cfg`` 가 주어지면
    강화/KLA 전처리 적용 (PyTorch 경로와 동일하게)."""
    from ..utils import image_io
    try:
        gray = image_io.preprocessed_roi_gray(path, long_edge=_INPUT_PX, cfg=cfg)
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
                       head=None,
                       cfg=None
                       ) -> Dict[Path, np.ndarray]:  # pragma: no cover
    """OpenVINO 백본 + (선택) PyTorch head 로 임베딩을 ``AsyncInferQueue``
    로 병렬 계산 — NPU/GPU 활용도 최대화.

    NPU 플러그인의 dynamic shape 제약을 우회하기 위해 **batch=1** 고정.
    대신 ``AsyncInferQueue(jobs=N)`` 으로 N 개 추론을 동시 in-flight 시켜
    파이프라인을 채우면 NPU 가 idle 없이 일한다 (THROUGHPUT 힌트와 함께).

    실패 path 는 결과 dict 에 누락 — 호출자(``embedder.compute_embeddings``)
    가 PyTorch 로 보완.
    """
    out: Dict[Path, np.ndarray] = {}
    if not is_available():
        return out
    pack = _compile_backbone()
    if pack is None:
        return out
    compiled, _dev = pack

    items = [Path(p) for p in paths]
    if not items:
        return out

    # 입력 텐서 사전 생성 — async 시작 시점에 즉시 큐잉.
    inputs: list[tuple[Path, np.ndarray]] = []
    for p in items:
        arr = _make_input_array(p, cfg)
        if arr is not None:
            inputs.append((p, arr[np.newaxis]))   # (1, 3, H, W)
    if not inputs:
        return out

    raw = _infer_raw(compiled, inputs, _optimal_streams(compiled))

    # head 통과 + L2 정규화 → PyTorch 경로와 동일 형식.
    for p, feat in raw.items():
        if head is not None and _HAS_TORCH:
            try:
                with torch.no_grad():
                    feat = head(torch.from_numpy(feat)).cpu().numpy()
            except Exception:
                continue
        norm = float(np.linalg.norm(feat[0])) + 1e-9
        out[p] = (feat[0] / norm).astype(np.float32)
    return out


# ---------------------------------------------------------------------------
# 공용 비동기 추론 — AsyncInferQueue 로 N 개 동시 in-flight (NPU/GPU saturate)
# ---------------------------------------------------------------------------
def _infer_raw(compiled, inputs, n_streams: int) -> Dict[Path, np.ndarray]:  # pragma: no cover - 환경 의존
    """``inputs=[(path, (1,3,H,W) ndarray), ...]`` → ``{path: raw_feat}``.

    ``AsyncInferQueue(jobs=n_streams)`` 로 다수 추론을 동시에 띄워 파이프라인을
    채운다.  큐를 못 만들면 InferRequest 단일 흐름으로 폴백.  실패 path 는 누락.
    """
    try:
        from openvino.runtime import AsyncInferQueue
    except Exception:
        AsyncInferQueue = None  # type: ignore
    raw: Dict[Path, np.ndarray] = {}
    queue = None
    if AsyncInferQueue is not None:
        try:
            queue = AsyncInferQueue(compiled, jobs=max(1, int(n_streams)))
        except Exception:
            queue = None
    if queue is not None:
        def _cb(infer_request, userdata):
            try:
                raw[userdata] = list(infer_request.results.values())[0]
            except Exception:
                pass
        queue.set_callback(_cb)
        for p, x in inputs:
            try:
                queue.start_async({0: x}, userdata=p)
            except Exception:
                continue
        try:
            queue.wait_all()
        except Exception:
            pass
    else:
        try:
            req = compiled.create_infer_request()
        except Exception:
            return raw
        for p, x in inputs:
            try:
                raw[p] = list(req.infer({0: x}).values())[0]
            except Exception:
                continue
    return raw


# ---------------------------------------------------------------------------
# 고효율 모드 — 장치 고정 컴파일/추론 (유닛별 서로 다른 모델 동시 가동)
# ---------------------------------------------------------------------------
def available_units() -> List[str]:
    """OpenVINO 로 가속 가능한 Intel 유닛 — ``["GPU","NPU"]`` 중 실제 존재분.

    torch/openvino 가 없으면 빈 리스트.  스케줄러가 이 결과로 어떤 워커를
    띄울지 결정한다 (CPU 는 항상 별도로 가동)."""
    if not (_HAS_TORCH and _HAS_OPENVINO):
        return []
    devs = _list_ov_devices()
    out: List[str] = []
    for cand in ("GPU", "NPU"):
        if any(d == cand or d.startswith(cand + ".") for d in devs):
            out.append(cand)
    return out


def _build_ov_model(model_kind: str):  # pragma: no cover - 환경 의존
    """torchvision 백본 → OpenVINO 모델 (raw 임베딩 — classifier/fc 제거).

    ``.eval()`` 로 BatchNorm 을 폴딩한 뒤 변환해야 NPU/GPU 에서 정확하다.
    """
    if model_kind == MODEL_RESNET18:
        weights = models.ResNet18_Weights.IMAGENET1K_V1
        backbone = models.resnet18(weights=weights)
        backbone.fc = torch.nn.Identity()       # 512-d 임베딩
    else:                                        # MobileNetV3-Small (576-d)
        weights = models.MobileNet_V3_Small_Weights.IMAGENET1K_V1
        backbone = models.mobilenet_v3_small(weights=weights)
        backbone.classifier = torch.nn.Identity()
    backbone.eval()
    example = torch.randn(1, 3, _INPUT_PX, _INPUT_PX)
    return ov.convert_model(backbone, example_input=example)


# (model_kind, device) → 실제 컴파일된 디바이스 풀네임 — 상태표시/디버그용.
_compiled_units: Dict[tuple, str] = {}


@lru_cache(maxsize=4)
def compile_model_on(model_kind: str, device: str):  # pragma: no cover - 환경 의존
    """``model_kind`` 백본을 ``device`` ("GPU"/"NPU") 에 고정 컴파일.

    반환 ``(compiled, full_name)`` 또는 실패 시 ``None``.  **다른 디바이스로
    silent fallback 하지 않는다** — 폴백은 스케줄러가 유닛 단위로 결정하므로,
    GPU 컴파일 실패는 단지 그 유닛을 띄우지 않는다는 뜻이다.
    """
    if not (_HAS_TORCH and _HAS_OPENVINO):
        return None
    try:
        ov_model = _build_ov_model(model_kind)
        core = ov.Core()
        compiled = core.compile_model(
            ov_model, device, config={"PERFORMANCE_HINT": "THROUGHPUT"},
        )
    except Exception:
        return None
    name = device
    try:
        name = str(core.get_property(device, "FULL_DEVICE_NAME"))
    except Exception:
        pass
    _compiled_units[(model_kind, device)] = name
    import logging
    logging.getLogger("aoi.openvino").info(
        "OpenVINO %s compiled on %s (%s)", model_kind, device, name,
    )
    return (compiled, name)


def active_unit_labels() -> List[str]:
    """컴파일에 성공한 유닛 디바이스 라벨 (중복 제거).  상태바 표시용."""
    return sorted({dev for (_kind, dev) in _compiled_units})


def device_embed(paths: Iterable[Path],
                 *,
                 model_kind: str,
                 device: str,
                 cfg=None,
                 jobs: Optional[int] = None) -> Dict[Path, np.ndarray]:  # pragma: no cover - 환경 의존
    """``model_kind`` 백본을 ``device`` 에 고정 컴파일해 raw 임베딩(L2 정규화) 계산.

    ``jobs`` 로 동시 in-flight 추론 수를 지정 — NPU(8GB)는 크게(예: 16) 주어
    메모리/파이프라인을 적극 활용한다.  실패 path 는 결과에서 누락.
    """
    out: Dict[Path, np.ndarray] = {}
    pack = compile_model_on(model_kind, device)
    if pack is None:
        return out
    compiled, _name = pack
    items = [Path(p) for p in paths]
    if not items:
        return out
    inputs: list[tuple[Path, np.ndarray]] = []
    for p in items:
        arr = _make_input_array(p, cfg)
        if arr is not None:
            inputs.append((p, arr[np.newaxis]))
    if not inputs:
        return out
    n_streams = _optimal_streams(compiled) if jobs is None else max(1, int(jobs))
    raw = _infer_raw(compiled, inputs, n_streams)
    for p, feat in raw.items():
        norm = float(np.linalg.norm(feat[0])) + 1e-9
        out[p] = (feat[0] / norm).astype(np.float32)
    return out
