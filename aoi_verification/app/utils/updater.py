"""GitHub 공개 저장소의 '현재 브랜치' 에서 앱 업데이트를 확인/적용한다.

배포(포터블) 빌드는 ``app/VERSION`` (JSON: ``{"sha","branch"}``) 을 동봉한다.
실행 시 백그라운드로 브랜치 최신 커밋 SHA 를 GitHub API 로 받아 현재 SHA 와
비교하고, 다르면 UI 가 '업데이트 있음' 팝업을 띄운다.  사용자가 동의하면 브랜치
zip 을 받아 **앱 구동에 필요한 것을 전부** 앱 폴더로 미러링한다(``aoi_verification/`` ·
``main.py`` · ``양식.xlsx`` · ``requirements.txt`` · ``docs/`` · ``scripts/`` 등 — 새
모듈/리소스 누락 방지).  개발 전용·대용량 데이터(``tests/`` · ``기준/`` · ``bench결과/``)와
VCS/캐시는 제외하고, 무거운 ``python/`` 런타임도 건드리지 않는다.  의존성 패키지는
다시 설치하지 않으며, ``requirements.txt`` 변경은 감지해 사용자에게 안내만 한다.

- 공개 저장소라 토큰 불필요.  모든 네트워크 오류는 **조용히 무시**(부가 기능).
- ``VERSION`` 이 없으면(소스 체크아웃/개발 모드) 업데이트 확인을 하지 않는다.
- 표준 라이브러리(urllib)만 사용 — 추가 의존성 없음.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import paths

DEFAULT_REPO = "king-taek/coding"
# 자동 업데이트의 단일 기준은 **저장소의 GitHub 기본(default) 브랜치**다(과거엔 main 이었으나
# main 은 삭제됨).  기본 브랜치 이름은 api.github.com 에서 동적으로 조회하고(_default_branch),
# 조회 실패(사내망에서 api.github.com 차단 등) 시 아래 상수를 폴백으로 쓴다.  VERSION·git 이
# 모두 없는 일반 사용자 PC 도 이 폴백으로 업데이트를 받을 수 있다.
DEFAULT_BRANCH = "claude/aoi-verification-app-LAXpX"
_API_REPO = "https://api.github.com/repos/{repo}"
_API = "https://api.github.com/repos/{repo}/commits/{branch}"
# 사내망이 api.github.com 만 막고 github.com(웹)은 허용하는 경우의 폴백 — 공개
# 저장소의 커밋 Atom 피드(github.com 호스트)에서 최신 커밋 SHA 를 읽는다.
_ATOM = "https://github.com/{repo}/commits/{branch}.atom"
_ZIP = "https://github.com/{repo}/archive/refs/heads/{branch}.zip"
_UA = {"User-Agent": "AOI-Verify-Updater"}

# 마지막 네트워크 오류 사유(사용자에게 표시 — '왜 안 되는지' 진단).
_last_error: str = ""
_OPENER = None
_OPENER_INSECURE = None
# 인증서 검증 실패로 '검증 없이' 폴백했는지(회사 SSL 검사 환경) — UI 안내용.
_insecure_used: bool = False
# 직전 업데이트로 필요한 패키지 목록(requirements.txt)이 바뀌었는지 — 의존성 재설치 안내용.
_deps_changed: bool = False


def last_error() -> str:
    return _last_error


def insecure_fallback_used() -> bool:
    return _insecure_used


def deps_changed() -> bool:
    """직전 ``download_and_apply`` 가 requirements.txt 변경을 감지했는지.

    자동 업데이트는 앱 소스만 바꾸고 **의존성 패키지는 다시 설치하지 않는다**(번들 런타임
    보존).  목록이 바뀌었으면 UI 가 사용자에게 '의존성을 갱신하라'고 안내하는 데 쓴다."""
    return _deps_changed


def _ssl_context(insecure: bool = False):
    """HTTPS SSL 컨텍스트.  ``insecure`` 면 인증서 검증을 끈다.

    회사 SSL 검사(인터셉트) 프록시 환경에서는 HTTPS 가 **회사 루트 CA** 로 재서명되어,
    파이썬 기본 인증서 묶음으로는 'unable to get local issuer certificate' 가 난다
    (Chrome 은 OS 저장소를 써서 됨).  그래서 먼저 ``truststore`` 로 **OS(Windows) 신뢰
    저장소**를 쓰고(없으면 시스템 인증서 로드), 그래도 검증 실패면 호출부가 ``insecure``
    로 재시도한다."""
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:                                   # Chrome 처럼 OS 신뢰 저장소 사용(권장)
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:                                   # 폴백: 시스템 인증서 로드(Windows ROOT/CA)
        ctx = ssl.create_default_context()
        ctx.load_default_certs()
        return ctx
    except Exception:
        return None


def _make_opener(insecure: bool):
    handlers = []
    proxies = urllib.request.getproxies()
    if proxies:
        handlers.append(urllib.request.ProxyHandler(proxies))
    ctx = _ssl_context(insecure=insecure)
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _opener(insecure: bool = False):
    """시스템/환경 프록시 + SSL 컨텍스트를 적용한 opener(검증/비검증 각각 1회 생성)."""
    global _OPENER, _OPENER_INSECURE
    if insecure:
        if _OPENER_INSECURE is None:
            _OPENER_INSECURE = _make_opener(True)
        return _OPENER_INSECURE
    if _OPENER is None:
        _OPENER = _make_opener(False)
    return _OPENER


def _is_ssl_verify_error(exc: Exception) -> bool:
    if isinstance(exc, ssl.SSLError):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, ssl.SSLError)


def _urlopen(url: str, headers: dict, timeout: float):
    """HTTPS GET — 인증서 검증을 시도하고, **검증 실패 시 검증 없이 자동 재시도**한다.

    회사 SSL 검사 프록시 환경에선 검증이 불가능하므로(인증서 확보 불가) 우회한다.
    ``AOI_UPDATE_INSECURE=1`` 이면 처음부터 검증 없이 시도한다.  열린 응답 객체를
    돌려주므로 호출부가 ``with`` 로 닫아야 한다."""
    global _insecure_used
    force = os.environ.get("AOI_UPDATE_INSECURE") == "1"
    if force:
        _insecure_used = True
        return _opener(insecure=True).open(
            urllib.request.Request(url, headers=headers), timeout=timeout)
    try:
        return _opener(insecure=False).open(
            urllib.request.Request(url, headers=headers), timeout=timeout)
    except Exception as exc:
        if _is_ssl_verify_error(exc):
            _insecure_used = True
            return _opener(insecure=True).open(
                urllib.request.Request(url, headers=headers), timeout=timeout)
        raise


def _describe_err(exc: Exception, url: str) -> str:
    host = url.split("/")[2] if "//" in url else url
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} — {host}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            return f"SSL 인증서 오류 — {host} ({reason})"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return f"응답 시간 초과 — {host}"
        return f"연결 실패 — {host} ({reason})"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"응답 시간 초과 — {host}"
    return f"{type(exc).__name__} — {host} ({exc})"


def _http_get(url: str, headers: dict, timeout: float) -> bytes:
    with _urlopen(url, headers, timeout) as r:
        return r.read()


def _app_root() -> Path:
    """앱 소스 루트(포터블의 ``app/``, 개발 시 저장소 루트)."""
    return paths._project_root()


def _version_file() -> Path:
    return _app_root() / "VERSION"


def current_version() -> Optional[dict]:
    """``app/VERSION`` (JSON) 을 읽어 ``{"sha","branch",...}`` 반환.  없으면 None."""
    f = _version_file()
    try:
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def _write_version(sha: str, branch: str, repo: str) -> None:
    try:
        _version_file().write_text(
            json.dumps({"sha": sha, "branch": branch, "repo": repo}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _latest_via_api(repo: str, branch: str, timeout: float) -> Optional[dict]:
    global _last_error
    url = _API.format(repo=repo, branch=branch)
    try:
        raw = _http_get(url, {**_UA, "Accept": "application/vnd.github+json"}, timeout)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        _last_error = _describe_err(exc, url)
        return None
    sha = data.get("sha")
    if not sha:
        _last_error = "GitHub API 응답에 커밋 SHA 없음"
        return None
    commit = data.get("commit") or {}
    msg = (commit.get("message") or "").splitlines()
    date = (commit.get("committer") or {}).get("date", "")
    return {"sha": str(sha), "message": msg[0] if msg else "", "date": date}


def _latest_via_atom(repo: str, branch: str, timeout: float) -> Optional[dict]:
    """github.com 커밋 Atom 피드에서 최신 커밋 SHA 파싱(api 차단 시 폴백)."""
    import re
    global _last_error
    url = _ATOM.format(repo=repo, branch=branch)
    try:
        text = _http_get(url, _UA, timeout).decode("utf-8", "replace")
    except Exception as exc:
        _last_error = _describe_err(exc, url)
        return None
    m = re.search(r"Commit/([0-9a-fA-F]{40})", text)
    if not m:
        _last_error = "github.com Atom 피드에서 커밋 SHA 를 찾지 못함"
        return None
    return {"sha": m.group(1).lower(), "message": "", "date": ""}


def latest_commit(repo: str, branch: str, timeout: float = 15.0) -> Optional[dict]:
    """브랜치 HEAD 커밋 ``{"sha","message","date"}``.  api.github.com → 실패 시
    github.com Atom 폴백.  둘 다 실패하면 None(사유는 ``last_error()``)."""
    info = _latest_via_api(repo, branch, timeout)
    if info:
        return info
    return _latest_via_atom(repo, branch, timeout)


_default_branch_cache: dict = {}


def _default_branch(repo: str, timeout: float = 10.0) -> str:
    """저장소의 GitHub 기본(default) 브랜치 이름.  조회 실패 시 ``DEFAULT_BRANCH`` 폴백.

    ``GET /repos/{repo}`` 의 ``default_branch`` 를 읽는다.  api.github.com 이 막힌 사내망에선
    조회가 불가하므로 폴백 상수를 쓴다.  한 번 성공하면 프로세스 동안 캐시(repo 별)."""
    if repo in _default_branch_cache:
        return _default_branch_cache[repo]
    try:
        raw = _http_get(_API_REPO.format(repo=repo),
                        {**_UA, "Accept": "application/vnd.github+json"}, timeout)
        b = (json.loads(raw.decode("utf-8")).get("default_branch") or "").strip()
        if b:
            _default_branch_cache[repo] = b
            return b
    except Exception:
        pass
    return DEFAULT_BRANCH


def _resolve_branch(branch: Optional[str], repo: str = DEFAULT_REPO) -> str:
    """VERSION 에 박힌 추적 브랜치를 자동 업데이트 기준(**저장소 기본 브랜치**)으로 정규화한다.

    과거 포터블 빌드는 VERSION 에 작업 브랜치(``claude/…``)를 스탬프했으므로, 그대로 두면
    옛 빌드는 계속 옛(삭제됐을 수 있는) 브랜치를 본다.  비었거나 ``claude/`` 로 시작하는(=과거
    작업 브랜치) 값은 저장소의 GitHub 기본 브랜치로 치환해 **모든 배포본이 기본 브랜치로
    합류**하게 한다.  그 외 명시적 브랜치(예: ``release``)는 존중한다.
    ※ 개발/클론(git HEAD) 경로엔 적용 안 함."""
    b = (branch or "").strip()
    if not b or b.startswith("claude/"):
        return _default_branch(repo)
    return b


def check_for_update() -> Optional[dict]:
    """업데이트가 있으면 ``{"repo","branch","sha","message","date"}``, 없으면 None.

    VERSION 이 없거나(개발 모드) 네트워크 실패면 None(=확인 안 함/조용히 무시)."""
    cur = current_version()
    if not cur or not cur.get("sha"):
        return None
    repo = cur.get("repo") or DEFAULT_REPO
    branch = _resolve_branch(cur.get("branch"), repo)
    if not branch:
        return None
    latest = latest_commit(repo, branch)
    if not latest or not latest.get("sha"):
        return None
    if str(latest["sha"]) != str(cur["sha"]):
        return {"repo": repo, "branch": branch, "sha": latest["sha"],
                "message": latest.get("message", ""), "date": latest.get("date", "")}
    return None


def is_git_checkout() -> bool:
    """앱 소스가 git 작업트리인지 — 개발/클론 실행(포터블 아님)."""
    return (_app_root() / ".git").exists()


def _git_head() -> Optional[dict]:
    """VERSION 이 없을 때, git 작업트리의 현재 커밋/브랜치를 읽는다(개발/클론 실행).

    포터블(VERSION 동봉)에선 쓰이지 않는다.  git 미설치/실패 시 None."""
    import subprocess
    root = str(_app_root())
    try:
        sha = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        branch = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception:
        return None
    if sha and branch and branch != "HEAD":
        return {"sha": sha, "branch": branch, "repo": DEFAULT_REPO}
    return None


def _identity() -> tuple:
    """추적 대상 ``(repo, branch, current_sha)`` 를 결정한다.

    우선순위: VERSION 파일 → git HEAD → 코드 내장 기본값(DEFAULT_REPO/BRANCH).
    VERSION·git 이 모두 없으면 current_sha 는 빈 문자열("미상")이지만, repo/branch 는
    기본값으로 알 수 있으므로 **최신 버전을 받아 적용하는 것은 가능**하다."""
    cur = current_version()
    if cur and cur.get("branch"):
        repo = cur.get("repo") or DEFAULT_REPO
        return (repo, _resolve_branch(cur["branch"], repo), str(cur.get("sha") or ""))
    gh = _git_head()
    if gh and gh.get("branch"):
        return (gh.get("repo") or DEFAULT_REPO, gh["branch"], str(gh.get("sha") or ""))
    return (DEFAULT_REPO, _default_branch(DEFAULT_REPO), "")


def manual_check() -> tuple:
    """사용자가 직접 '업데이트 확인' 을 눌렀을 때 — 결과를 명시적으로 돌려준다.

    반환 ``(status, info)``:
      · ``("update", {repo,branch,sha,message[,current_unknown]})`` — 받을 버전 있음
      · ``("latest", {})``  — 이미 최신
      · ``("unknown", {"error":…})`` — 네트워크 실패(확인 불가)
    현재 버전(SHA)을 모르면(VERSION·git 모두 없음) 비교는 못 하지만, 내장된 기본
    repo/branch 로 **최신 버전을 받아 적용**하도록 ``current_unknown`` 으로 안내한다.
    (한 번 받으면 VERSION 이 기록돼 이후엔 정상 비교)"""
    global _last_error
    _last_error = ""
    repo, branch, cur_sha = _identity()
    latest = latest_commit(repo, branch)
    if not latest or not latest.get("sha"):
        return ("unknown", {"error": _last_error or "GitHub 연결 실패"})
    if cur_sha and str(latest["sha"]) == str(cur_sha):
        return ("latest", {})
    info = {"repo": repo, "branch": branch, "sha": latest["sha"],
            "message": latest.get("message", "")}
    if not cur_sha:
        info["current_unknown"] = True
    return ("update", info)


# 앱 구동에 필요 없는(또는 받지 말아야 할) 최상위 항목 — 개발 전용·대용량 데이터·VCS/캐시.
# 그 외에는 리포에 있는 것을 **전부** 받아 앱 폴더로 미러링한다(새 모듈·리소스 누락 방지).
_UPDATE_SKIP_TOP = {
    ".git", ".github", "__pycache__", ".pytest_cache", ".idea", ".vscode",
    "dev",            # 개발 전용 모음(tests·bench결과·양식.xlsx) — 구동에 불필요.
    "pytest.ini",     #   ※ 양식.xlsx 는 포터블 빌드 시 app\ 루트로 따로 복사되므로
}                     #     dev/ 를 통째로 건너뛰어도 구동에 지장 없음.


def download_and_apply(repo: str, branch: str, target_sha: str,
                       timeout: float = 60.0,
                       progress: Optional[callable] = None) -> bool:
    """브랜치 zip 을 받아 **앱 구동에 필요한 것을 전부** 덮어쓴다(미러링).  성공 시 True.

    ``progress(done, total, phase)`` 가 주어지면 다운로드/압축해제/적용 단계의 진행을
    보고한다(로딩바가 0 에서 멈추지 않도록).  total<=0 이면 진행량 미상(busy) 의미."""
    import shutil
    import tempfile
    import zipfile

    global _last_error, _deps_changed
    _deps_changed = False

    def _emit(done, total, phase):
        if progress:
            try:
                progress(int(done), int(total), str(phase))
            except Exception:
                pass

    url = _ZIP.format(repo=repo, branch=branch)
    tmpd = Path(tempfile.mkdtemp(prefix="aoi_update_"))
    try:
        # 1) 다운로드 — Content-Length 가 있으면 바이트 진행, 없으면 busy.
        zip_path = tmpd / "src.zip"
        with _urlopen(url, _UA, timeout) as r, open(zip_path, "wb") as f:
            total = int(getattr(r, "headers", {}).get("Content-Length", 0) or 0)
            done = 0
            _emit(0, total, "다운로드 중…")
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                _emit(done, total, "다운로드 중…")

        # 2) 압축 해제 — 파일 수 기준 진행.
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            n = len(names)
            for i, name in enumerate(names, start=1):
                z.extract(name, tmpd)
                if i % 20 == 0 or i == n:
                    _emit(i, n, "압축 해제 중…")

        roots = [p for p in tmpd.iterdir() if p.is_dir()]
        if not roots:
            return False
        src_root = roots[0]                 # coding-<branch> 형태의 단일 최상위 폴더
        app_root = _app_root()
        if not (src_root / "aoi_verification").exists():
            return False

        # 의존성 변경은 덮어쓰기 **전에** 비교한다(자동 재설치는 안 함 — UI 가 안내).
        _deps_changed = _apply_requirements(src_root, app_root)

        # 3) 적용(미러링) — skip 목록 외 최상위 항목을 전부 앱 폴더로 복사.
        items = [p for p in src_root.iterdir() if p.name not in _UPDATE_SKIP_TOP]
        m = len(items)
        for i, item in enumerate(items, start=1):
            dst = app_root / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
            _emit(i, m, "적용 중…")

        # dev/ 는 통째로 건너뛰지만, 그 안의 엑셀 템플릿(양식.xlsx)은 구동에 필요하므로
        # 앱 루트로 따로 복사한다(포터블 레이아웃: app\양식.xlsx → template_path 가 찾음).
        tmpl = src_root / "dev" / "양식.xlsx"
        if tmpl.exists():
            try:
                shutil.copy2(tmpl, app_root / "양식.xlsx")
            except Exception:
                pass

        _write_version(target_sha, branch, repo)
        _emit(m, m, "완료")
        return True
    except Exception as exc:
        _last_error = _describe_err(exc, url)
        return False
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def _file_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _apply_requirements(src_root: Path, app_root: Path) -> bool:
    """새 requirements.txt 가 **기존과 다른지** 판단(복사는 호출부에서 수행).

    기존 파일이 없으면(이 기능 도입 후 첫 업데이트) '바뀜'으로 오인해 불필요하게
    안내하지 않도록 False 를 돌려준다(둘 다 있고 내용이 다를 때만 True)."""
    new = _file_text(src_root / "requirements.txt")
    if new is None:
        return False
    old = _file_text(app_root / "requirements.txt")
    if old is None:
        return False
    return old.strip() != new.strip()
