"""build.py — exe/포터블 빌드 스크립트 (배치 파일 대체).

한국어 Windows(cp949) 콘솔에서 ``.bat`` 의 한글이 깨지는 문제를 피하려고, 빌드를
**파이썬으로** 한다(Python 은 UTF-8 안전).  세 가지 배포 방식을 한 스크립트로 제공:

    python scripts/build.py online      # 작은 온라인 launcher exe (권장)
    python scripts/build.py portable    # 자체 포함 CPython 폴더(인터넷 없는 PC)
    python scripts/build.py windows     # 단독 exe(PyInstaller, 전부 동봉)

어디서 실행하든(더블클릭/터미널) 저장소 루트로 자동 이동한다.  VS Code 에서 이 파일을
열고 ‘Run Python File’ 을 눌러도 된다(인자 없으면 사용법 안내).

실제 빌드는 **Windows + 인터넷** 환경에서 한다(PyInstaller 는 크로스컴파일 불가).
순수 판단 로직(명령 구성·검증)은 부수효과 없이 분리해 헤드리스 테스트가 가능하다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

# 이 스크립트는 scripts/ 안에 있다 → 저장소 루트는 부모.
REPO_ROOT = Path(__file__).resolve().parent.parent
INTERNAL = REPO_ROOT / "scripts" / "internal"

# python-build-standalone 의 'install_only' Windows x86_64 (포터블 베이스 런타임).
# 404 면 https://github.com/astral-sh/python-build-standalone/releases 에서 최신
# install_only Windows x86_64 .tar.gz 링크로 교체.
PY_STANDALONE_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    "20250115/cpython-3.11.11+20250115-x86_64-pc-windows-msvc-install_only.tar.gz"
)


# ---------------------------------------------------------------------------
# 순수 로직 (테스트 대상) — 부수효과 없음
# ---------------------------------------------------------------------------
def venv_python(repo_root: Path) -> Path:
    """저장소 ``.venv`` 의 파이썬 실행 파일 경로(OS 별)."""
    if os.name == "nt":
        return repo_root / ".venv" / "Scripts" / "python.exe"
    return repo_root / ".venv" / "bin" / "python"


def pyinstaller_cmd(python_exe: str, spec: Path) -> List[str]:
    """PyInstaller 빌드 명령(spec 사용)."""
    return [str(python_exe), "-m", "PyInstaller", "--noconfirm", str(spec)]


def pip_install_cmd(python_exe: str, *args: str) -> List[str]:
    return [str(python_exe), "-m", "pip", "install", *args]


def guard_cmd(python_exe: str) -> List[str]:
    """회사 보안 정책 가드 실행 명령."""
    return [str(python_exe), str(INTERNAL / "verify_no_forbidden.py")]


def output_path(kind: str, repo_root: Path) -> Path:
    """빌드 종류별 산출물 경로(안내·테스트용)."""
    return {
        "online": repo_root / "dist" / "AOI_Verify_Online.exe",
        "windows": repo_root / "dist" / "AOI_Verify" / "AOI_Verify.exe",
        "portable": repo_root / "dist_portable",
    }[kind]


# ---------------------------------------------------------------------------
# 실행 헬퍼 — run 은 주입 가능(테스트는 가짜로 대체)
# ---------------------------------------------------------------------------
def _default_run(cmd: List[str], cwd: Optional[Path] = None) -> int:
    print(">>", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None)


def _ensure_venv(run: Callable, log: Callable) -> str:
    """저장소 .venv 를 준비하고 그 파이썬 경로를 돌려준다."""
    vpy = venv_python(REPO_ROOT)
    if not vpy.exists():
        log("[venv] creating .venv ...")
        if run([sys.executable, "-m", "venv", str(REPO_ROOT / ".venv")]) != 0:
            raise SystemExit("venv creation failed")
    run(pip_install_cmd(str(vpy), "--upgrade", "pip"))
    return str(vpy)


# ---------------------------------------------------------------------------
# 빌드 액션
# ---------------------------------------------------------------------------
def build_online(run: Callable = _default_run, log: Callable = print) -> int:
    """작은 온라인 launcher exe (앱/무거운 의존성 미포함, 첫 실행 시 인터넷 설치)."""
    vpy = _ensure_venv(run, log)
    if run(pip_install_cmd(vpy, "pyinstaller>=6")) != 0:
        raise SystemExit("pyinstaller install failed")
    if run(guard_cmd(vpy)) != 0:
        raise SystemExit("security guard failed")
    log("[build] online launcher (onefile, no app/deps bundled) ...")
    rc = run(pyinstaller_cmd(vpy, INTERNAL / "online.spec"), REPO_ROOT)
    if rc == 0:
        log("[done] " + str(output_path("online", REPO_ROOT)))
        log("       Ship this single file. First run downloads app+packages "
            "into %LOCALAPPDATA%\\AOI Recipe Verification.")
    return rc


def build_windows(run: Callable = _default_run, log: Callable = print) -> int:
    """단독 exe(PyInstaller, 전부 동봉)."""
    vpy = _ensure_venv(run, log)
    if run(pip_install_cmd(vpy, "-r", str(REPO_ROOT / "requirements.txt"))) != 0:
        raise SystemExit("requirements install failed")
    if run(pip_install_cmd(vpy, "pyinstaller>=6")) != 0:
        raise SystemExit("pyinstaller install failed")
    if run(guard_cmd(vpy)) != 0:
        raise SystemExit("security guard failed")
    log("[build] standalone exe (onedir, everything bundled) ...")
    rc = run(pyinstaller_cmd(vpy, INTERNAL / "aoi_verification.spec"), REPO_ROOT)
    if rc == 0:
        log("[done] " + str(output_path("windows", REPO_ROOT)))
        log("       Ship the whole dist\\AOI_Verify folder (zip).")
        log("")
        vrc = verify_windows(REPO_ROOT, log)
        if vrc != 0:
            log("[주의] 빌드는 완료됐지만 검증에서 누락 항목이 발견되었습니다.")
            return vrc
    return rc


def _load_portable_impl():
    """scripts/internal/portable_build.py 를 패키지 설정 없이 직접 로드."""
    import importlib.util as _u
    path = INTERNAL / "portable_build.py"
    spec = _u.spec_from_file_location("portable_build", str(path))
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_portable(run: Callable = _default_run, log: Callable = print) -> int:
    """자체 포함 CPython 폴더 빌드(인터넷 없는 PC 용).  네이티브 다운로드/압축은
    portable_build.run_build 에 위임 — 무거워서 실제 실행은 Windows 에서만."""
    impl = _load_portable_impl()
    return impl.run_build(REPO_ROOT, PY_STANDALONE_URL, run=run, log=log)


def verify_windows(repo_root: Path = REPO_ROOT, log: Callable = print) -> int:
    """windows 빌드 산출물(dist/AOI_Verify/)이 정상인지 검증한다.

    빌드 직후 자동 실행되며, ``python scripts/build.py verify`` 로도 수동 호출 가능."""
    dist = repo_root / "dist" / "AOI_Verify"
    exe = dist / "AOI_Verify.exe"
    internal = dist / "_internal"

    ok = True
    total = 0
    passed = 0

    def _check(condition: bool, label: str):
        nonlocal ok, total, passed
        total += 1
        if condition:
            passed += 1
            log(f"  [OK] {label}")
        else:
            ok = False
            log(f"  [!!] {label}")

    log("[verify] windows 빌드 산출물 검증 ...")

    # 1) exe 존재
    _check(exe.is_file(), f"AOI_Verify.exe 존재 ({exe})")

    # _internal 폴더 (PyInstaller onedir 의 번들 루트)
    bundle = internal if internal.is_dir() else dist
    _check(internal.is_dir(), "_internal/ 폴더 존재")

    # 2) 리소스 파일
    qss = bundle / "aoi_verification" / "app" / "ui" / "style.qss"
    _check(qss.is_file(), "style.qss (스타일시트)")
    xlsx = bundle / "양식.xlsx"
    _check(xlsx.is_file(), "양식.xlsx (엑셀 템플릿)")

    # 3) 앱 패키지
    app_pkg = bundle / "aoi_verification"
    _check(app_pkg.is_dir(), "aoi_verification/ 패키지")

    # 4) 핵심 의존성 — PyInstaller 가 수집한 패키지 폴더/pyd/dll 존재 확인
    _core_deps = {
        "PyQt6": ["PyQt6"],
        "cv2 (OpenCV)": ["cv2"],
        "numpy": ["numpy"],
        "PIL (Pillow)": ["PIL"],
        "openpyxl": ["openpyxl"],
    }
    _optional_deps = {
        "torch": ["torch"],
        "openvino": ["openvino"],
    }
    for label, candidates in _core_deps.items():
        found = any((bundle / c).is_dir() for c in candidates)
        _check(found, f"{label} (필수)")

    for label, candidates in _optional_deps.items():
        found = any((bundle / c).is_dir() for c in candidates)
        if found:
            log(f"  [OK] {label} (고효율 모드)")
        else:
            log(f"  [--] {label} (미포함 — INCLUDE_EFFICIENCY=False 이면 정상)")

    # 5) 용량 확인 (너무 작으면 빌드 불완전)
    if dist.is_dir():
        size_mb = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file()) / (1024 * 1024)
        _check(size_mb > 100, f"총 용량 {size_mb:.0f} MB (100 MB 이상이어야 정상)")

    log(f"[verify] 결과: {passed}/{total} 통과" +
        (" — 빌드 정상!" if ok else " — 위 [!!] 항목을 확인하세요."))
    return 0 if ok else 1


_ACTIONS = {
    "online": build_online,
    "windows": build_windows,
    "portable": build_portable,
    "verify": lambda run=_default_run, log=print: verify_windows(REPO_ROOT, log),
}


def _usage() -> str:
    return (
        "사용법: python scripts/build.py <online|portable|windows|verify>\n"
        "  online    작은 온라인 launcher exe (권장) — 첫 실행 시 인터넷으로 앱/패키지 설치\n"
        "  portable  자체 포함 CPython 폴더 (인터넷 없는 PC)\n"
        "  windows   단독 exe (PyInstaller, 전부 동봉)\n"
        "  verify    windows 빌드 산출물 검증 (빌드 후 자동 실행됨)\n"
        "예) python scripts/build.py online")


_MENU = [("online", "작은 온라인 launcher exe (권장)"),
         ("portable", "자체 포함 CPython 폴더 (인터넷 없는 PC)"),
         ("windows", "단독 exe (전부 동봉)"),
         ("verify", "windows 빌드 산출물 검증")]


def _prompt_kind(input_fn=input) -> Optional[str]:
    """인자 없이 실행(예: VS Code ▶)했을 때 번호로 빌드 종류를 고르게 한다.

    대화형 입력이 불가하면 None 을 돌려준다(=사용법만 출력)."""
    print("어떤 빌드를 만들까요? 번호를 입력하세요 (취소: Enter):")
    for i, (k, desc) in enumerate(_MENU, start=1):
        print(f"  {i}) {k:9s} {desc}")
    try:
        sel = input_fn("선택 [1-3]: ").strip()
    except (EOFError, OSError):
        return None
    if not sel:
        return None
    if sel.isdigit() and 1 <= int(sel) <= len(_MENU):
        return _MENU[int(sel) - 1][0]
    if sel in _ACTIONS:                      # 'online' 처럼 이름을 직접 입력해도 허용
        return sel
    print("잘못된 선택:", sel)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-h", "--help"):
        print(_usage())
        return 0
    if not argv:
        # 인자 없이 실행(VS Code '▶ Run Python File' 등) → 대화형 메뉴로 선택.
        if not sys.stdin or not sys.stdin.isatty():
            print(_usage())
            return 0
        kind = _prompt_kind()
        if kind is None:
            print("취소되었습니다.")
            return 0
        argv = [kind]
    kind = argv[0]
    action = _ACTIONS.get(kind)
    if action is None:
        print("알 수 없는 빌드 종류:", kind)
        print(_usage())
        return 2
    if os.name != "nt":
        print("[주의] 실제 exe/포터블 빌드는 Windows 에서만 동작합니다 "
              "(PyInstaller 크로스컴파일 불가). 현재 OS 에서는 명령만 확인됩니다.")
    try:
        return action()
    except SystemExit as exc:
        print("[실패]", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
