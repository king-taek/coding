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
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 한글 출력이 깨지지 않도록 stdout/stderr 를 UTF-8 로. (Windows cp949 대비)
# ★ 콘솔에 직접 찍을 때는 Windows 가 WriteConsoleW 를 써서 무사하지만, 로그 파일로
#   리다이렉트하는 순간 인코딩이 cp949 로 떨어져 UnicodeEncodeError 로 **빌드가 죽는다**
#   (출력 문구의 '—' 같은 문자). 30분짜리 빌드에서 로그를 남기려다 실패하면 원인 파악이
#   더 어려워지므로, 안내를 하기 전에 이걸 먼저 보장한다. (run_this_before.py 와 동일 패턴)
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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

# 다시 빌드할 때 **반드시 지워야 할** 것들.  PyInstaller 는 onefile 이라 exe 파일 하나만
# 교체하고 distpath 폴더를 비우지 않고, portable_build 도 app/aoi_verification 아래만
# 지운다.  그래서 옛 방식(단독 exe) 산출물의 `_internal/` 이 남으면 검증이 **영원히**
# 실패하고, 사용자는 '다시 빌드하세요' 안내를 따라도 상태를 바꿀 수 없다.
_STALE_ON_REBUILD = ("_internal", "app.new", "app.new.part", "app.old",
                     "AOI_Verify.exe")
# 반대로 **남겨야** 하는 것: python\ 과 runtime\ 은 다시 만들면 30분이 걸리고,
# portable_build 의 증분 재사용이 이걸 전제로 한다.  결과\ 는 사용자 산출물이다.


def stale_paths(out: Path) -> List[Path]:
    """다시 빌드하기 전에 지울 경로들(존재하는 것만).  순수 — 테스트 대상."""
    return [out / name for name in _STALE_ON_REBUILD if (out / name).exists()]


def clean_stale_output(out: Path, log: Callable = print) -> int:
    """옛 산출물을 지운다.  못 지우면 **중단**한다(조용히 진행하면 같은 함정에 다시 빠진다)."""
    targets = stale_paths(out)
    if not targets:
        return 0
    log("[clean] 옛 빌드 산출물 정리 ...")
    for p in targets:
        log(f"  - {p}")
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass
    left = [p for p in targets if p.exists()]
    if left:
        log("[실패] 다음을 지우지 못했습니다(프로그램이 실행 중이거나 탐색기가 열고 "
            "있을 수 있습니다):")
        for p in left:
            log(f"  {p}")
        log("       해당 프로그램·창을 닫고 다시 실행하세요.")
        return 1
    return 0


# 산출물 상태 진단 — portable_build.run_build 의 **기록 순서가 그대로 단계 사다리**라
# 별도 마커 없이 '어디까지 갔는지' 를 알 수 있다(마지막 기록이 app/VERSION).
def diagnose(out: Path) -> Tuple[str, List[str]]:
    """``(상태, [사용자가 할 일 …])``.  순수 — 테스트 대상.

    검증이 '무엇이 없다' 를 나열하는 것만으로는 사용자가 다음에 뭘 해야 할지 알 수 없다.
    특히 '빌드를 안 돌린 것' 과 '옛 산출물이 남은 것' 과 '중간에 실패한 것' 은 대응이 전혀
    다른데 화면에는 똑같이 빨간 줄로만 보인다."""
    if not out.is_dir():
        return "not_built", ["빌드를 아직 실행하지 않았습니다.",
                             "  python scripts\\build.py exe"]
    if (out / "_internal").is_dir():
        return "stale_onedir", [
            f"옛 방식(단독 exe) 산출물이 남아 있습니다: {out / '_internal'}",
            "지금 빌드는 이것을 자동으로 지웁니다. 그래도 남아 있다면 수동으로 지우세요:",
            f"  rmdir /s /q {out}",
            "그 다음:  python scripts\\build.py exe"]
    if not (out / "AOI_Verify.exe").is_file() and not (out / "python").is_dir():
        return "not_built", ["폴더는 있으나 빌드 산출물이 없습니다.",
                             "  python scripts\\build.py exe"]
    if not (out / "python" / "python.exe").is_file():
        return "partial:runtime", [
            "CPython 런타임을 받아 푸는 단계에서 멈췄습니다.",
            "네트워크(github.com 다운로드)를 확인하고 다시 실행하세요."]
    if not (out / ".deps_installed").is_file():
        return "partial:deps", [
            "의존성 설치(pip — torch/openvino) 단계에서 멈췄습니다.",
            "네트워크와 디스크 여유 공간을 확인하고 다시 실행하세요.",
            "  (python\\ 은 재사용되므로 런타임을 다시 받지는 않습니다.)"]
    ck = out / "runtime" / "torch" / "hub" / "checkpoints"
    n_ckpt = len(list(ck.glob("*.pth"))) if ck.is_dir() else 0
    if n_ckpt < 2:
        return "partial:weights", [
            "모델 가중치를 받는 단계에서 멈췄습니다.",
            "download.pytorch.org 접속을 확인하고 다시 실행하세요."]
    if not (out / "app" / "VERSION").is_file():
        return "partial:appcopy", [
            "앱 소스 복사 / VERSION 스탬프 단계에서 멈췄습니다.",
            "  python scripts\\build.py exe"]
    return "complete", []


def report_diagnosis(out: Path, log: Callable = print) -> str:
    """진단을 사람이 읽을 형태로 출력하고 상태를 돌려준다."""
    state, todo = diagnose(out)
    if state == "complete":
        return state
    log("")
    log("[진단] " + todo[0])
    for line in todo[1:]:
        log("       " + line)
    return state


def preflight(repo_root: Path, log: Callable = print,
              free_bytes: Optional[int] = None,
              dirty: Optional[bool] = None) -> int:
    """빌드 시작 전 점검.  0=계속.  ``free_bytes``/``dirty`` 는 테스트 주입용."""
    if free_bytes is None:
        try:
            free_bytes = shutil.disk_usage(str(repo_root)).free
        except OSError:
            free_bytes = None
    if free_bytes is not None and free_bytes < 10 * 1024 ** 3:
        log(f"[실패] 디스크 여유 공간이 부족합니다 "
            f"({free_bytes / 1024 ** 3:.1f} GB). 10 GB 이상 확보하세요.")
        log("       산출물만 ~1.5 GB 이고 pip 캐시·휠이 더 필요합니다. 공간이 모자라면 "
            "30분 뒤 pip 한복판에서 실패합니다.")
        return 1

    # 커밋 안 한 변경이 있으면 VERSION 이 거짓말을 한다 — 스탬프는 HEAD 의 sha 인데
    # 복사되는 소스는 작업트리다.  그러면 사용자 앱은 원격 HEAD 와 비교해 '최신입니다'
    # 라며 **공개되지 않은 코드를 영원히 실행**한다(자동 업데이트 불변식 위반).
    if dirty is None:
        try:
            outp = subprocess.check_output(
                ["git", "-C", str(repo_root), "status", "--porcelain"],
                stderr=subprocess.DEVNULL, timeout=10).decode("utf-8", "replace")
            dirty = bool(outp.strip())
        except Exception:
            dirty = False
    if dirty:
        log("[주의] 커밋하지 않은 변경이 있습니다.")
        log("       배포본의 VERSION 에는 HEAD 의 커밋이 박히지만 실제 복사되는 소스는")
        log("       작업트리입니다. 그러면 사용자 앱이 '최신입니다' 라며 공개되지 않은")
        log("       코드를 계속 실행합니다. 커밋·푸시 후 빌드하는 것을 권합니다.")
    return 0


# ---------------------------------------------------------------------------
# 실행 헬퍼 — run 은 주입 가능(테스트는 가짜로 대체)
# ---------------------------------------------------------------------------
def _default_run(cmd: List[str], cwd: Optional[Path] = None) -> int:
    # 시각을 찍는다 — 30분짜리 빌드에서 '어디서 멈췄나' 는 결국 '언제 멈췄나' 로 판단한다.
    print(f">> [{time.strftime('%H:%M:%S')}]", " ".join(str(c) for c in cmd), flush=True)
    # 심층 방어 — argv 의 `-s` 와 달리 **자식 프로세스까지 상속**된다(pip 이 띄우는
    # 빌드 백엔드 등).  동봉 파이썬이 개발 PC 의 user site 를 보면 pip 이 전 패키지를
    # 'already satisfied' 로 건너뛰어 빈 번들이 나온다.  venv 는 원래 user site 를 안
    # 보므로 여기 일괄로 걸어도 무해하다.
    env = dict(os.environ, PYTHONNOUSERSITE="1")
    return subprocess.call([str(c) for c in cmd],
                           cwd=str(cwd) if cwd else None, env=env)


def _ensure_venv(run: Callable, log: Callable) -> str:
    """저장소 .venv 를 준비하고 그 파이썬 경로를 돌려준다."""
    vpy = venv_python(REPO_ROOT)
    if not vpy.exists():
        log("[venv] creating .venv ...")
        if run([sys.executable, "-m", "venv", str(REPO_ROOT / ".venv")]) != 0:
            raise SystemExit("venv creation failed")
    if run(pip_install_cmd(str(vpy), "--upgrade", "pip")) != 0:
        raise SystemExit("pip upgrade failed")     # 아래 단계들과 동일하게 확인한다
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
    # ⓪ 30분을 버리기 전에 점검하고, 옛 산출물을 정리한다.
    if preflight(REPO_ROOT, log) != 0:
        return 1
    if clean_stale_output(out, log) != 0:
        return 1

    # ① 런처 exe 먼저 — 여기서 실패하면 무거운 런타임 다운로드 전에 빨리 끝난다.
    vpy = _ensure_venv(run, log)
    if run(pip_install_cmd(vpy, "pyinstaller>=6")) != 0:
        raise SystemExit("pyinstaller install failed")
    if run(guard_cmd(vpy)) != 0:
        raise SystemExit("security guard failed")
    log("[build] thin launcher exe (no app code inside) ...")
    if run(pyinstaller_cmd(vpy, INTERNAL / "exe_launcher.spec", out), REPO_ROOT) != 0:
        raise SystemExit("launcher build failed")
    # 런처가 제대로 얇게 나왔는지 **여기서** 본다 — 2~3 GB 를 받기 전에 멈추기 위해.
    exe = out / "AOI_Verify.exe"
    if not exe.is_file():
        raise SystemExit(f"launcher exe not produced: {exe}")
    exe_mb = exe.stat().st_size / (1024 ** 2)
    if (out / "_internal").exists() or exe_mb >= 30:
        raise SystemExit(
            f"launcher looks wrong (exe {exe_mb:.1f} MB, _internal "
            f"{'있음' if (out / '_internal').exists() else '없음'}) — "
            "앱이 exe 안에 얼려 들어갔을 수 있습니다. exe_launcher.spec 을 확인하세요.")
    log(f"       launcher OK ({exe_mb:.1f} MB)")

    # ② 번들 런타임 + 앱 소스 — 포터블 빌드와 레이아웃이 같으므로 그대로 재사용한다.
    impl = _load_portable_impl()
    rc = impl.run_build(REPO_ROOT, PY_STANDALONE_URL, run=run, log=log,
                        out_dirname=EXE_OUT_DIRNAME,
                        bats=("run_aoi.bat", "run_aoi_debug.bat"),
                        torch_home=True)
    if rc != 0:
        # run_build 가 이미 정확한 [FAILED] 를 출력했다.  여기서 파일시스템으로 추측한
        # 진단을 덧붙이면 서로 모순되는 안내가 된다(실제로 그런 일이 있었다).
        log("[실패] 런타임/앱 배치 단계에서 실패했습니다 — 위의 [FAILED] 메시지를 "
            "확인하세요.")
        return rc
    log("[done] " + str(output_path("exe", REPO_ROOT)))
    log("       Ship the whole dist\\AOI_Verify folder (zip).")
    log("")
    vrc = verify_exe(REPO_ROOT, log, run=run)
    if vrc != 0:
        log("[주의] 빌드는 완료됐지만 검증에서 누락 항목이 발견되었습니다.")
        return vrc
    log("")
    log("[다음] 배포용 zip 만들기:  python scripts\\make_release_zip.py")
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

    # ★ 번들에 패키지가 **실제로** 들어갔는지 — 총 용량은 부분 설치를 못 잡고 사용자
    #   `결과/` 파일까지 세어 오염된다.  site-packages 를 직접 본다.
    impl = _load_portable_impl()
    missing = impl.missing_packages(out, out / "app" / "requirements.txt")
    checks.append((not missing,
                   "번들 site-packages 에 필요한 패키지 전부 존재"
                   + (f" (빠짐: {', '.join(missing[:5])})" if missing else "")))
    sp_mb = _dir_size_mb(impl.site_packages_dir(out))
    checks.append((sp_mb > 500,
                   f"site-packages 용량 {sp_mb:.0f} MB (500 MB 이상이어야 정상)"))
    return checks


def import_probe_cmd(out: Path) -> List[str]:
    """번들 파이썬이 **실제로 앱을 import 할 수 있는지** 확인하는 명령.

    ★ ``-s`` 가 필수다.  이 파이썬은 버전이 겹치면 개발 PC 의 user site 를 보므로,
    없으면 번들이 텅 비어 있어도 개발 PC 에서 무조건 통과한다 — 정작 증명해야 할 것을
    증명하지 못한다."""
    app = out / "app"
    src = (
        "import sys; sys.path.insert(0, r'%s');"
        "import PyQt6.QtWidgets, cv2, numpy, PIL, openpyxl;"
        "import torch, torchvision, openvino, skimage, imagehash, psutil;"
        "from aoi_verification.app.utils import updater, paths;"
        "assert updater.DEFAULT_BRANCH" % str(app)
    )
    return [str(out / "python" / "python.exe"), "-s", "-c", src]


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
    if not ok:
        report_diagnosis(out, log)      # make_release_zip 과 같은 말을 하게 한다
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
