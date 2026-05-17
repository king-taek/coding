"""Embedder 디바이스 감지 — Intel GPU / NPU / DirectML / MPS fallback 검증.

CI 환경에선 가속 디바이스가 없으므로 monkeypatch 로 각 분기를 시뮬레이션.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from aoi_verification.app.learning import embedder, embedder_openvino  # noqa: E402


def test_cpu_fallback_when_no_accelerator(monkeypatch):
    """CUDA/XPU/MPS 모두 미가용 + DirectML 미설치 → CPU."""
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    if hasattr(torch, "xpu"):
        monkeypatch.setattr(torch.xpu, "is_available", lambda: False)
    mps = getattr(torch.backends, "mps", None)
    if mps is not None:
        monkeypatch.setattr(mps, "is_available", lambda: False)
    dev = embedder._detect_device()
    assert dev is not None and dev.type == "cpu"


def test_cuda_picked_when_available(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    dev = embedder._detect_device()
    assert dev is not None and dev.type == "cuda"


def test_xpu_picked_when_cuda_unavailable(monkeypatch):
    """CUDA 없음 + XPU 있음 → Intel GPU (xpu)."""
    if not hasattr(torch, "xpu"):
        pytest.skip("torch.xpu 가 없는 PyTorch 버전")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.xpu, "is_available", lambda: True)
    dev = embedder._detect_device()
    assert dev is not None and dev.type == "xpu"


def test_mps_picked_when_only_mps(monkeypatch):
    mps = getattr(torch.backends, "mps", None)
    if mps is None:
        pytest.skip("torch.backends.mps 가 없는 PyTorch 버전")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    if hasattr(torch, "xpu"):
        monkeypatch.setattr(torch.xpu, "is_available", lambda: False)
    monkeypatch.setattr(mps, "is_available", lambda: True)
    monkeypatch.setattr(mps, "is_built", lambda: True)
    dev = embedder._detect_device()
    assert dev is not None and dev.type == "mps"


def test_device_label_describes_xpu(monkeypatch):
    """device_label 이 Intel GPU 를 '인텔 GPU 가속' 으로 표시."""
    if not hasattr(torch, "xpu"):
        pytest.skip("torch.xpu 가 없는 PyTorch 버전")
    monkeypatch.setattr(embedder, "_DEVICE", torch.device("xpu"))
    # OpenVINO 가 우선이므로 그 부분도 차단.
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: False)
    label = embedder.device_label()
    assert "Intel GPU" in label, label


def test_device_label_describes_cuda(monkeypatch):
    monkeypatch.setattr(embedder, "_DEVICE", torch.device("cuda"))
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: False)
    # get_device_name 은 cuda 가 실제 없으면 예외 → 그래도 fallback 'CUDA' 표시.
    label = embedder.device_label()
    assert "GPU 가속" in label


def test_openvino_label_takes_precedence(monkeypatch):
    """OpenVINO + NPU 가 인식되면 PyTorch device 라벨보다 우선."""
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: True)
    monkeypatch.setattr(
        embedder_openvino, "device_label",
        lambda: "NPU 가속 (Intel AI Boost — OpenVINO)",
    )
    label = embedder.device_label()
    assert "NPU" in label


def test_openvino_unavailable_when_module_missing():
    """openvino 가 설치 안 됐을 때 is_available()=False, target=None."""
    # CI 에선 openvino 미설치 → 정상 흐름.
    if embedder_openvino._HAS_OPENVINO:
        pytest.skip("openvino 가 설치되어 있어 이 테스트는 무의미")
    assert embedder_openvino.is_available() is False
    assert embedder_openvino.target_device() is None
    assert embedder_openvino.device_label() == ""
