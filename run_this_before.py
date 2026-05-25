"""run_this_before.py — main.py 실행 전 ‘한 번’ 실행하는 환경 준비 스크립트.

사용법
------
VS Code 에서 이 파일을 열고 ‘Run Python File in Terminal’ (또는 F5) 을 누르세요.
다음이 자동으로 수행됩니다:

  1) Python 버전 확인 (>= 3.9)
  2) requirements.txt 의 모든 패키지 설치 (pip)
  3) 핵심 의존성 import 검증
  4) ``~/.aoi_verification_cache`` 하위 폴더 사전 생성

전부 통과하면 마지막에 ‘준비 완료 → main.py 를 실행하세요’ 메시지가 뜹니다.
이미 설치된 패키지는 pip 가 ‘Requirement already satisfied’ 로 빠르게 넘어가므로
여러 번 실행해도 안전합니다.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# 한글 출력이 깨지지 않도록 stdout 을 UTF-8 로. (Windows cp949 콘솔 대비)
# ---------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


MIN_PYTHON = (3, 9)
HERE = Path(__file__).resolve().parent
REQ_FILE = HERE / "requirements.txt"
CACHE_ROOT = Path.home() / ".aoi_verification_cache"
CACHE_SUBDIRS = (
    "thumbs", "mid", "features", "session",
    "models", "training_data", "evaluations",
)


# ---------------------------------------------------------------------------
# 출력 헬퍼 (이모지 없이 [OK]/[FAIL]/[INFO] 형태)
# ---------------------------------------------------------------------------
def _hr(ch: str = "=", width: int = 64) -> str:
    return ch * width


def _step(n: int, total: int, label: str) -> None:
    print(f"\n[{n}/{total}] {label}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


# ---------------------------------------------------------------------------
# 1) Python 버전
# ---------------------------------------------------------------------------
def check_python() -> None:
    cur = sys.version_info
    if (cur.major, cur.minor) < MIN_PYTHON:
        _fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ 가 필요합니다. "
            f"현재: {sys.version.split()[0]}"
        )
        sys.exit(1)
    _ok(f"Python {sys.version.split()[0]}  ({sys.executable})")


# ---------------------------------------------------------------------------
# 2) pip install -r requirements.txt
# ---------------------------------------------------------------------------
def install_requirements() -> None:
    if not REQ_FILE.exists():
        _fail(f"requirements.txt 를 찾을 수 없습니다: {REQ_FILE}")
        sys.exit(1)

    _info(f"실행: {sys.executable} -m pip install -r {REQ_FILE.name}")
    _info("이미 설치된 패키지는 'Requirement already satisfied' 로 빠르게 넘어갑니다.")

    # pip 출력이 그대로 보이도록 capture_output 미사용 — 진행률을 실시간으로 봄.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE)],
    )
    if result.returncode != 0:
        _fail(
            "pip install 가 실패했습니다. 네트워크/권한/Python 환경을 확인하세요.\n"
            "         (회사 프록시 환경이라면 'pip config set global.proxy <url>' 가 필요할 수 있습니다.)"
        )
        sys.exit(result.returncode)
    _ok("requirements.txt 의 모든 패키지 설치 완료")


# ---------------------------------------------------------------------------
# 3) 핵심 의존성 import 검증
# ---------------------------------------------------------------------------
def verify_imports() -> None:
    required: list[tuple[str, str]] = [
        ("PyQt6.QtWidgets", "PyQt6"),
        ("PIL", "Pillow"),
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("skimage", "scikit-image"),
        ("imagehash", "imagehash"),
        ("openpyxl", "openpyxl"),
        ("psutil", "psutil"),
        ("openvino", "openvino (Intel GPU 가속 — 필수)"),
    ]
    optional: list[tuple[str, str]] = [
        ("torch", "torch (학습 기능 — 옵션)"),
        ("torchvision", "torchvision (학습 기능 — 옵션)"),
    ]

    failed: list[str] = []
    for mod, label in required:
        try:
            importlib.import_module(mod)
            _ok(label)
        except Exception as exc:
            _fail(f"{label} import 실패: {exc}")
            failed.append(label)

    for mod, label in optional:
        try:
            importlib.import_module(mod)
            _ok(label)
        except Exception:
            _info(f"{label} 미설치 — 기본 탐지 모드로만 동작합니다.")

    if failed:
        _fail(f"누락/실패한 필수 모듈: {', '.join(failed)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# 4) 캐시 디렉토리 사전 생성
# ---------------------------------------------------------------------------
def prepare_cache_dir() -> None:
    for sub in CACHE_SUBDIRS:
        (CACHE_ROOT / sub).mkdir(parents=True, exist_ok=True)
    _ok(f"캐시 폴더 준비됨: {CACHE_ROOT}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    print(_hr())
    print("AOI 검증 프로그램 — 환경 준비 스크립트")
    print(_hr())

    total = 4
    _step(1, total, "Python 버전 확인")
    check_python()

    _step(2, total, "requirements.txt 의 패키지 설치 (pip)")
    install_requirements()

    _step(3, total, "핵심 의존성 import 검증")
    verify_imports()

    _step(4, total, "캐시 디렉토리 사전 생성")
    prepare_cache_dir()

    print()
    print(_hr())
    print("준비 완료. 이제 VS Code 에서 main.py 를 열고")
    print("'Run Python File in Terminal' (또는 F5) 을 누르면 GUI 가 뜹니다.")
    print(_hr())
    return 0


if __name__ == "__main__":
    sys.exit(main())
