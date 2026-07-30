"""백본 IR 동봉 — 빌드 배선과 **수치 동일성** 검증.

torch 를 배포본에서 빼기 위해, 예전에 런타임에서 하던 ``ov.convert_model`` 을
빌드 시점으로 옮기고 IR 을 동봉한다.  여기서 지켜야 하는 계약:

1. 빌드가 IR 을 실제로 만들고, 검증이 그것을 본다 (무거운 의존성 없이 확인).
2. 저장했다 읽은 IR 이 in-memory 모델과 **같은 임베딩**을 낸다
   (torch·openvino 가 있는 환경에서만 — 정확도는 절대 깨지 않는다).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _portable():
    spec = importlib.util.spec_from_file_location(
        "portable_build", str(REPO_ROOT / "scripts" / "internal" / "portable_build.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 1) 빌드 배선 — 순수 로직이라 무거운 의존성 없이 돈다
# ---------------------------------------------------------------------------
def test_missing_ir_requires_both_xml_and_bin(tmp_path):
    p = _portable()
    assert p.missing_ir(tmp_path) == list(p.IR_MODELS)      # 아무것도 없음
    d = p.ir_dir(tmp_path)
    d.mkdir(parents=True)
    for kind in p.IR_MODELS:
        (d / f"{kind}.xml").write_text("<net/>", encoding="utf-8")
    # .xml 만 있으면 여전히 '빠진 것' 이다 — 가중치 없는 모델은 틀린 값을 낸다.
    assert p.missing_ir(tmp_path) == list(p.IR_MODELS)
    for kind in p.IR_MODELS:
        (d / f"{kind}.bin").write_bytes(b"x")
    assert p.missing_ir(tmp_path) == []


def test_ir_build_source_never_compresses_to_fp16():
    """★ ``ov.save_model`` 의 ``compress_to_fp16`` 기본값은 True 다.

    그냥 저장하면 FP32 가중치가 FP16 으로 반올림돼 지금까지의 임베딩과 값이 달라진다.
    용량이 절반이 되는 유혹이 있는 자리라 회귀 가드로 못 박는다."""
    src = _portable()._IR_BUILD_SRC
    assert "compress_to_fp16=False" in src
    assert ".eval()" in src, "BatchNorm 폴딩을 빼면 GPU 결과가 틀어진다"
    # 빌드 전용 트리는 sys.path 뒤에 붙어야 한다 — 앞에 붙이면 거기 딸려 온 numpy 가
    # 번들 것을 가려 openvino 와 ABI 가 어긋난다.
    assert "sys.path.append(tmp)" in src
    assert "sys.path.insert" not in src


def test_build_only_torch_is_not_a_runtime_requirement():
    """torch 는 **빌드 전용**이다 — requirements.txt 에 들어가면 목적이 무너진다."""
    p = _portable()
    assert any("torch" in r for r in p._BUILD_ONLY_REQS)
    req = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    from aoi_verification.app.utils import bootstrap
    names = [ln.split(">")[0].split("=")[0].strip().lower()
             for ln in bootstrap.req_lines(req)]
    assert "torch" not in names and "torchvision" not in names


def test_ir_temp_tree_is_cleaned_and_never_shipped(tmp_path, monkeypatch):
    """빌드 전용 torch 트리(수백 MB)는 실패해도 반드시 지워져야 한다."""
    p = _portable()
    tmp = tmp_path / p.IR_TMP_DIRNAME

    def fake_run(cmd, cwd=None):
        tmp.mkdir(parents=True, exist_ok=True)      # pip --target 흉내
        (tmp / "torch").mkdir(exist_ok=True)
        return 1                                    # 설치 실패
    assert p._build_ir(tmp_path, Path("py"), fake_run, lambda *a: None) != 0
    assert not tmp.exists(), "실패 경로에서 임시 트리가 남았다"


# ---------------------------------------------------------------------------
# 2) 수치 동일성 — 이 작업의 핵심 계약
# ---------------------------------------------------------------------------
def test_saved_ir_matches_in_memory_conversion(tmp_path):
    """★ 정확도 가드: 저장→로드한 IR 이 변환 직후 모델과 같은 값을 내야 한다.

    다르면 사용자의 매칭 결과가 조용히 바뀐다.  torch·openvino 가 모두 있는
    환경에서만 의미가 있어 게이트한다(개발 PC·빌드 PC)."""
    torch = pytest.importorskip("torch")
    ov = pytest.importorskip("openvino")
    models = pytest.importorskip("torchvision.models")

    import numpy as np

    P = 64                                   # 회귀 확인용이라 작게 — 그래프는 동일하다
    backbone = models.mobilenet_v3_small(weights=None)
    backbone.classifier = torch.nn.Identity()
    backbone.eval()
    example = torch.randn(1, 3, P, P)

    in_memory = ov.convert_model(backbone, example_input=example)
    xml = tmp_path / "m.xml"
    ov.save_model(in_memory, str(xml), compress_to_fp16=False)
    loaded = ov.Core().read_model(str(xml))

    x = np.random.RandomState(0).rand(1, 3, P, P).astype(np.float32)
    core = ov.Core()
    a = list(core.compile_model(in_memory, "CPU").create_infer_request()
             .infer({0: x}).values())[0].ravel()
    b = list(core.compile_model(loaded, "CPU").create_infer_request()
             .infer({0: x}).values())[0].ravel()

    cos = float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12))
    assert cos >= 1 - 1e-6, f"IR 왕복에서 임베딩이 달라졌다 (cos={cos})"


def test_reshape_after_read_model_still_works(tmp_path):
    """IR 은 모델당 1개면 된다 — 배치/해상도는 ``reshape`` 로 맞춘다.

    이 전제가 깨지면 배치 크기마다 IR 을 따로 구워야 한다."""
    torch = pytest.importorskip("torch")
    ov = pytest.importorskip("openvino")
    models = pytest.importorskip("torchvision.models")

    backbone = models.mobilenet_v3_small(weights=None)
    backbone.classifier = torch.nn.Identity()
    backbone.eval()
    xml = tmp_path / "m.xml"
    ov.save_model(ov.convert_model(backbone, example_input=torch.randn(1, 3, 64, 64)),
                  str(xml), compress_to_fp16=False)

    m = ov.Core().read_model(str(xml))
    m.reshape([16, 3, 96, 96])               # 운영 배치(16) + 다른 해상도
    assert list(m.inputs[0].partial_shape.to_shape()) == [16, 3, 96, 96]
