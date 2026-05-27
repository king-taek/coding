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
import threading
import time
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
# 가속 유닛 활동 추적 — 상태바의 GPU/NPU '가동/대기' 표시용.
# OpenVINO 추론이 돌 때마다 디바이스별 timestamp 를 찍고, GUI 가 최근 활동
# 여부(window 초 이내)를 폴링한다.  Intel GPU/NPU 의 실제 점유율(%)은 이식성
# 있게 얻을 수 없으므로, '우리가 그 장치로 추론 중인지'를 대신 보여준다.
# ---------------------------------------------------------------------------
_unit_activity: Dict[str, float] = {}
_unit_activity_lock = threading.Lock()


def _unit_tag(device: str) -> str:
    d = str(device).upper()
    if d.startswith("NPU"):
        return "NPU"
    if d.startswith("GPU"):
        return "GPU"
    return d


def mark_unit_active(device: str) -> None:
    """``device`` ("GPU"/"NPU"/"GPU.0" 등) 에서 추론이 발생했음을 기록."""
    with _unit_activity_lock:
        _unit_activity[_unit_tag(device)] = time.monotonic()


def unit_busy(device: str, window: float = 2.0) -> bool:
    """``device`` 가 최근 ``window`` 초 이내에 추론했으면 True ('가동 중')."""
    tag = _unit_tag(device)
    with _unit_activity_lock:
        t = _unit_activity.get(tag)
    return t is not None and (time.monotonic() - t) <= window


# ---------------------------------------------------------------------------
# Device 감지
# ---------------------------------------------------------------------------
def _list_ov_devices() -> List[str]:  # pragma: no cover — 환경 의존
    import logging
    log = logging.getLogger("aoi.openvino")
    if not _HAS_OPENVINO:
        log.warning("OpenVINO 미설치 — GPU/NPU 가속 불가 (requirements 의 "
                    "openvino 설치 필요). torch=%s", _HAS_TORCH)
        return []
    try:
        devs = list(ov.Core().available_devices)
        log.info("OpenVINO available_devices: %s", devs)
        return devs
    except Exception:
        log.warning("OpenVINO 디바이스 조회 실패", exc_info=True)
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
    """상태바 표시용 — 사용 가능할 때만 비어있지 않은 문자열 반환.

    NPU 가속 문구는 표시하지 않는다(사용자 요청).  Intel GPU 가 있으면 그것만
    표시하고, NPU 전용 환경에서는 빈 문자열을 반환해 embedder 가 torch/CPU
    라벨로 폴백하도록 둔다.
    """
    if not is_available():
        return ""
    devs = _list_ov_devices()
    if any(d == "GPU" or d.startswith("GPU.") for d in devs):
        return "Intel GPU 가속 (OpenVINO)"
    return ""


# ---------------------------------------------------------------------------
# Backbone 컴파일 (lazy) + 디바이스별 최적 처리량 hint
# ---------------------------------------------------------------------------
# NPU/GPU 활용도를 끌어올리려면 ‘여러 추론을 동시 in-flight’ 하게 해야 한다
# (AsyncInferQueue).  OpenVINO 에 PERFORMANCE_HINT=THROUGHPUT 을 주면
# 디바이스에 맞는 최적 스트림 수가 자동 설정됨.
# Batch 는 1 로 고정 — NPU 는 dynamic shape 미지원이 잦아 단순/호환성 우선.
def _force_static_shape(ov_model, batch: int = 1) -> None:  # pragma: no cover
    """입력을 정적 ``[batch,3,_INPUT_PX,_INPUT_PX]`` 으로 고정.

    ``ov.convert_model`` 은 배치 차원을 동적(-1)으로 남기는 경우가 있는데,
    Intel **NPU 플러그인은 동적 shape 컴파일을 거부**한다(GPU 는 허용).  정적화
    하지 않으면 NPU 컴파일이 조용히 실패해 NPU 가 영영 '대기' 로 남는다.
    ``batch>1`` 이면 요청당 B장을 한 번에 추론(테스트용).  실패해도 무시."""
    try:
        ov_model.reshape([int(batch), 3, _INPUT_PX, _INPUT_PX])
    except Exception:
        import logging
        logging.getLogger("aoi.openvino").debug(
            "reshape→static 실패(무시 가능)", exc_info=True,
        )


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
    _force_static_shape(ov_model)
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
        except Exception as e:
            import logging
            _compile_errors[("backbone", cand)] = repr(e)
            logging.getLogger("aoi.openvino").warning(
                "OpenVINO backbone 컴파일 실패: %s → 다음 디바이스로 폴백", cand,
                exc_info=True,
            )
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
    _compile_errors.clear()


# ---------------------------------------------------------------------------
# 입력 텐서 만들기 (PyTorch 와 동일 전처리 — 결과 호환 보장)
# ---------------------------------------------------------------------------
def _make_input_array(path: Path, cfg=None, side=None) -> Optional[np.ndarray]:  # pragma: no cover
    """``(3, _INPUT_PX, _INPUT_PX)`` float32 NumPy 배열.  ``cfg`` 가 주어지면
    강화/KLA 전처리 적용 (PyTorch 경로와 동일하게).  ``side`` ('ref'/'val') 가
    주어지면 중앙 30% crop 도 side 별로 적용(center_crop 옵션이 켜진 경우)."""
    from ..utils import image_io
    try:
        gray = image_io.preprocessed_roi_gray(path, long_edge=_INPUT_PX, cfg=cfg,
                                               side=side)
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


def _preprocess_workers() -> int:
    """전처리(디코드/리사이즈) 병렬 워커 수 — cv2/PIL 은 GIL 을 풀어 멀티코어 활용."""
    return max(2, min(8, (os.cpu_count() or 4)))


def _preprocess_parallel(items, cfg=None, side=None):  # pragma: no cover - 환경 의존
    """경로들을 **멀티스레드로 전처리**해 ``(path, arr)`` 를 준비되는 대로 yield.

    기존엔 모든 이미지를 단일 스레드로 전처리한 *뒤* 일괄 추론해, 전처리 동안 GPU
    가 놀고 추론 동안 CPU 가 놀았다(#3).  여기서는 전처리를 코어 수만큼 병렬로
    돌리고 준비되는 텐서를 즉시 흘려보내, 호출자가 추론 큐(``AsyncInferQueue``)에
    바로 투입해 전처리(CPU)·추론(GPU) 이 동시에 돌게 한다.

    메모리 폭주를 막기 위해 in-flight 전처리 수를 ``window`` 로 제한(완료될 때마다
    다음 항목을 채움).  실패(``None``)는 건너뛴다.
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    items = [Path(p) for p in items]
    if not items:
        return
    workers = _preprocess_workers()
    window = max(workers * 2, 4)
    it = iter(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        inflight: dict = {}
        for _ in range(window):
            try:
                p = next(it)
            except StopIteration:
                break
            inflight[pool.submit(_make_input_array, p, cfg, side)] = p
        while inflight:
            done, _pending = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fut in done:
                p = inflight.pop(fut)
                try:
                    arr = fut.result()
                except Exception:
                    arr = None
                try:                                   # 완료분만큼 다음 항목 보충
                    nxt = next(it)
                    inflight[pool.submit(_make_input_array, nxt, cfg, side)] = nxt
                except StopIteration:
                    pass
                if arr is not None:
                    yield p, arr


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
    mark_unit_active(_dev)

    items = [Path(p) for p in paths]
    if not items:
        return out

    # 전처리(멀티스레드)와 추론(GPU/NPU)을 파이프라인으로 겹쳐 가동(#3) — 준비되는
    # 텐서를 즉시 AsyncInferQueue 로 흘려보내 장치가 놀지 않게 한다.
    inputs = ((p, arr[np.newaxis]) for p, arr in _preprocess_parallel(items, cfg))
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
def _udata_count(userdata) -> int:
    """userdata 가 path 면 1, (real_paths) 튜플(정적 배치)이면 그 길이."""
    return len(userdata) if isinstance(userdata, tuple) else 1


def _infer_raw(compiled, inputs, n_streams: int, progress_cb=None) -> Dict[Path, np.ndarray]:  # pragma: no cover - 환경 의존
    """``inputs=[(path, (1,3,H,W) ndarray), ...]`` → ``{path: raw_feat}``.

    ``AsyncInferQueue(jobs=n_streams)`` 로 다수 추론을 동시에 띄워 파이프라인을
    채운다.  큐를 못 만들면 InferRequest 단일 흐름으로 폴백.  실패 path 는 누락.
    ``progress_cb(n)`` 이 주어지면 추론 결과가 나올 때마다 처리 장수를 보고한다
    (느린 NAS 에서도 진행률이 per-image 로 즉시 올라가게 — #3)."""
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
            if progress_cb is not None:
                try:
                    progress_cb(_udata_count(userdata))
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
            if progress_cb is not None:
                try:
                    progress_cb(_udata_count(p))
                except Exception:
                    pass
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


def accelerator_presence() -> Dict[str, object]:
    """상태바 표시용 — 인텔 GPU/NPU **존재 여부**를 OpenVINO 만으로 조사.

    스케줄러용 ``available_units()`` 와 달리 torch 설치 여부에 의존하지 않는다
    (장치 존재는 추론 백엔드와 무관).  반환::

        {"GPU": bool, "NPU": bool, "devices": [...], "reason": str}

    ``reason`` 은 GPU/NPU 가 안 잡힐 때의 진단 문자열(미설치/조회실패/디바이스
    없음) — GUI 툴팁으로 노출해 사용자가 원인을 바로 알 수 있게 한다."""
    if not _HAS_OPENVINO:
        return {"GPU": False, "NPU": False, "devices": [],
                "reason": "OpenVINO 미설치"}
    devs = _list_ov_devices()
    present = {
        cand: any(d == cand or d.startswith(cand + ".") for d in devs)
        for cand in ("GPU", "NPU")
    }
    if not devs:
        reason = "OpenVINO 디바이스 조회 실패"
    elif not (present["GPU"] or present["NPU"]):
        reason = "GPU/NPU 미감지 (드라이버/플러그인 확인)"
    else:
        reason = ""
    return {"GPU": present["GPU"], "NPU": present["NPU"],
            "devices": devs, "reason": reason}


def _build_ov_model(model_kind: str, batch: int = 1):  # pragma: no cover - 환경 의존
    """torchvision 백본 → OpenVINO 모델 (raw 임베딩 — classifier/fc 제거).

    ``.eval()`` 로 BatchNorm 을 폴딩한 뒤 변환해야 NPU/GPU 에서 정확하다.
    ``batch`` 로 정적 배치 크기를 지정(테스트용, 기본 1).
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
    ov_model = ov.convert_model(backbone, example_input=example)
    _force_static_shape(ov_model, batch)
    return ov_model


# (model_kind, device) → 실제 컴파일된 디바이스 풀네임 — 상태표시/디버그용.
_compiled_units: Dict[tuple, str] = {}
# (model_kind, device) → 컴파일 실패 에러 문자열 — 상태바 툴팁/진단용.
_compile_errors: Dict[tuple, str] = {}


@lru_cache(maxsize=8)
def compile_model_on(model_kind: str, device: str, batch: int = 1):  # pragma: no cover
    """``model_kind`` 백본을 ``device`` ("GPU"/"NPU") 에 정적 배치 ``batch`` 로 컴파일.

    반환 ``(compiled, full_name)`` 또는 실패 시 ``None``.  **다른 디바이스로
    silent fallback 하지 않는다** — 폴백은 스케줄러가 유닛 단위로 결정하므로,
    GPU 컴파일 실패는 단지 그 유닛을 띄우지 않는다는 뜻이다.  lru_cache 가
    ``(model_kind, device, batch)`` 별로 컴파일 결과를 보관한다.
    """
    if not (_HAS_TORCH and _HAS_OPENVINO):
        return None
    import logging
    log = logging.getLogger("aoi.openvino")
    batch = max(1, int(batch))
    try:
        ov_model = _build_ov_model(model_kind, batch)
        core = ov.Core()
        compiled = core.compile_model(
            ov_model, device, config={"PERFORMANCE_HINT": "THROUGHPUT"},
        )
    except Exception as e:
        # 조용히 None 만 반환하면 NPU 가 왜 '대기' 인지 알 수 없으므로
        # 에러를 보존(상태바 툴팁용) + 로그.
        _compile_errors[(model_kind, device)] = repr(e)
        log.warning("OpenVINO %s 컴파일 실패 on %s (batch=%d) — 해당 유닛 비활성",
                    model_kind, device, batch, exc_info=True)
        return None
    _compile_errors.pop((model_kind, device), None)
    name = device
    try:
        name = str(core.get_property(device, "FULL_DEVICE_NAME"))
    except Exception:
        pass
    _compiled_units[(model_kind, device)] = name
    log.info("OpenVINO %s compiled on %s (%s) batch=%d",
             model_kind, device, name, batch)
    return (compiled, name)


def active_unit_labels() -> List[str]:
    """컴파일에 성공한 유닛 디바이스 라벨 (중복 제거).  상태바 표시용."""
    return sorted({dev for (_kind, dev) in _compiled_units})


def compile_diagnostics() -> Dict[str, object]:
    """상태바 툴팁/디버그용 컴파일 진단 — 추측을 끝내기 위한 가시화.

    반환::

        {"compiled": ["GPU", ...],            # 컴파일 성공 디바이스
         "errors": {"NPU": "<에러 문자열>"}}  # 디바이스별 마지막 실패 사유

    추론 컴파일은 lazy(첫 매칭 시) 이므로, 매칭을 한 번 돌린 뒤에 값이 채워진다.
    """
    errors: Dict[str, str] = {}
    for (_kind, device), msg in _compile_errors.items():
        errors[_unit_tag(device)] = msg
    return {"compiled": active_unit_labels(), "errors": errors}


def _l2(vec: np.ndarray) -> np.ndarray:  # pragma: no cover - 환경 의존
    norm = float(np.linalg.norm(vec)) + 1e-9
    return (vec / norm).astype(np.float32)


# ---------------------------------------------------------------------------
# 임베딩 영속 디스크 캐시 (#3) — 재실행/같은 이미지는 디코드·추론을 건너뛴다.
# 키 = 원본경로 | mtime | 모델 | 입력해상도 | center-crop 여부.  device 는 키에
# 넣지 않아 GPU/CPU 폴백이 캐시를 공유한다(같은 모델이라 매칭엔 영향 없음).
# ---------------------------------------------------------------------------
def _emb_signature(model_kind: str, cfg, side) -> str:
    cc = 0
    try:
        if cfg is not None and getattr(cfg, "_center_crop_for", None) is not None:
            cc = 1 if cfg._center_crop_for(side) else 0
    except Exception:
        cc = 0
    return f"{model_kind}|px{_INPUT_PX}|cc{cc}"


def _emb_cache_file(path: Path, sig: str) -> Path:
    import hashlib
    import os as _os

    from ..utils import cache as _cache
    from ..utils import paths as _paths
    # NAS 왕복 감소(#5): resolve() 미사용(순수 abspath) + 세션 메모이즈된 mtime 재사용.
    ap = _os.path.abspath(str(path))
    mtime = int(_cache.memo_mtime(path))
    h = hashlib.sha1(f"{ap}|{mtime}|{sig}".encode("utf-8", "replace")).hexdigest()
    return _paths.embedding_cache_dir() / f"{h}.npy"


def _emb_cache_load(path: Path, sig: str) -> Optional[np.ndarray]:
    f = _emb_cache_file(path, sig)
    if f.exists():
        try:
            return np.load(str(f))
        except Exception:
            return None
    return None


def _emb_cache_save(path: Path, vec: np.ndarray, sig: str) -> None:
    try:
        tmp = _emb_cache_file(path, sig)
        np.save(str(tmp), np.asarray(vec, dtype=np.float32))
    except Exception:
        pass


def _emb_finish(out, computed_paths, sig, use_cache, model_kind, device,
                n_hit, t0) -> None:  # pragma: no cover - 환경 의존
    """새로 계산한 임베딩을 디스크에 저장하고, 단계 소요시간을 로그로 남긴다(#3)."""
    import logging
    import time as _time
    dt = _time.perf_counter() - t0
    n_new = 0
    if use_cache:
        for p in computed_paths:
            v = out.get(p)
            if v is not None:
                _emb_cache_save(p, v, sig)
                n_new += 1
    rate = (len(computed_paths) / dt) if dt > 1e-6 else 0.0
    logging.getLogger("aoi.openvino").info(
        "embed[%s/%s]: 신규 %d장 %.2fs(%.1f img/s) · 캐시적중 %d장 · 저장 %d장",
        model_kind, device, len(computed_paths), dt, rate, n_hit, n_new,
    )


def device_embed(paths: Iterable[Path],
                 *,
                 model_kind: str,
                 device: str,
                 cfg=None,
                 jobs: Optional[int] = None,
                 batch: int = 1,
                 side=None,
                 progress_cb=None) -> Dict[Path, np.ndarray]:  # pragma: no cover - 환경 의존
    """``model_kind`` 백본을 ``device`` 에 고정 컴파일해 raw 임베딩(L2 정규화) 계산.

    ``side`` ('ref'/'val') 가 주어지고 cfg.center_crop 이 켜져 있으면 중앙 30%
    crop 입력으로 임베딩한다(개발자 벤치마크의 center-crop 변형용).

    ``jobs`` 로 동시 in-flight 추론 수를 지정 — NPU(8GB)는 크게 주어 메모리/
    파이프라인을 적극 활용한다.  ``batch>1`` 이면 요청당 B장을 한 번에 추론
    (정적 배치 B, 테스트용).  실패 path 는 결과에서 누락.  배치 결과는 batch=1
    과 동일(임베딩은 배치 무관).
    """
    import logging
    import time as _time

    out: Dict[Path, np.ndarray] = {}
    batch = max(1, int(batch))
    all_items = [Path(p) for p in paths]
    if not all_items:
        return out

    # 디스크 캐시 히트는 디코드·추론을 통째로 건너뛴다(#3).  cfg 가 있을 때만
    # 캐시(테스트의 cfg=None 경로는 영향 없음).
    use_cache = cfg is not None
    sig = _emb_signature(model_kind, cfg, side) if use_cache else ""
    items = all_items
    n_hit = 0
    if use_cache:
        items = []
        for p in all_items:
            v = _emb_cache_load(p, sig)
            if v is not None:
                out[p] = v
                n_hit += 1
            else:
                items.append(p)
        if n_hit and progress_cb is not None:     # 캐시 적중분도 진행률에 반영(#3)
            try:
                progress_cb(n_hit)
            except Exception:
                pass
        if not items:
            logging.getLogger("aoi.openvino").debug(
                "embed: %d개 전부 캐시 적중(%s)", n_hit, model_kind,
            )
            return out

    pack = compile_model_on(model_kind, device, batch)
    if pack is None:
        return out
    compiled, _name = pack
    mark_unit_active(device)
    n_streams = _optimal_streams(compiled) if jobs is None else max(1, int(jobs))
    # 전처리를 멀티스레드로 돌려 준비되는 텐서를 즉시 추론 큐로 흘려보낸다(#3) —
    # 전처리(CPU)와 추론(GPU/NPU)이 동시에 돌아 장치 유휴를 줄인다.
    _t0 = _time.perf_counter()
    prepped = _preprocess_parallel(items, cfg, side)

    if batch <= 1:
        inputs = ((p, a[np.newaxis]) for p, a in prepped)   # (1,3,H,W) per path
        raw = _infer_raw(compiled, inputs, n_streams, progress_cb=progress_cb)
        for p, feat in raw.items():
            out[p] = _l2(feat[0])
        _emb_finish(out, items, sig, use_cache, model_kind, device, n_hit, _t0)
        return out

    # 정적 배치 B — 준비되는 대로 B장씩 묶어 (B,3,H,W) 로 추론.  마지막 그룹은
    # 0-pad 후 실제 path 수만큼만 결과를 취한다.  userdata 는 실제 path 튜플.
    def _grouped():
        buf_p: list = []
        buf_a: list = []
        for p, a in prepped:
            buf_p.append(p)
            buf_a.append(a)
            if len(buf_a) == batch:
                yield tuple(buf_p), np.stack(buf_a, axis=0)
                buf_p, buf_a = [], []
        if buf_a:
            stack = np.stack(buf_a, axis=0)
            if stack.shape[0] < batch:
                pad = np.zeros((batch - stack.shape[0],) + stack.shape[1:],
                               dtype=stack.dtype)
                stack = np.concatenate([stack, pad], axis=0)  # (B,3,H,W)
            yield tuple(buf_p), stack

    raw = _infer_raw(compiled, _grouped(), n_streams, progress_cb=progress_cb)
    for real_paths, feat in raw.items():
        for b, p in enumerate(real_paths):                   # 실제 행만(패딩 무시)
            out[p] = _l2(feat[b])
    _emb_finish(out, items, sig, use_cache, model_kind, device, n_hit, _t0)
    return out
