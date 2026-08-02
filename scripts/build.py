"""build.py — exe/포터블 빌드 스크립트 (배치 파일 대체).

한국어 Windows(cp949) 콘솔에서 ``.bat`` 의 한글이 깨지는 문제를 피하려고, 빌드를
**파이썬으로** 한다(Python 은 UTF-8 안전).  세 가지 배포 방식을 한 스크립트로 제공:

    python scripts/build.py exe         # exe + app 폴더 (권장)
    python scripts/build.py exe-lite    # 위와 같되 라이브러리는 첫 실행 때 받음
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

def _py_standalone_url() -> str:
    """자체 포함 CPython 다운로드 주소 — **앱의 bootstrap 에서 가져온다.**

    온라인 launcher 도 같은 주소로 받으므로 상수를 두 곳에 적으면 어긋난다
    (CLAUDE.md 의 '같은 상수를 두 곳에 두지 않는다' 패턴)."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from aoi_verification.app.utils.bootstrap import PY_STANDALONE_URL
    return PY_STANDALONE_URL


PY_STANDALONE_URL = _py_standalone_url()


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
        "exe-lite": repo_root / "dist" / "AOI_Verify_Lite",
        "portable": repo_root / "dist_portable",
    }[kind]


# 'exe + app 폴더' 산출물 폴더 이름(저장소 루트 기준) — portable_build 에 넘긴다.
EXE_OUT_DIRNAME = "dist/AOI_Verify"
# lite 배포 — 라이브러리를 빼고 사용자 PC 의 첫 실행 때 pip 로 받게 한다.
EXE_LITE_OUT_DIRNAME = "dist/AOI_Verify_Lite"


def exe_out_dirname(lite: bool) -> str:
    return EXE_LITE_OUT_DIRNAME if lite else EXE_OUT_DIRNAME

# 다시 빌드할 때 **반드시 지워야 할** 것들.  PyInstaller 는 onefile 이라 exe 파일 하나만
# 교체하고 distpath 폴더를 비우지 않고, portable_build 도 app/aoi_verification 아래만
# 지운다.  그래서 옛 방식(단독 exe) 산출물의 `_internal/` 이 남으면 검증이 **영원히**
# 실패하고, 사용자는 '다시 빌드하세요' 안내를 따라도 상태를 바꿀 수 없다.
# `.irbuild` 는 IR 변환용 torch 를 푸는 임시 트리다(portable_build.IR_TMP_DIRNAME).
# 정상 종료 때는 스스로 지워지지만, 중간에 죽으면 수백 MB 가 남아 배포 zip 에 실린다.
_STALE_ON_REBUILD = ("_internal", "app.new", "app.new.part", "app.old",
                     "AOI_Verify.exe", ".irbuild")
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
def diagnose(out: Path, lite: bool = False) -> Tuple[str, List[str]]:
    """``(상태, [사용자가 할 일 …])``.  순수 — 테스트 대상.

    검증이 '무엇이 없다' 를 나열하는 것만으로는 사용자가 다음에 뭘 해야 할지 알 수 없다.
    특히 '빌드를 안 돌린 것' 과 '옛 산출물이 남은 것' 과 '중간에 실패한 것' 은 대응이 전혀
    다른데 화면에는 똑같이 빨간 줄로만 보인다.

    ``lite`` 배포는 라이브러리를 일부러 넣지 않으므로 ``.deps_installed`` 가 **없는 것이
    정상**이다 — 그걸 '중간에 멈췄다' 로 읽으면 멀쩡한 빌드를 실패로 안내한다."""
    cmd = "  python scripts\\build.py " + ("exe-lite" if lite else "exe")
    if not out.is_dir():
        return "not_built", ["빌드를 아직 실행하지 않았습니다.", cmd]
    if (out / "_internal").is_dir():
        return "stale_onedir", [
            f"옛 방식(단독 exe) 산출물이 남아 있습니다: {out / '_internal'}",
            "지금 빌드는 이것을 자동으로 지웁니다. 그래도 남아 있다면 수동으로 지우세요:",
            f"  rmdir /s /q {out}",
            "그 다음:" + cmd]
    if not (out / "AOI_Verify.exe").is_file() and not (out / "python").is_dir():
        return "not_built", ["폴더는 있으나 빌드 산출물이 없습니다.", cmd]
    if not (out / "python" / "python.exe").is_file():
        return "partial:runtime", [
            "CPython 런타임을 받아 푸는 단계에서 멈췄습니다.",
            "네트워크(github.com 다운로드)를 확인하고 다시 실행하세요."]
    if not lite and not (out / ".deps_installed").is_file():
        return "partial:deps", [
            "의존성 설치(pip — PyQt6/openvino) 단계에서 멈췄습니다.",
            "네트워크와 디스크 여유 공간을 확인하고 다시 실행하세요.",
            "  (python\\ 은 재사용되므로 런타임을 다시 받지는 않습니다.)"]
    impl = _load_portable_impl()
    if impl.missing_ir(out):
        return "partial:ir", [
            "백본을 OpenVINO IR 로 변환하는 단계에서 멈췄습니다.",
            "빌드 전용 torch 설치(PyPI)와 가중치 다운로드(download.pytorch.org)",
            "접속을 확인하고 다시 실행하세요.",
            "  (변환에만 쓰는 것이고 배포본에는 torch 가 들어가지 않습니다.)"]
    if not (out / "app" / "VERSION").is_file():
        return "partial:appcopy", [
            "앱 소스 복사 / VERSION 스탬프 단계에서 멈췄습니다.", cmd]
    return "complete", []


def report_diagnosis(out: Path, log: Callable = print,
                     lite: bool = False) -> str:
    """진단을 사람이 읽을 형태로 출력하고 상태를 돌려준다."""
    state, todo = diagnose(out, lite)
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
        log("       산출물에 더해 IR 변환용 torch 를 임시로 풀고(끝나면 삭제) pip 캐시·"
            "휠도 필요합니다. 공간이 모자라면 30분 뒤 pip 한복판에서 실패합니다.")
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
    # ※ 여기서 PYTHONNOUSERSITE 를 일괄로 걸지 않는다.  격리는 argv 의 `-s` 로 한다
    #   (`portable_build._isolated`) — 그래야 테스트가 관찰할 수 있고, **개발 PC 환경을
    #   일부러 검사하는 가드**(user site 를 봐야 한다)를 막지 않는다.
    return subprocess.call([str(c) for c in cmd], cwd=str(cwd) if cwd else None)


def dev_python() -> str:
    """개발 PC 의 '진짜' 파이썬 — venv 의 base 인터프리터.

    빌드는 보통 저장소 `.venv` 로 돌아가는데, venv 는 user site 를 보지 않는다.
    개발 PC 에 무엇이 깔려 있는지 보려면 그 venv 가 만들어진 원본을 봐야 한다."""
    base = Path(sys.base_prefix)
    return str(base / "python.exe" if os.name == "nt" else base / "bin" / "python3")


def check_dev_machine(run: Callable, log: Callable) -> int:
    """개발 PC 환경(user site 포함)에 금지 패키지가 있는지 — **무거운 단계 전에** 본다.

    ★ 순서가 핵심이다.  이 검사가 뒤에 있으면 사용자는 CPython 다운로드와 2~3 GB
    pip 설치로 30분 이상을 태운 **뒤에야** 같은 실패를 다시 본다."""
    py = dev_python()
    if not Path(py).exists():
        return 0                      # 판단 불가 — 막지 않는다
    log("[check] 개발 PC 환경 검사 (회사 보안 정책) ...")
    if run(guard_cmd(py)) != 0:
        log("[실패] 개발 PC 에 금지 패키지가 있습니다.")
        log("       배포본에는 들어가지 않지만 회사 정책상 제거해야 합니다.")
        log("       위 [금지] 메시지가 알려주는 경로와 명령을 그대로 쓰세요.")
        return 1
    return 0


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
def committed_ir_missing(repo_root: Path = REPO_ROOT) -> List[str]:
    """저장소에 커밋되지 않은 백본 IR 목록.  비어 있으면 정상.  순수 — 테스트 대상."""
    impl = _load_portable_impl()
    src = impl.repo_ir_dir(repo_root)
    return [k for k in impl.IR_MODELS
            if not ((src / f"{k}.xml").is_file() and (src / f"{k}.bin").is_file())]


def build_online(run: Callable = _default_run, log: Callable = print) -> int:
    """작은 온라인 launcher exe — 첫 실행에 파이썬·앱·라이브러리를 **전부** 받는다.

    ★ 백본 IR 은 앱 트리에 실려 함께 내려온다(저장소에 커밋돼 있어야 한다).  이 배포는
      아무것도 동봉하지 않으므로 **IR 이 저장소에 없으면 만들 수단이 없다** — 그대로
      내보내면 사용자 PC 에서 고효율 모드의 GPU 가속이 조용히 죽는다.  여기서 막는다."""
    missing = committed_ir_missing(REPO_ROOT)
    if missing:
        log("[실패] 백본 IR 이 저장소에 커밋돼 있지 않습니다: " + ", ".join(missing))
        log("       온라인 배포는 앱을 GitHub 에서 받아 쓰므로, IR 도 저장소에 있어야 "
            "합니다.")
        log("       한 번만 만들어 커밋하세요 (torch 접속이 되는 PC 에서):")
        log("         pip install torch torchvision openvino")
        log("         python scripts\\internal\\make_ir.py")
        log("         git add runtime/ir && git commit -m \"백본 IR 추가\" && git push")
        log("       (exe / exe-lite 빌드는 IR 이 없어도 그 자리에서 변환하므로 동작합니다.)")
        return 1

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


def build_exe(run: Callable = _default_run, log: Callable = print,
              lite: bool = False) -> int:
    """exe + app 폴더 — 파이썬 미설치 PC 용이고 **자동 업데이트가 완전히 동작**한다.

    얇은 런처 exe 하나만 얼리고(앱 코드 0줄), 앱 소스·리소스는 ``app\\`` 에 loose 로 둔다.
    옛 단독 exe(onedir) 는 앱을 exe 안 PYZ 에 넣어서 업데이트가 조용히 무시됐다 —
    그 구조를 되풀이하지 않기 위한 형태다.

    ``lite=True`` 는 라이브러리를 번들에 넣지 않는다(첫 실행 때 사용자 PC 가 pip 로
    받는다).  전달 파일이 크게 줄어드는 대신 **각 PC 가 PyPI 에 닿아야** 하므로, 인터넷이
    막힌 PC 를 위해 전체 배포본도 함께 유지한다.  런처·정리·검증 흐름은 완전히 같다."""
    out = REPO_ROOT / exe_out_dirname(lite)
    # ⓪ 30분을 버리기 전에 점검하고, 옛 산출물을 정리한다.
    if preflight(REPO_ROOT, log) != 0:
        return 1
    if check_dev_machine(run, log) != 0:
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
                        out_dirname=exe_out_dirname(lite),
                        bats=("run_aoi.bat", "run_aoi_debug.bat"),
                        bundle_ir=True, install_deps=not lite)
    if rc != 0:
        # run_build 가 이미 정확한 [FAILED] 를 출력했다.  여기서 파일시스템으로 추측한
        # 진단을 덧붙이면 서로 모순되는 안내가 된다(실제로 그런 일이 있었다).
        log("[실패] 런타임/앱 배치 단계에서 실패했습니다 — 위의 [FAILED] 메시지를 "
            "확인하세요.")
        return rc
    log("[done] " + str(output_path("exe-lite" if lite else "exe", REPO_ROOT)))
    log(f"       Ship the whole {exe_out_dirname(lite).replace('/', chr(92))} "
        "folder (zip).")
    if lite:
        log("       ※ 라이브러리는 들어 있지 않습니다 — 사용자 PC 의 첫 실행 때 "
            "인터넷으로 설치됩니다.")
    log("")
    vrc = verify_exe(REPO_ROOT, log, run=run, lite=lite)
    if vrc != 0:
        log("[주의] 빌드는 완료됐지만 검증에서 누락 항목이 발견되었습니다.")
        return vrc
    log("")
    log("[다음] 배포용 zip 만들기:  python scripts\\make_release_zip.py"
        + (" --lite" if lite else ""))
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
    if check_dev_machine(run, log) != 0:      # exe 모드와 같은 검사를 같은 시점에
        return 1
    impl = _load_portable_impl()
    return impl.run_build(REPO_ROOT, PY_STANDALONE_URL, run=run, log=log)


def _dir_size_mb(d: Path) -> float:
    if not d.is_dir():
        return 0.0
    return sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024)


def verify_checks(out: Path, lite: bool = False) -> List[tuple]:
    """'exe + app 폴더' 산출물 검사 목록 ``[(통과여부, 라벨), …]`` — 순수(테스트 대상).

    옛 검증은 ``_internal/aoi_verification`` 이 '폴더로 존재하는가' 만 봤는데, 그 폴더는
    리소스 파일(style.qss·assets) 때문에 **앱 코드가 하나도 없어도 만들어진다** — 즉
    공허하게 통과했다.  여기서는 '통과했다면 실제로 그렇다' 가 성립하는 것만 본다.

    ``lite`` 배포는 라이브러리를 일부러 빼므로 기대값이 **뒤집힌다**: site-packages 는
    비어 있어야 하고, ``.deps_installed`` 는 **없어야** 한다(있으면 첫 실행이 설치를
    건너뛰어 앱이 아예 안 뜬다)."""
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
    # ★ 동봉 폰트 — 없으면 **조용히** 폴백 서체로 떨어진다(앱은 그대로 뜬다).
    #   `theme.load_app_fonts` 가 실패를 삼키도록 일부러 만들었기 때문에, 빠진 것을
    #   알아채는 자리는 여기뿐이다.  PC 마다 글꼴이 달라 보이는 것을 막으려고 동봉한
    #   것이므로, 빠지면 그 목적이 통째로 사라진다.  앱이 실제로 읽는 목록
    #   (`theme._FONT_FILES`)을 그대로 확인한다 — 목록이 갈라지지 않게.
    from aoi_verification.app.ui.theme import _FONT_FILES
    for _fname in _FONT_FILES:
        checks.append((
            (pkg / "app" / "ui" / "assets" / "font" / _fname).is_file(),
            f"app/aoi_verification/app/ui/assets/font/{_fname}"))
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
    # ★ lite 는 정반대다: 표식이 **없어야** 첫 실행이 설치를 한다.  잘못 찍혀 나가면
    #   사용자 PC 에서 설치를 건너뛰고 ImportError 로 죽는다 — 되돌릴 방법이 없다.
    marker = (out / ".deps_installed").is_file()
    if lite:
        checks.append((not marker,
                       ".deps_installed 없음 (lite — 첫 실행 때 설치하라는 신호)"))
    else:
        checks.append((marker,
                       ".deps_installed (의존성 표식 — 업데이트 감지의 전제)"))

    impl = _load_portable_impl()

    # 백본 IR — 없으면 GPU 가속 경로가 통째로 죽는다(배포본에 torch 가 없어 폴백도 없다).
    missing_ir = impl.missing_ir(out)
    checks.append((not missing_ir,
                   "runtime/ir 백본 IR(.xml+.bin) 존재"
                   + (f" (빠짐: {', '.join(missing_ir)})" if missing_ir else "")))
    # 빌드 전용 torch 임시 트리가 남으면 수백 MB 가 그대로 배포 zip 에 실린다.
    checks.append((not (out / impl.IR_TMP_DIRNAME).exists(),
                   f"{impl.IR_TMP_DIRNAME}/ 없음 (빌드 전용 torch 잔재 방지)"))

    sp_mb = _dir_size_mb(impl.site_packages_dir(out))
    if lite:
        # 라이브러리가 딸려 들어가면 'lite' 가 아니다 — 전달 파일이 커진 것을 모른 채
        # 배포하게 된다.  pip 자체(+setuptools)만 있으므로 넉넉히 100 MB 로 잡는다.
        checks.append((sp_mb < 100,
                       f"site-packages 용량 {sp_mb:.0f} MB (lite — 100 MB 미만)"))
    else:
        # ★ 번들에 패키지가 **실제로** 들어갔는지 — 총 용량은 부분 설치를 못 잡고 사용자
        #   `결과/` 파일까지 세어 오염된다.  site-packages 를 직접 본다.
        missing = impl.missing_packages(out, out / "app" / "requirements.txt")
        checks.append((not missing,
                       "번들 site-packages 에 필요한 패키지 전부 존재"
                       + (f" (빠짐: {', '.join(missing[:5])})" if missing else "")))
        # torch 가 빠지면서 하한이 내려갔다.  '전부 건너뛰어 텅 빈' 상태만 잡으면 되므로
        # PyQt6+openvino+opencv 만으로도 확실히 넘는 값을 쓴다.
        checks.append((sp_mb > 200,
                       f"site-packages 용량 {sp_mb:.0f} MB (200 MB 이상이어야 정상)"))
    # ★ torch 가 배포본에 들어가면 이 작업의 목적 자체가 무너진다 — 회귀 가드.
    torch_dir = impl.site_packages_dir(out) / "torch"
    checks.append((not torch_dir.exists(),
                   "번들에 torch 없음 (IR 동봉으로 대체 — 회귀 가드)"))
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
        "import openvino, skimage, imagehash, psutil;"
        "from aoi_verification.app.utils import updater, paths;"
        "assert updater.DEFAULT_BRANCH" % str(app)
    )
    return [str(out / "python" / "python.exe"), "-s", "-c", src]


def verify_exe(repo_root: Path = REPO_ROOT, log: Callable = print,
               run: Optional[Callable] = None, lite: bool = False) -> int:
    """'exe + app 폴더' 산출물 검증.  빌드 직후 자동 실행되며 수동 호출도 가능:
    ``python scripts/build.py verify``."""
    out = repo_root / exe_out_dirname(lite)
    log(f"[verify] exe + app 폴더 산출물 검증{' (lite)' if lite else ''} ...")
    checks = verify_checks(out, lite)
    passed = 0
    for good, label in checks:
        log(("  [OK] " if good else "  [!!] ") + label)
        passed += 1 if good else 0

    # import 프로브 — Windows 빌드 머신에서만 의미가 있다(번들 런타임이 windows 바이너리).
    # ★ lite 는 건너뛴다.  라이브러리가 아직 없는 게 정상이라, 돌리면 반드시 실패한다 —
    #   정상인 빌드를 실패로 보고하는 거짓 경보가 된다.
    probe_ok = True
    if not lite and run is not None and (out / "python" / "python.exe").is_file():
        probe_ok = run(import_probe_cmd(out)) == 0
        log(("  [OK] " if probe_ok else "  [!!] ")
            + "번들 파이썬으로 앱 import 성공 (폴더 존재로는 증명 못 하는 것)")
        passed += 1 if probe_ok else 0
        checks.append((probe_ok, "import probe"))

    ok = passed == len(checks)
    log(f"[verify] 결과: {passed}/{len(checks)} 통과" +
        (" — 빌드 정상!" if ok else " — 위 [!!] 항목을 확인하세요."))
    if not ok:
        report_diagnosis(out, log, lite)   # make_release_zip 과 같은 말을 하게 한다
    return 0 if ok else 1


_ACTIONS = {
    "online": build_online,
    "exe": build_exe,
    "exe-lite": lambda run=_default_run, log=print: build_exe(run, log, lite=True),
    "portable": build_portable,
    "verify": lambda run=_default_run, log=print: verify_exe(REPO_ROOT, log, run=run),
    "verify-lite": lambda run=_default_run, log=print: verify_exe(
        REPO_ROOT, log, run=run, lite=True),
}


def _usage() -> str:
    return (
        "사용법: python scripts/build.py <exe|exe-lite|portable|online|verify>\n"
        "  exe       exe + app 폴더 (권장) — 파이썬 미설치 PC, 자동 업데이트 완전 동작\n"
        "  exe-lite  위와 같되 **라이브러리를 빼고** 첫 실행 때 사용자 PC 가 받는다\n"
        "            전달 파일이 훨씬 작다. 대신 각 PC 가 PyPI 에 닿아야 한다\n"
        "  portable  자체 포함 CPython 폴더 (.bat 실행 — exe 가 백신에 막힐 때)\n"
        "  online    작은 온라인 launcher exe — 첫 실행 시 인터넷으로 앱/패키지 설치\n"
        "            ※ 사용자 PC 에 파이썬이 설치돼 있어야 하고, 백본 IR 이 없어\n"
        "               GPU 가속이 동작하지 않는다 (exe-lite 를 쓸 것)\n"
        "  verify    exe 빌드 산출물 검증 (빌드 후 자동 실행됨)  · verify-lite\n"
        "예) python scripts/build.py exe")


_MENU = [("exe", "exe + app 폴더 (권장, 자동 업데이트 완전 동작)"),
         ("exe-lite", "위와 같되 라이브러리는 첫 실행 때 받음 (전달 파일 작음)"),
         ("portable", "자체 포함 CPython 폴더 (.bat 실행)"),
         ("online", "작은 온라인 launcher exe (파이썬 설치 필요·가속 없음)"),
         ("verify", "exe 빌드 산출물 검증")]


def _prompt_kind(input_fn=input) -> Optional[str]:
    """인자 없이 실행(예: VS Code ▶)했을 때 번호로 빌드 종류를 고르게 한다.

    대화형 입력이 불가하면 None 을 돌려준다(=사용법만 출력)."""
    print("어떤 빌드를 만들까요? 번호를 입력하세요 (취소: Enter):")
    for i, (k, desc) in enumerate(_MENU, start=1):
        print(f"  {i}) {k:9s} {desc}")
    try:
        sel = input_fn(f"선택 [1-{len(_MENU)}]: ").strip()
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
