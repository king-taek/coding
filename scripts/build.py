"""build.py — exe/포터블 빌드 스크립트 (배치 파일 대체).

한국어 Windows(cp949) 콘솔에서 ``.bat`` 의 한글이 깨지는 문제를 피하려고, 빌드를
**파이썬으로** 한다(Python 은 UTF-8 안전).  세 가지 배포 방식을 한 스크립트로 제공:

    python scripts/build.py exe         # exe + app 폴더 (권장)
    python scripts/build.py portable    # 자체 포함 CPython 폴더(.bat 실행)
    python scripts/build.py online      # 작은 온라인 launcher exe (PyPI 접속 필요)

``exe`` 는 **얇은 런처 exe + loose 한 app\\ 폴더** 다.  앱 코드를 exe 안에 넣지 않는 것이
핵심 — 옛 단독 exe(onedir) 는 앱을 exe 안 PYZ 에 넣어서, 자동 업데이트가 디스크에 새
파일을 써도 실행 중인 파이썬이 그걸 읽지 않아 '성공했다는데 안 바뀌는' 상태가 됐다.

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


def pyinstaller_cmd(python_exe: str, spec: Path,
                    distpath: Optional[Path] = None) -> List[str]:
    """PyInstaller 빌드 명령(spec 사용).  ``distpath`` 를 주면 산출물 위치를 지정한다."""
    cmd = [str(python_exe), "-m", "PyInstaller", "--noconfirm"]
    if distpath is not None:
        cmd += ["--distpath", str(distpath)]
    return cmd + [str(spec)]


def pip_install_cmd(python_exe: str, *args: str) -> List[str]:
    return [str(python_exe), "-m", "pip", "install", *args]


def guard_cmd(python_exe: str) -> List[str]:
    """회사 보안 정책 가드 실행 명령."""
    return [str(python_exe), str(INTERNAL / "verify_no_forbidden.py")]


def output_path(kind: str, repo_root: Path) -> Path:
    """빌드 종류별 산출물 경로(안내·테스트용)."""
    return {
        "online": repo_root / "dist" / "AOI_Verify_Online.exe",
        "exe": repo_root / "dist" / "AOI_Verify",
        "portable": repo_root / "dist_portable",
    }[kind]


# 'exe + app 폴더' 산출물 폴더 이름(저장소 루트 기준) — portable_build 에 넘긴다.
EXE_OUT_DIRNAME = "dist/AOI_Verify"


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


def build_exe(run: Callable = _default_run, log: Callable = print) -> int:
    """exe + app 폴더 — 파이썬 미설치 PC 용이고 **자동 업데이트가 완전히 동작**한다.

    얇은 런처 exe 하나만 얼리고(앱 코드 0줄), 앱 소스·리소스는 ``app\\`` 에 loose 로 둔다.
    옛 단독 exe(onedir) 는 앱을 exe 안 PYZ 에 넣어서 업데이트가 조용히 무시됐다 —
    그 구조를 되풀이하지 않기 위한 형태다."""
    out = REPO_ROOT / EXE_OUT_DIRNAME
    # ① 런처 exe 먼저 — 여기서 실패하면 무거운 런타임 다운로드 전에 빨리 끝난다.
    vpy = _ensure_venv(run, log)
    if run(pip_install_cmd(vpy, "pyinstaller>=6")) != 0:
        raise SystemExit("pyinstaller install failed")
    if run(guard_cmd(vpy)) != 0:
        raise SystemExit("security guard failed")
    log("[build] thin launcher exe (no app code inside) ...")
    if run(pyinstaller_cmd(vpy, INTERNAL / "exe_launcher.spec", out), REPO_ROOT) != 0:
        raise SystemExit("launcher build failed")

    # ② 번들 런타임 + 앱 소스 — 포터블 빌드와 레이아웃이 같으므로 그대로 재사용한다.
    impl = _load_portable_impl()
    rc = impl.run_build(REPO_ROOT, PY_STANDALONE_URL, run=run, log=log,
                        out_dirname=EXE_OUT_DIRNAME,
                        bats=("run_aoi.bat", "run_aoi_debug.bat"),
                        torch_home=True)
    if rc != 0:
        return rc
    log("[done] " + str(output_path("exe", REPO_ROOT)))
    log("       Ship the whole dist\\AOI_Verify folder (zip).")
    log("")
    vrc = verify_exe(REPO_ROOT, log, run=run)
    if vrc != 0:
        log("[주의] 빌드는 완료됐지만 검증에서 누락 항목이 발견되었습니다.")
    return vrc


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


def _dir_size_mb(d: Path) -> float:
    if not d.is_dir():
        return 0.0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)


def verify_checks(out: Path) -> List[tuple]:
    """'exe + app 폴더' 산출물 검사 목록 ``[(통과여부, 라벨), …]`` — 순수(테스트 대상).

    옛 검증은 ``_internal/aoi_verification`` 이 '폴더로 존재하는가' 만 봤는데, 그 폴더는
    리소스 파일(style.qss·assets) 때문에 **앱 코드가 하나도 없어도 만들어진다** — 즉
    공허하게 통과했다.  여기서는 '통과했다면 실제로 그렇다' 가 성립하는 것만 본다."""
    exe = out / "AOI_Verify.exe"
    app = out / "app"
    pkg = app / "aoi_verification"
    checks: List[tuple] = []

    checks.append((exe.is_file(), f"AOI_Verify.exe 존재 ({exe})"))
    # ★ 핵심 회귀 가드 — _internal/ 이 있으면 앱을 다시 exe 안에 얼렸다는 지문이다.
    #   (그게 자동 업데이트가 조용히 무시되던 옛 사고의 원인)
    checks.append((not (out / "_internal").exists(),
                   "_internal/ 없음 (앱이 exe 안에 얼려지지 않았다는 증거)"))
    exe_mb = (exe.stat().st_size / (1024 * 1024)) if exe.is_file() else 0.0
    checks.append((0 < exe_mb < 30,
                   f"런처 exe 용량 {exe_mb:.1f} MB (30 MB 미만 — 앱/PyQt6 미포함)"))

    checks.append(((out / "python" / "python.exe").is_file(),
                   "python/python.exe (번들 런타임)"))
    checks.append(((out / "python" / "pythonw.exe").is_file(),
                   "python/pythonw.exe (런처가 실행하는 파일)"))

    for rel in ("main.py", "requirements.txt", "양식.xlsx"):
        checks.append(((app / rel).is_file(), f"app/{rel}"))
    checks.append(((pkg / "app" / "ui" / "style.qss").is_file(),
                   "app/aoi_verification/app/ui/style.qss"))
    checks.append(((pkg / "app" / "ui" / "assets" / "logo.ico").is_file(),
                   "app/aoi_verification/app/ui/assets/logo.ico"))
    # 앱 패키지가 '폴더만' 이 아니라 실제 코드 트리인지 — 모듈 수로 확인.
    py_count = len(list(pkg.rglob("*.py"))) if pkg.is_dir() else 0
    checks.append((py_count >= 50,
                   f"app/aoi_verification 파이썬 모듈 {py_count}개 (50개 이상)"))

    # VERSION — 자동 업데이트 식별자가 유효한 JSON 인지.
    vf = app / "VERSION"
    ver_ok = False
    if vf.is_file():
        try:
            import json
            data = json.loads(vf.read_text(encoding="utf-8"))
            ver_ok = isinstance(data, dict) and "sha" in data and "branch" in data
        except Exception:
            ver_ok = False
    checks.append((ver_ok, "app/VERSION (sha·branch 를 가진 JSON)"))

    checks.append((not (app / ".git").exists(), "app/.git 없음 (체크아웃 동봉 방지)"))
    checks.append((not (out / "app.new").exists(), "app.new 없음 (업데이트 찌꺼기 방지)"))

    # 의존성 표식 — 없으면 '새 패키지가 필요한 업데이트' 를 감지하지 못한다.
    checks.append(((out / ".deps_installed").is_file(),
                   ".deps_installed (의존성 표식 — 업데이트 감지의 전제)"))

    # 모델 가중치 — 사내망에선 런타임 다운로드가 막힐 수 있어 동봉이 필수.
    ckpt = out / "runtime" / "torch" / "hub" / "checkpoints"
    n_ckpt = len(list(ckpt.glob("*.pth"))) if ckpt.is_dir() else 0
    checks.append((n_ckpt >= 2, f"runtime/torch 가중치 {n_ckpt}개 (2개 이상)"))

    size_mb = _dir_size_mb(out)
    checks.append((size_mb > 800, f"총 용량 {size_mb:.0f} MB (800 MB 이상이어야 정상)"))
    return checks


def import_probe_cmd(out: Path) -> List[str]:
    """번들 파이썬이 **실제로 앱을 import 할 수 있는지** 확인하는 명령.

    폴더가 있다는 것만으로는 증명되지 않는 것을 증명한다(옛 검증의 빈틈)."""
    app = out / "app"
    src = (
        "import sys; sys.path.insert(0, r'%s');"
        "import PyQt6.QtWidgets, cv2, numpy, PIL, openpyxl;"
        "from aoi_verification.app.utils import updater, paths;"
        "assert updater.DEFAULT_BRANCH" % str(app)
    )
    return [str(out / "python" / "python.exe"), "-c", src]


def verify_exe(repo_root: Path = REPO_ROOT, log: Callable = print,
               run: Optional[Callable] = None) -> int:
    """'exe + app 폴더' 산출물 검증.  빌드 직후 자동 실행되며 수동 호출도 가능:
    ``python scripts/build.py verify``."""
    out = repo_root / EXE_OUT_DIRNAME
    log("[verify] exe + app 폴더 산출물 검증 ...")
    checks = verify_checks(out)
    passed = 0
    for good, label in checks:
        log(("  [OK] " if good else "  [!!] ") + label)
        passed += 1 if good else 0

    # import 프로브 — Windows 빌드 머신에서만 의미가 있다(번들 런타임이 windows 바이너리).
    probe_ok = True
    if run is not None and (out / "python" / "python.exe").is_file():
        probe_ok = run(import_probe_cmd(out)) == 0
        log(("  [OK] " if probe_ok else "  [!!] ")
            + "번들 파이썬으로 앱 import 성공 (폴더 존재로는 증명 못 하는 것)")
        passed += 1 if probe_ok else 0
        checks.append((probe_ok, "import probe"))

    ok = passed == len(checks)
    log(f"[verify] 결과: {passed}/{len(checks)} 통과" +
        (" — 빌드 정상!" if ok else " — 위 [!!] 항목을 확인하세요."))
    return 0 if ok else 1


_ACTIONS = {
    "online": build_online,
    "exe": build_exe,
    "portable": build_portable,
    "verify": lambda run=_default_run, log=print: verify_exe(REPO_ROOT, log, run=run),
}


def _usage() -> str:
    return (
        "사용법: python scripts/build.py <exe|portable|online|verify>\n"
        "  exe       exe + app 폴더 (권장) — 파이썬 미설치 PC, 자동 업데이트 완전 동작\n"
        "  portable  자체 포함 CPython 폴더 (.bat 실행 — exe 가 백신에 막힐 때)\n"
        "  online    작은 온라인 launcher exe — 첫 실행 시 인터넷으로 앱/패키지 설치\n"
        "            ※ 사내망처럼 PyPI 가 막힌 곳에서는 쓸 수 없다\n"
        "  verify    exe 빌드 산출물 검증 (빌드 후 자동 실행됨)\n"
        "예) python scripts/build.py exe")


_MENU = [("exe", "exe + app 폴더 (권장, 자동 업데이트 완전 동작)"),
         ("portable", "자체 포함 CPython 폴더 (.bat 실행)"),
         ("online", "작은 온라인 launcher exe (PyPI 접속 필요)"),
         ("verify", "exe 빌드 산출물 검증")]


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
