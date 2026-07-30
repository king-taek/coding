"""온라인 부트스트래퍼 — 파이썬이 없는 사용자도 쓰는 **얇은 launcher exe** 의 핵심 로직.

배포 시나리오: PyInstaller 로 이 모듈(``bootstrap_main``)을 진입점으로 하는 **작은 exe**
하나만 만든다(수십 MB).  사용자가 그 exe 를 처음 실행하면:

  1) 쓰기 가능한 설치 폴더(``%LOCALAPPDATA%\\AOI Recipe Verification``)를 만든다.
     앱 소스·패키지·캐시가 모두 이 폴더 안에 담긴다.
  2) 앱 소스(``aoi_verification/`` · ``main.py`` · ``양식.xlsx`` …)가 없으면 GitHub
     브랜치 zip 으로 받아 그 폴더에 푼다(=기존 ``updater.download_and_apply``).
  3) 의존성(requirements.txt)이 아직 없으면 **인터넷에서 pip 로 설치**한다(번들 파이썬
     또는 시스템 파이썬 대상).  이미 있으면 건너뛴다.
  4) 그 폴더의 ``main.py`` 를 실행한다.

이 구조의 이점:
  - exe 자체엔 무거운 PyQt6/openvino 를 안 넣어도 돼 **작다**(온라인 다운로드형).
  - 앱이 쓰기 가능한 폴더에 풀려 **기존 자동 업데이트가 그대로 동작**한다
    (``updater`` 가 그 폴더를 ``_app_root`` 로 보고 덮어쓴다).

여기서는 **순수 함수**(경로 결정·필요 여부 판단·pip 명령 구성)만 두고, 실제 네트워크/
프로세스 실행은 주입(injection)으로 분리해 헤드리스 테스트가 가능하게 한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, List, Optional


APP_DIRNAME = "AOI Recipe Verification"   # 사용자 데이터(설치) 폴더 이름
_MARKER = ".deps_installed"               # 의존성 설치 완료 표식(버전별)


# ---------------------------------------------------------------------------
# 순수 로직 (테스트 대상) — 부수효과 없음
# ---------------------------------------------------------------------------
def data_root(env: Optional[dict] = None) -> Path:
    """앱을 풀어 둘 **쓰기 가능한** 설치 폴더(앱·패키지·캐시가 모두 이 안에 담긴다).

    Windows 는 ``%LOCALAPPDATA%\\AOI Recipe Verification``, 그 외/미설정은
    ``~/.AOI Recipe Verification``.  여기에 앱 소스를 풀고 자동 업데이트도 이 폴더를
    덮어쓴다(읽기 전용 exe 와 분리)."""
    env = env if env is not None else os.environ
    base = env.get("LOCALAPPDATA") or env.get("APPDATA")
    if base:
        return Path(base) / APP_DIRNAME
    return Path(env.get("HOME", str(Path.home()))) / ("." + APP_DIRNAME)


def app_is_present(root: Path) -> bool:
    """앱 소스가 이미 풀려 있나 — ``main.py`` + ``aoi_verification/`` 둘 다 있어야 True."""
    root = Path(root)
    return (root / "main.py").is_file() and (root / "aoi_verification").is_dir()


def deps_marker(root: Path) -> Path:
    return Path(root) / _MARKER


def deps_installed(root: Path, req_text: Optional[str]) -> bool:
    """이 requirements 내용에 대해 의존성 설치가 끝났는지(표식 파일 내용으로 비교).

    requirements.txt 가 바뀌면 표식이 달라져 재설치가 필요하다고 본다.  ``req_text``
    가 None(파일 없음)이면 비교 불가 → 표식 존재만으로 판단."""
    mk = deps_marker(root)
    if not mk.exists():
        return False
    if req_text is None:
        return True
    try:
        return mk.read_text(encoding="utf-8").strip() == _req_fingerprint(req_text)
    except Exception:
        return False


def req_lines(req_text: Optional[str]) -> List[str]:
    """requirements 에서 **실제 요구사항 줄만** 뽑는다(주석·빈 줄 제거).

    주석이나 빈 줄만 바뀐 것을 '의존성이 바뀌었다'고 오인하면, exe 배포에서는 업데이트가
    통째로 막히고(설치 불가 판정) 온라인 배포에서는 불필요한 재설치가 돈다."""
    out: List[str] = []
    for line in (req_text or "").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def _req_fingerprint(req_text: str) -> str:
    import hashlib
    norm = "\n".join(req_lines(req_text))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def write_deps_marker(root: Path, req_text: Optional[str]) -> None:
    try:
        deps_marker(root).write_text(
            _req_fingerprint(req_text) if req_text else "installed", encoding="utf-8")
    except Exception:
        pass


def pip_install_cmd(python_exe: str, req_file: Path,
                    upgrade: bool = True) -> List[str]:
    """requirements.txt 를 설치하는 pip 명령(리스트).  회사 SSL 프록시 대비 truststore 는
    앱 런타임에서 처리하므로 여기선 표준 pip 만 쓴다.

    ``upgrade=False`` 는 **빠진 것만 채운다**.  requirements 가 전부 ``>=`` 라
    ``--upgrade`` 를 주면 이미 잘 돌던 무거운 패키지(PyQt6·openvino 등)까지 최신으로
    끌어올려(대용량 + 회귀 위험) 버린다.  첫 설치(온라인 launcher)는 최신을 받는 게
    맞으므로 기본값은 유지한다."""
    cmd = [str(python_exe), "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    return cmd + ["-r", str(req_file)]


def launch_cmd(python_exe: str, main_py: Path) -> List[str]:
    """앱(main.py) 실행 명령."""
    return [str(python_exe), str(main_py)]


def ensure_deps(root: Path, python_exe: str, req_file: Path,
                run: Callable[[List[str]], int],
                log: Callable[[str], None] = lambda _m: None) -> bool:
    """설치 폴더의 의존성이 준비돼 있는지 확인하고, 아니면 pip 로 설치한다.  True=사용 가능.

    **lite 배포**(``build.py exe-lite``)의 첫 실행이 여기로 온다 — 빌드가 라이브러리를
    일부러 넣지 않고 표식(``.deps_installed``)도 남기지 않으므로, 표식이 없다는 사실이
    곧 '아직 설치 안 됨' 신호다.

    ``upgrade=False`` — **빠진 것만 채운다**.  requirements 가 전부 ``>=`` 라
    ``--upgrade`` 를 주면 잘 돌던 무거운 패키지까지 최신으로 끌어올린다(CLAUDE.md 규칙).

    ★ 설치가 실패하면 표식을 **쓰지 않는다**.  거짓 표식이 남으면 다음 실행이 '설치됐다'
      고 판단해 영원히 재시도하지 않고, 사용자는 ImportError 만 보게 된다."""
    req_file = Path(req_file)
    if not req_file.is_file():
        return True                     # 비교할 목록이 없으면 판단하지 않는다
    try:
        req_text = req_file.read_text(encoding="utf-8")
    except OSError:
        return True
    if deps_installed(root, req_text):
        return True

    log("필요한 패키지를 설치합니다 — 처음 1회, 인터넷이 필요하고 몇 분 걸립니다.")
    if run(pip_install_cmd(python_exe, req_file, upgrade=False)) != 0:
        log("패키지 설치에 실패했습니다. 인터넷·프록시 설정을 확인한 뒤 다시 실행하세요.")
        return False
    write_deps_marker(root, req_text)
    log("설치가 끝났습니다.")
    return True


def target_python(root: Path, *, frozen: bool, sys_executable: str) -> str:
    """앱을 실행할 파이썬 인터프리터 경로.

    - 번들 파이썬이 데이터 폴더에 있으면(``python/python.exe``) 그것을 쓴다.
    - PyInstaller 로 얼린 launcher(frozen) 안에서는 ``sys.executable`` 이 launcher exe
      자신이라 파이썬이 아니다 → 시스템 ``python`` 에 위임(없으면 호출부가 안내).
    - 개발 실행(frozen 아님)에서는 현재 ``sys.executable``(=파이썬) 그대로."""
    root = Path(root)
    for cand in (root / "python" / "python.exe", root / "python" / "bin" / "python3"):
        if cand.exists():
            return str(cand)
    if frozen:
        return "python"            # 시스템 파이썬 위임(설치 안내는 호출부가 처리)
    return sys_executable


# ---------------------------------------------------------------------------
# 오케스트레이션 — 부수효과는 주입된 콜러블로 분리(테스트는 가짜 주입)
# ---------------------------------------------------------------------------
def bootstrap(root: Path, *,
              repo: str, branch: str,
              fetch_app: Callable[[Path], bool],
              run: Callable[[List[str]], int],
              log: Callable[[str], None] = lambda _m: None,
              frozen: bool = False,
              sys_executable: str = sys.executable) -> int:
    """앱을 준비(다운로드+의존성)하고 실행한다.  실제 다운로드/프로세스 실행은 주입.

    ``fetch_app(root) -> bool`` : 앱 소스를 root 로 받아 푼다(보통 updater.download_and_apply).
    ``run(cmd) -> int``        : 프로세스 실행(보통 subprocess.call).
    반환: 앱 종료 코드(준비 실패 시 비정상 코드)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    if not app_is_present(root):
        log("앱을 내려받는 중…")
        if not fetch_app(root) or not app_is_present(root):
            log("앱 다운로드 실패 — 인터넷 연결을 확인하세요.")
            return 3

    py = target_python(root, frozen=frozen, sys_executable=sys_executable)
    req = root / "requirements.txt"
    req_text = req.read_text(encoding="utf-8") if req.exists() else None
    if req.exists() and not deps_installed(root, req_text):
        log("필요한 패키지를 설치하는 중… (처음 1회, 인터넷 필요)")
        rc = run(pip_install_cmd(py, req))
        if rc != 0:
            log("패키지 설치 실패 — 인터넷/프록시 설정을 확인하세요.")
            return 4
        write_deps_marker(root, req_text)

    log("앱을 시작합니다…")
    return run(launch_cmd(py, root / "main.py"))
