"""runtime/ir/ 를 만든다 — 백본을 OpenVINO IR 로 굽는 **1회성** 스크립트.

앱은 추론을 전부 OpenVINO 로 하고, torch 가 필요한 곳은 백본을 IR 로 바꾸는 이 변환
하나뿐이다.  그래서 결과만 저장소에 커밋해 두고 배포본에는 torch 를 넣지 않는다.

    python scripts/internal/make_ir.py        # → runtime/ir/*.xml, *.bin (커밋할 것)

**언제 돌리나**: 백본을 바꿀 때(그리고 아직 한 번도 안 만들었을 때)뿐이다.  평소 빌드는
커밋된 IR 을 그대로 복사한다(`portable_build._place_ir`).

**필요한 것** (이 스크립트 전용 — `requirements.txt` 에는 넣지 않는다):

    pip install torch torchvision openvino

가중치는 ``download.pytorch.org`` 에서 받는다.  그 호스트가 막힌 환경에서는 만들 수
없으니, 접속되는 PC(예: 빌드 PC)에서 돌려 결과를 커밋하면 된다.

★ 두 가지를 절대 바꾸지 마라 — 둘 다 임베딩 값을 바꾼다("정확도는 절대 깨지 않는다"):
  · ``compress_to_fp16=False`` — ``ov.save_model`` 의 기본값은 **True** 다.  그냥 저장하면
    FP32 가중치가 FP16 으로 반올림된다.
  · ``.eval()`` — BatchNorm 폴딩.  빼면 GPU 결과가 틀어진다.
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):        # Windows cp949 콘솔 대비
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parents[2]

# 앱이 쓰는 입력 해상도.  IR 은 모델당 1개면 되고, 배치·해상도는 런타임 reshape 로
# 맞춘다(`embedder_openvino._force_static_shape`) — 여기 값은 변환 시드일 뿐이다.
INPUT_PX = 256


def _models() -> tuple:
    """구울 백본 목록 — 빌드가 기대하는 것과 **같은 이름**이어야 한다."""
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "internal"))
    import importlib.util as u
    spec = u.spec_from_file_location(
        "portable_build", str(REPO_ROOT / "scripts" / "internal" / "portable_build.py"))
    mod = u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.IR_MODELS


def build_one(kind: str, out_dir: Path) -> Path:
    import openvino as ov
    import torch
    from torchvision import models as m

    if kind != "mobilenet_v3_small":
        raise SystemExit(f"모르는 백본: {kind} — 이 스크립트에 변환 코드를 추가하세요.")
    backbone = m.mobilenet_v3_small(
        weights=m.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    backbone.classifier = torch.nn.Identity()    # raw 임베딩(576-d)
    backbone.eval()                              # ★ BatchNorm 폴딩
    ov_model = ov.convert_model(
        backbone, example_input=torch.randn(1, 3, INPUT_PX, INPUT_PX))
    xml = out_dir / f"{kind}.xml"
    ov.save_model(ov_model, str(xml), compress_to_fp16=False)   # ★ FP32 유지
    return xml


def main() -> int:
    try:
        import openvino  # noqa: F401
        import torch     # noqa: F401
        import torchvision  # noqa: F401
    except ImportError as exc:
        print(f"[실패] {exc}")
        print("       pip install torch torchvision openvino  로 설치한 뒤 다시 "
              "실행하세요.")
        print("       (이 셋은 이 스크립트 전용입니다 — 배포본에는 들어가지 않습니다.)")
        return 1

    out_dir = REPO_ROOT / "runtime" / "ir"
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind in _models():
        print(f"[변환] {kind} ...")
        xml = build_one(kind, out_dir)
        mb = (xml.stat().st_size + xml.with_suffix(".bin").stat().st_size) / 1024 ** 2
        print(f"       {xml.name} + {xml.with_suffix('.bin').name}  ({mb:.1f} MB)")

    print()
    print(f"[완료] {out_dir}")
    print("       이 파일들을 **커밋**하세요 — 빌드와 온라인 배포가 이걸 그대로 씁니다.")
    print("         git add runtime/ir && git commit -m \"백본 IR 갱신\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
