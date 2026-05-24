"""고효율 모드 — 유닛 선택(폴백) + EngineMode 단위 테스트.

가용 장치/컴파일 성공 여부에 따라 ``build_units`` 가 올바른 유닛 집합을
구성하는지 검증한다.  CPU 는 항상 포함, GPU/NPU 는 컴파일 성공 시만."""

from __future__ import annotations

from aoi_verification.app.utils.prefs import EngineMode
from aoi_verification.app.workers import efficiency_matcher as eff


def _tags(units):
    return [getattr(u, "tag", "?") for u in units]


def test_enginemode_efficiency():
    assert EngineMode.is_efficiency("efficiency") is True
    assert EngineMode.is_efficiency("fast") is False
    assert EngineMode.is_efficiency("basic") is False
    assert "efficiency" in EngineMode.ALL


def test_cpu_only_when_no_accel(monkeypatch):
    monkeypatch.setattr(eff._ov, "available_units", lambda: [])
    assert eff.has_accel_units() is False
    units = eff.build_units(cfg=None, threshold=0.5)
    assert _tags(units) == ["cpu"]


def test_gpu_added_when_available(monkeypatch):
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on",
                        lambda mk, dev: (object(), "Intel GPU"))
    units = eff.build_units(cfg=None, threshold=0.5)
    assert _tags(units) == ["cpu", "gpu"]


def test_gpu_and_npu_added(monkeypatch):
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU", "NPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on",
                        lambda mk, dev: (object(), "dev"))
    units = eff.build_units(cfg=None, threshold=0.5)
    assert _tags(units) == ["cpu", "gpu", "npu"]


def test_unit_dropped_when_compile_fails(monkeypatch):
    """장치는 보이지만 컴파일 실패(None) → 그 유닛은 빠지고 CPU 만."""
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU", "NPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on", lambda mk, dev: None)
    units = eff.build_units(cfg=None, threshold=0.5)
    assert _tags(units) == ["cpu"]


def test_npu_only_when_gpu_compile_fails(monkeypatch):
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU", "NPU"])

    def compile(mk, dev):
        return None if dev == "GPU" else (object(), "npu")

    monkeypatch.setattr(eff._ov, "compile_model_on", compile)
    units = eff.build_units(cfg=None, threshold=0.5)
    assert _tags(units) == ["cpu", "npu"]
