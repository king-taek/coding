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


# ---------------------------------------------------------------------------
# 라우팅 / partial-result 폴백 검증
# ---------------------------------------------------------------------------
def test_compute_embeddings_routes_through_openvino(monkeypatch, tmp_path):
    """OpenVINO 가 가용하면 compute_embeddings 가 그 쪽으로 라우팅된다."""
    import numpy as np
    paths = [tmp_path / f"a{i}.jpg" for i in range(3)]
    sentinel = {p: np.ones(128, dtype=np.float32) for p in paths}

    monkeypatch.setattr(embedder, "is_available", lambda: True)
    monkeypatch.setattr(embedder, "get_active_mode", lambda: "fake_model")
    monkeypatch.setattr(embedder.registry, "BASIC", "basic_other")
    # OV 분기 라우팅.
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: True)
    monkeypatch.setattr(
        embedder_openvino, "compute_embeddings",
        lambda paths, *, batch_size=1, head=None: dict(sentinel),
    )
    # PyTorch 폴백이 호출되지 않아야 함 — 호출되면 sentinel 과 다른 값을 섞을 것.
    def _unexpected_pytorch(*_a, **_kw):
        raise AssertionError("PyTorch 경로가 호출되면 안 됨 (OV 가 모두 처리)")
    monkeypatch.setattr(embedder, "_compute_embeddings_pytorch",
                         _unexpected_pytorch)
    # cpu_head_clone 도 head=None 인 시나리오라 안전.
    monkeypatch.setattr(embedder, "_load_head_for", lambda mode: None)

    out = embedder.compute_embeddings(paths)
    assert out == sentinel


def test_partial_openvino_result_falls_through_to_pytorch(monkeypatch, tmp_path):
    """OV 가 일부만 처리하면 누락된 path 는 PyTorch 가 보완한다."""
    import numpy as np
    paths = [tmp_path / f"b{i}.jpg" for i in range(4)]
    ov_part = {paths[0]: np.full(128, 0.1, dtype=np.float32),
               paths[2]: np.full(128, 0.3, dtype=np.float32)}
    pt_part = {paths[1]: np.full(128, 0.2, dtype=np.float32),
               paths[3]: np.full(128, 0.4, dtype=np.float32)}

    monkeypatch.setattr(embedder, "is_available", lambda: True)
    monkeypatch.setattr(embedder, "get_active_mode", lambda: "fake_model")
    monkeypatch.setattr(embedder.registry, "BASIC", "basic_other")
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: True)
    monkeypatch.setattr(
        embedder_openvino, "compute_embeddings",
        lambda paths, *, batch_size=1, head=None: dict(ov_part),
    )
    # PyTorch fallback 은 누락된 path 만 받아야 한다.
    received_paths = []
    def _fake_pt(paths, *, batch_size, mode):
        received_paths.extend(paths)
        return dict(pt_part)
    monkeypatch.setattr(embedder, "_compute_embeddings_pytorch", _fake_pt)
    monkeypatch.setattr(embedder, "_load_head_for", lambda mode: None)

    out = embedder.compute_embeddings(paths)
    assert set(received_paths) == {paths[1], paths[3]}
    assert set(out.keys()) == set(paths)
    for p in paths:
        assert p in out


def test_cpu_head_clone_does_not_mutate_cached_head(monkeypatch):
    """_cpu_head_clone 이 lru_cache 가 보관 중인 head 를 변형하지 않아야 한다."""
    if not hasattr(torch, "nn"):
        pytest.skip("torch.nn 없음")
    # 가짜 GPU 디바이스 시뮬레이션.
    monkeypatch.setattr(embedder, "_DEVICE", torch.device("cuda"))
    # 작은 linear 로 head 흉내. 실제 GPU 가 없어도 .to('cuda') 호출 직전엔
    # CPU 에 있는 module 이라 deepcopy 후 .to('cpu') 가 안전.
    head = torch.nn.Linear(8, 4)
    head.dims = (8, 8, 4)
    # CPU 클론 후 head 자체는 그대로여야 한다 — id 가 달라야 함.
    clone = embedder._cpu_head_clone(head)
    assert clone is not head
    # 원본의 weight 텐서가 그대로 동일 객체여야 (deepcopy 후 원본 미변형).
    assert id(head.weight) != id(clone.weight)


# ---------------------------------------------------------------------------
# OpenVINO 자동 설치 도우미
# ---------------------------------------------------------------------------
def test_openvino_installer_skips_when_already_installed(monkeypatch):
    from aoi_verification.app.learning import openvino_installer as _oi
    monkeypatch.setattr(_oi, "is_openvino_installed", lambda: True)
    assert _oi.should_offer_install(declined=False) is False


def test_openvino_installer_skips_when_declined(monkeypatch):
    from aoi_verification.app.learning import openvino_installer as _oi
    monkeypatch.setattr(_oi, "is_openvino_installed", lambda: False)
    monkeypatch.setattr(_oi, "is_intel_cpu", lambda: True)
    assert _oi.should_offer_install(declined=True) is False


def test_openvino_installer_skips_on_non_intel(monkeypatch):
    from aoi_verification.app.learning import openvino_installer as _oi
    monkeypatch.setattr(_oi, "is_openvino_installed", lambda: False)
    monkeypatch.setattr(_oi, "is_intel_cpu", lambda: False)
    assert _oi.should_offer_install(declined=False) is False


def test_openvino_installer_offers_when_intel_and_missing(monkeypatch):
    from aoi_verification.app.learning import openvino_installer as _oi
    monkeypatch.setattr(_oi, "is_openvino_installed", lambda: False)
    monkeypatch.setattr(_oi, "is_intel_cpu", lambda: True)
    assert _oi.should_offer_install(declined=False) is True


# ---------------------------------------------------------------------------
# 가속기 활용 (NPU/GPU) — 기본 모드에서도 CNN 자동 활성
# ---------------------------------------------------------------------------
def test_has_accelerator_true_when_gpu_device(monkeypatch):
    """torch device 가 cpu 가 아니면 has_accelerator() True."""
    monkeypatch.setattr(embedder, "_DEVICE", torch.device("cuda"))
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: False)
    assert embedder.has_accelerator() is True


def test_has_accelerator_true_when_openvino_available(monkeypatch):
    monkeypatch.setattr(embedder, "_DEVICE", torch.device("cpu"))
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: True)
    assert embedder.has_accelerator() is True


def test_has_accelerator_false_when_cpu_only(monkeypatch):
    monkeypatch.setattr(embedder, "_DEVICE", torch.device("cpu"))
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: False)
    assert embedder.has_accelerator() is False


def test_basic_mode_skips_cnn_regardless_of_accelerator(monkeypatch, tmp_path):
    """롤백: basic 모드는 가속기 유무와 관계없이 CNN 미실행.

    사용자 요청으로 NPU 자동 활성 로직을 rollback — basic 모드는 항상
    pHash/ORB/SSIM 만 사용 (이전 안정 동작 복귀).
    """
    monkeypatch.setattr(embedder, "is_available", lambda: True)
    monkeypatch.setattr(embedder, "get_active_mode", lambda: "basic")
    monkeypatch.setattr(embedder.registry, "BASIC", "basic")
    called = []
    monkeypatch.setattr(
        embedder, "_compute_embeddings_pytorch",
        lambda *a, **kw: called.append("nope") or {},
    )
    # 가속기 있어도 / 없어도 모두 동일 — basic 은 항상 skip.
    for accel in (True, False):
        called.clear()
        monkeypatch.setattr(embedder, "has_accelerator", lambda v=accel: v)
        out = embedder.compute_embeddings([tmp_path / "x.jpg"])
        assert called == [], f"basic + accel={accel} 는 PyTorch 경로 미호출"
        assert out == {}
