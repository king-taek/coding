"""Intel NPU 컴파일 진단/정적 shape 회귀 테스트.

NPU 가 감지되나 '대기'에 머무는 원인(동적 shape 로 NPU 컴파일 실패)을 줄이기
위한 변경 검증:
- `_force_static_shape` 가 입력을 정적으로 reshape(예외는 삼킴).
- `compile_diagnostics()` 가 컴파일 성공/실패를 요약(상태바 툴팁용).
- `build_units` 가 컴파일 실패 시에도 크래시 없이 해당 유닛만 비활성화.
"""

from __future__ import annotations

import pytest

from aoi_verification.app.learning import embedder_openvino as ov
from aoi_verification.app.workers import efficiency_matcher as eff


@pytest.fixture(autouse=True)
def _clean_diag():
    ov._compiled_units.clear()
    ov._compile_errors.clear()
    yield
    ov._compiled_units.clear()
    ov._compile_errors.clear()


def test_force_static_shape_reshapes_to_batch1():
    captured = {}

    class FakeModel:
        def reshape(self, shape):
            captured["shape"] = shape

    ov._force_static_shape(FakeModel())
    assert captured["shape"] == [1, 3, ov._INPUT_PX, ov._INPUT_PX]


def test_force_static_shape_swallows_errors():
    class Raising:
        def reshape(self, shape):
            raise RuntimeError("이미 정적이거나 미지원")

    # 예외가 새어나오면 안 된다(컴파일 흐름을 막지 않도록).
    ov._force_static_shape(Raising())


def test_compile_diagnostics_structure_empty():
    diag = ov.compile_diagnostics()
    assert diag == {"compiled": [], "errors": {}}


def test_compile_diagnostics_reports_success_and_failure():
    ov._compiled_units[(ov.MODEL_MOBILENET_V3, "GPU")] = "Intel(R) Graphics"
    ov._compile_errors[(ov.MODEL_RESNET18, "NPU")] = "RuntimeError: dynamic shape"
    diag = ov.compile_diagnostics()
    assert diag["compiled"] == ["GPU"]
    assert diag["errors"] == {"NPU": "RuntimeError: dynamic shape"}


def test_build_units_drops_npu_on_compile_failure_without_crash(monkeypatch):
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU", "NPU"])

    def compile(mk, dev, batch=1):
        return (object(), "gpu") if dev == "GPU" else None  # NPU 실패

    monkeypatch.setattr(eff._ov, "compile_model_on", compile)
    units = eff.build_units(cfg=None, threshold=0.5)
    assert [getattr(u, "tag", "?") for u in units] == ["cpu", "gpu"]
