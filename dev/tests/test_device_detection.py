"""가속 장치 감지 — OpenVINO(Intel GPU) 경로와 설치 도우미.

torch 는 배포본에 없다(백본 IR 을 빌드 때 만들어 동봉한다).  그래서 예전의
CUDA/XPU/MPS/DirectML 감지 테스트도 함께 사라졌다 — 검증할 코드가 없다.
여기 남은 것은 **openvino 만으로 판정되는** 부분이라 무거운 의존성 없이 돈다.
"""

from __future__ import annotations

from aoi_verification.app.learning import embedder_openvino


def test_openvino_gpu_label_shown_when_available(monkeypatch):
    """Intel GPU(OpenVINO) 가 잡히면 상태 문자열에 GPU 가 들어간다."""
    monkeypatch.setattr(embedder_openvino, "is_available", lambda: True)
    monkeypatch.setattr(embedder_openvino, "_list_ov_devices",
                        lambda: ["GPU", "CPU"])
    assert "GPU" in embedder_openvino.device_label()


def test_openvino_unavailable_when_module_missing():
    """openvino 가 설치 안 됐을 때 is_available()=False, target=None."""
    if embedder_openvino._HAS_OPENVINO:
        import pytest
        pytest.skip("openvino 가 설치되어 있어 이 테스트는 무의미")
    assert embedder_openvino.is_available() is False
    assert embedder_openvino.target_device() is None
    assert embedder_openvino.device_label() == ""
    # ★ 예전엔 torch 미설치만으로도 여기가 빈 리스트였다.  torch 게이트를 걷어낸 뒤
    #   openvino 유무만 보는지 확인한다 — 게이트가 남아 있으면 배포본(=torch 없음)에서
    #   GPU 가속이 통째로 꺼진다.
    assert embedder_openvino.available_units() == []


def test_accelerator_presence_reports_reason_without_openvino():
    """GPU 가 안 잡힐 때 '왜' 를 돌려줘야 한다(상태 툴팁)."""
    if embedder_openvino._HAS_OPENVINO:
        import pytest
        pytest.skip("openvino 가 설치되어 있어 이 테스트는 무의미")
    info = embedder_openvino.accelerator_presence()
    assert info["GPU"] is False
    assert info["reason"]


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


def test_basic_path_never_computes_cnn_embeddings():
    """고전 채점의 CNN 항은 비활성이다 — 켜지면 채점 결과가 바뀐다.

    학습 기능이 사라져 활성화 수단이 없다(`similarity/cnn_embed.py`).  여기가
    True 로 돌아서면 phash/ORB/SSIM 만 쓰던 기존 점수와 달라지므로 회귀 가드로 둔다."""
    from aoi_verification.app.similarity import cnn_embed
    assert cnn_embed.is_available() is False
    assert cnn_embed.compute_embedding(__file__) is None
