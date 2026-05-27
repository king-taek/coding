"""GitHub 공개 저장소의 '현재 브랜치' 에서 앱 업데이트를 확인/적용한다.

배포(포터블) 빌드는 ``app/VERSION`` (JSON: ``{"sha","branch"}``) 을 동봉한다.
실행 시 백그라운드로 브랜치 최신 커밋 SHA 를 GitHub API 로 받아 현재 SHA 와
비교하고, 다르면 UI 가 '업데이트 있음' 팝업을 띄운다.  사용자가 동의하면 브랜치
zip 을 받아 앱 소스(``aoi_verification/``, ``main.py``, ``양식.xlsx``)만 교체하고
재시작을 안내한다(무거운 ``python/`` 런타임은 건드리지 않음).

- 공개 저장소라 토큰 불필요.  모든 네트워크 오류는 **조용히 무시**(부가 기능).
- ``VERSION`` 이 없으면(소스 체크아웃/개발 모드) 업데이트 확인을 하지 않는다.
- 표준 라이브러리(urllib)만 사용 — 추가 의존성 없음.
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from . import paths

DEFAULT_REPO = "king-taek/coding"
# VERSION·git 이 모두 없을 때(일반 사용자 PC) 도 업데이트를 받을 수 있도록, 추적
# 대상 저장소/브랜치를 코드에 내장한다(현재 작업 브랜치).
DEFAULT_BRANCH = "claude/matching-npu-gpu-modes-GwTRB"
_API = "https://api.github.com/repos/{repo}/commits/{branch}"
# 사내망이 api.github.com 만 막고 github.com(웹)은 허용하는 경우의 폴백 — 공개
# 저장소의 커밋 Atom 피드(github.com 호스트)에서 최신 커밋 SHA 를 읽는다.
_ATOM = "https://github.com/{repo}/commits/{branch}.atom"
_ZIP = "https://github.com/{repo}/archive/refs/heads/{branch}.zip"
_UA = {"User-Agent": "AOI-Verify-Updater"}

# 마지막 네트워크 오류 사유(사용자에게 표시 — '왜 안 되는지' 진단).
_last_error: str = ""
_OPENER = None


def last_error() -> str:
    return _last_error


def _ssl_context():
    """HTTPS 검증용 SSL 컨텍스트.

    회사 SSL 검사(인터셉트) 프록시 환경에서는 HTTPS 가 **회사 루트 CA** 로 재서명되어,
    파이썬 기본 인증서 묶음으로는 'unable to get local issuer certificate' 가 난다
    (Chrome 은 OS 저장소를 써서 됨).  그래서 ``truststore`` 로 **OS(Windows) 신뢰
    저장소**를 쓰게 한다(Chrome 과 동일).  없으면 시스템 인증서 로드로 폴백.

    마지막 수단으로 ``AOI_UPDATE_INSECURE=1`` 환경변수를 주면 검증을 끈다(권장하지
    않음 — 인증서 확보가 불가능한 폐쇄망용 비상 옵션)."""
    import os as _os
    if _os.environ.get("AOI_UPDATE_INSECURE") == "1":
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


def _opener():
    """시스템/환경 프록시 + OS 신뢰 저장소(SSL)를 적용한 opener(1회 생성).

    회사 PC 는 보통 시스템 프록시를 쓰므로 ``getproxies()`` 로 자동 적용하고,
    SSL 검사 프록시 대비 OS 인증서 저장소(``_ssl_context``)로 검증한다.
    (PAC/자동구성 프록시는 urllib 가 해석 못 함 — 그 경우 수동 프록시 필요.)"""
    global _OPENER
    if _OPENER is None:
        handlers = []
        proxies = urllib.request.getproxies()
        if proxies:
            handlers.append(urllib.request.ProxyHandler(proxies))
        ctx = _ssl_context()
        if ctx is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        _OPENER = urllib.request.build_opener(*handlers)
    return _OPENER


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
    req = urllib.request.Request(url, headers=headers)
    with _opener().open(req, timeout=timeout) as r:
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


def check_for_update() -> Optional[dict]:
    """업데이트가 있으면 ``{"repo","branch","sha","message","date"}``, 없으면 None.

    VERSION 이 없거나(개발 모드) 네트워크 실패면 None(=확인 안 함/조용히 무시)."""
    cur = current_version()
    if not cur or not cur.get("sha"):
        return None
    repo = cur.get("repo") or DEFAULT_REPO
    branch = cur.get("branch") or ""
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
        return (cur.get("repo") or DEFAULT_REPO, cur["branch"], str(cur.get("sha") or ""))
    gh = _git_head()
    if gh and gh.get("branch"):
        return (gh.get("repo") or DEFAULT_REPO, gh["branch"], str(gh.get("sha") or ""))
    return (DEFAULT_REPO, DEFAULT_BRANCH, "")


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


def download_and_apply(repo: str, branch: str, target_sha: str,
                       timeout: float = 60.0) -> bool:
    """브랜치 zip 을 받아 앱 소스만 덮어쓴다.  성공 시 VERSION 갱신 후 True."""
    import shutil
    import tempfile
    import zipfile

    global _last_error
    url = _ZIP.format(repo=repo, branch=branch)
    tmpd = Path(tempfile.mkdtemp(prefix="aoi_update_"))
    try:
        zip_path = tmpd / "src.zip"
        req = urllib.request.Request(url, headers=_UA)
        with _opener().open(req, timeout=timeout) as r, \
                open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmpd)
        roots = [p for p in tmpd.iterdir() if p.is_dir()]
        if not roots:
            return False
        src_root = roots[0]                 # coding-<branch> 형태의 단일 최상위 폴더
        app_root = _app_root()
        pkg = src_root / "aoi_verification"
        if not pkg.exists():
            return False
        # 실행 중 .py 를 덮어써도 메모리의 모듈엔 영향 없음 → 재시작 시 적용.
        shutil.copytree(pkg, app_root / "aoi_verification", dirs_exist_ok=True)
        for fn in ("main.py", "양식.xlsx"):
            srcf = src_root / fn
            if srcf.exists():
                shutil.copy2(srcf, app_root / fn)
        _write_version(target_sha, branch, repo)
        return True
    except Exception as exc:
        _last_error = _describe_err(exc, url)
        return False
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def restart_app() -> bool:
    """현재 파이썬으로 ``main.py`` 를 새로 띄운다(호출자가 곧바로 종료해야 함)."""
    import subprocess
    try:
        subprocess.Popen([sys.executable, str(_app_root() / "main.py")],
                         cwd=str(_app_root()))
        return True
    except Exception:
        return False
