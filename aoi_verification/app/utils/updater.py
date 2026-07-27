"""GitHub 공개 저장소의 '현재 브랜치' 에서 앱 업데이트를 확인/적용한다.

배포(포터블) 빌드는 ``app/VERSION`` (JSON: ``{"sha","branch"}``) 을 동봉한다.
실행 시 백그라운드로 브랜치 최신 커밋 SHA 를 GitHub API 로 받아 현재 SHA 와
비교하고, 다르면 UI 가 '업데이트 있음' 팝업을 띄운다.  사용자가 동의하면 브랜치
zip 을 받아 **앱 구동에 필요한 것을 전부** 담은 새 트리를 만든다(``aoi_verification/`` ·
``main.py`` · ``양식.xlsx`` · ``requirements.txt`` · ``docs/`` · ``scripts/`` 등 — 새
모듈/리소스 누락 방지).  개발 전용·대용량 데이터(``tests/`` · ``기준/`` · ``bench결과/``)와
VCS/캐시는 제외하고, 무거운 ``python/`` 런타임도 건드리지 않는다.

**적용은 "완성·검증된 트리만" 한다.**  기존 트리에 덮어쓰는 미러링이 아니라 새 트리를
통째로 만들어 바꾼다 — 그래서 상류에서 **삭제된 파일이 실제로 사라지고**(옛 방식은 영원히
남았다), 중간에 실패해도 구버전과 ``VERSION`` 이 그대로 남아 다시 시도할 수 있다.
적용 경로는 두 가지다:

- **exe 모드**(런처가 ``AOI_APP_HOME`` 을 넘겨준 경우) — ``app.new`` 로 스테이징만 하고
  끝낸다.  실제 교체는 다음 실행 때 런처(``scripts/exe_launcher.py``)가 한다.  실행 중인
  앱은 자기가 돌아가는 폴더를 안전하게 바꿀 수 없기 때문이다.
- **포터블/온라인** — 교체해 줄 런처가 없으므로 제자리에 항목 단위로 적용한다(실패 시 롤백).

의존성 패키지는 다시 설치하지 않는다.  포터블/온라인은 ``requirements.txt`` 변경을 감지해
안내만 하고(``deps_changed``), exe 모드는 사내망에서 설치 자체가 불가능하므로 **적용을
포기하고**(``deps_blocked``) '새 배포본 전체를 받으라'고 안내한다.

- 공개 저장소라 토큰 불필요.  모든 네트워크 오류는 **조용히 무시**(부가 기능).
- ``VERSION`` 이 없으면 ``ensure_version_file`` 이 **초기 파일을 만든다**(SHA 는 '미상').
  그래야 VERSION 없이 만들어진 포터블 빌드도 업데이트를 받을 수 있다 — 예전엔 이
  경우 시작 시 자동 확인이 통째로 꺼져 있었다.  단 **git 작업트리에는 만들지 않는다**
  (개발 실행은 자동 적용 자체가 차단된다).
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
# 직전 업데이트가 '새 패키지가 필요해서' 적용되지 않았는지(exe 모드) — UI 안내 분기용.
_deps_blocked: bool = False


def last_error() -> str:
    return _last_error


def insecure_fallback_used() -> bool:
    return _insecure_used


def deps_changed() -> bool:
    """직전 ``download_and_apply`` 가 requirements.txt 변경을 감지했는지.

    자동 업데이트는 앱 소스만 바꾸고 **의존성 패키지는 다시 설치하지 않는다**(번들 런타임
    보존).  목록이 바뀌었으면 UI 가 사용자에게 '의존성을 갱신하라'고 안내하는 데 쓴다."""
    return _deps_changed


def deps_blocked() -> bool:
    """직전 ``download_and_apply`` 가 **새 패키지가 필요해서** 적용을 포기했는지(exe 모드).

    사내망은 PyPI 가 막혀 있어 사용자 PC 에서 패키지를 설치할 수 없다.  코드만 새것으로
    바꾸면 앱이 깨지므로, 이 경우 업데이트를 적용하지 않고 구버전을 유지한 채 UI 가
    '새 배포본 전체를 받으라'고 안내한다."""
    return _deps_blocked


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


# 'exe + app 폴더' 배포에서 런처(``scripts/exe_launcher.py``)가 설치 루트를 넘겨준다.
# 이 값이 있다는 것은 **교체를 대신 해 줄 런처가 있다**는 뜻이므로, 업데이트는 실행 중인
# ``app/`` 을 건드리지 않고 ``app.new`` 로 스테이징만 한다.  없으면(포터블/온라인/개발)
# 예전처럼 제자리에 적용한다.
_APP_HOME_ENV = "AOI_APP_HOME"


def _install_root() -> Optional[Path]:
    """설치 루트(런처가 알려줌).  None 이면 교체해 줄 런처가 없다 → 제자리 적용."""
    v = os.environ.get(_APP_HOME_ENV)
    return Path(v) if v else None


def _pending_dir(root: Path) -> Path:
    """적용 대기 중인 새 앱 폴더 — **존재 자체가 '준비 완료' 신호**다."""
    return root / "app.new"


def update_pending() -> bool:
    """이미 받아둔 업데이트가 재시작을 기다리고 있는지(exe 모드).

    UI 는 이 값으로 안내 문구를 고른다 — 스테이징만 된 상태이므로 '적용되었다' 가 아니라
    '다시 실행하면 적용된다' 가 사실이다."""
    root = _install_root()
    return bool(root and _pending_dir(root).exists())


def _staging_dir(root: Optional[Path], app_root: Path) -> Path:
    """새 트리를 만들 자리.

    반드시 **최종 위치와 같은 볼륨**이어야 한다 — ``%TEMP%`` 를 쓰면 rename 이 복사로
    바뀌어 원자성이 사라진다.  미완성 트리에는 ``.part`` 이름을 주고, 완성된 뒤에만
    ``app.new`` 로 rename 한다(그 rename 이 곧 준비 완료 신호다)."""
    return (root / "app.new.part") if root else (app_root / ".update.part")


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


def _write_version_to(root: Path, sha: str, branch: str, repo: str) -> None:
    """``root/VERSION`` 에 업데이트 식별자를 쓴다.

    스테이징 트리에 쓰기 위해 위치를 인자로 받는다 — 살아있는 ``app/VERSION`` 에 미리
    쓰면 교체가 보류·실패했을 때 앱이 "최신입니다"라고 하면서 **구버전을 영원히 실행**한다."""
    try:
        (Path(root) / "VERSION").write_text(
            json.dumps({"sha": sha, "branch": branch, "repo": repo}),
            encoding="utf-8",
        )
    except Exception:
        pass


def _write_version(sha: str, branch: str, repo: str) -> None:
    _write_version_to(_app_root(), sha, branch, repo)


def ensure_version_file() -> Optional[dict]:
    """VERSION 이 없으면 **초기 VERSION 을 만들어** 자동 업데이트가 늘 동작하게 한다.

    왜 필요했나: 시작 시 자동 확인(`check_for_update`)이 VERSION 이 없으면 네트워크
    호출조차 하지 않고 조용히 꺼졌다.  그런데 포터블 빌드는 git 이 아닌 소스에서
    만들면 애초에 VERSION 이 없다(`portable_build.py`).  결과적으로 그런 배포본은
    **업데이트가 있다는 사실 자체를 영영 모른다.**

    - 이미 있으면 건드리지 않는다(실제 SHA 를 덮어쓰면 안 된다).
    - **git 작업트리에는 만들지 않는다** — 개발 실행은 어차피 자동 적용이 차단되고
      (`main_window` 의 `is_git_checkout` 가드), 저장소에 산출물을 남기지 않는다.
    - 네트워크를 부르지 않는다.  브랜치는 `DEFAULT_BRANCH` 로 적어 두면
      `_resolve_branch` 가 확인 시점에 저장소의 실제 기본 브랜치로 정규화한다
      (CLAUDE.md 의 자기교정 불변식을 그대로 탄다).

    SHA 는 빈 문자열('미상')이다 — 비교할 대상이 없다는 뜻이고, 호출부는 이때
    ``current_unknown`` 으로 안내한 뒤 최신을 받게 한다.  한 번 적용하면
    ``_write_version`` 이 진짜 SHA 를 남겨 이후엔 정상 비교로 돌아간다.

    돌려주는 값은 (기존이든 새로 만든 것이든) 현재 VERSION dict, 만들지 않았고
    읽을 것도 없으면 ``None``."""
    cur = current_version()
    if cur is not None:
        return cur
    if is_git_checkout():
        return None
    seed = _git_head() or {"sha": "", "branch": DEFAULT_BRANCH,
                           "repo": DEFAULT_REPO}
    _write_version(str(seed.get("sha") or ""),
                   str(seed.get("branch") or DEFAULT_BRANCH),
                   str(seed.get("repo") or DEFAULT_REPO))
    return current_version()


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


def _latest_self_healing(repo: str, branch: str) -> tuple[Optional[dict], str]:
    """``latest_commit`` 을 시도하되, 추적 브랜치가 삭제/이름변경(404)돼 실패하면
    저장소의 **실제 기본 브랜치**로 한 번 더 시도한다.

    ``(info, 실제_사용_브랜치)`` 를 돌려준다 — 다운로드(``download_and_apply``)도 살아있는
    브랜치로 가야 하므로 호출부가 교정된 브랜치를 그대로 쓸 수 있게 한다.  둘 다 실패하면
    ``(None, branch)`` (사유는 ``last_error()``)."""
    info = latest_commit(repo, branch)
    if info:
        return info, branch
    db = _default_branch(repo)
    if db and db != branch:
        info = latest_commit(repo, db)
        if info:
            return info, db
    return None, branch


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
    합류**하게 한다.  옛 기본 브랜치(``main``/``master``)는 이미 삭제됐으므로 이들도 함께
    교정한다(그대로 두면 404).  그 외 명시적 브랜치(예: ``release``)는 존중한다.
    ※ 개발/클론(git HEAD) 경로엔 적용 안 함."""
    b = (branch or "").strip()
    if not b or b.startswith("claude/") or b in ("main", "master"):
        return _default_branch(repo)
    return b


def check_for_update() -> Optional[dict]:
    """업데이트가 있으면 ``{"repo","branch","sha","message","date"[,"current_unknown"]}``,
    없으면 None.

    ★ ``manual_check`` 과 **같은 근거**(``_identity``)로 판단한다.  예전엔 이 함수만
    "VERSION 에 sha 가 없으면 즉시 None" 이었고, 그래서 VERSION 이 없는 배포본은
    시작 시 자동 확인이 통째로 꺼져 있었다 — 같은 앱에서 [업데이트 확인] 버튼은
    잘 동작하는데 자동 확인만 안 되는, 근거가 어긋난 상태였다.

    현재 SHA 를 모르면 비교를 건너뛰고 최신을 제안하되 ``current_unknown`` 을 실어
    호출부가 그렇게 안내하게 한다(``manual_check`` 과 동일).

    git 작업트리(개발 실행)와 네트워크 실패는 None — 조용히 무시한다."""
    if is_git_checkout():
        return None                     # 개발 실행에선 자동 팝업을 띄우지 않는다
    if update_pending():
        return None                     # 이미 받아뒀다 — 재시작만 하면 된다(재다운로드 금지)
    ensure_version_file()
    repo, branch, cur_sha = _identity()
    if not branch:
        return None
    latest, branch = _latest_self_healing(repo, branch)
    if not latest or not latest.get("sha"):
        return None
    if cur_sha and str(latest["sha"]) == str(cur_sha):
        return None
    info = {"repo": repo, "branch": branch, "sha": latest["sha"],
            "message": latest.get("message", ""), "date": latest.get("date", "")}
    if not cur_sha:
        info["current_unknown"] = True
    return info


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
    latest, branch = _latest_self_healing(repo, branch)
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
                      #     dev/ 를 통째로 건너뛰어도 구동에 지장 없음.
    ".claude",        # 이 저장소에서 Claude Code 로 작업할 때 쓰는 스킬 모음 —
}                     #   앱 구동과 무관한데 198개 파일·4.8 MB 다(사용자에게 보낼 이유 없음).


# 스테이징 트리가 '앱으로 쓸 수 있는 것' 인지 확인할 최소 항목.  하나라도 없으면 적용하지
# 않는다 — 예전엔 복사 도중 실패해도 '예외가 안 났으면 성공' 이라 반쪽 트리가 적용됐다.
_REQUIRED_IN_STAGING = (
    "main.py",
    "requirements.txt",
    "양식.xlsx",
    "aoi_verification/app/ui/main_window.py",
    "aoi_verification/app/ui/style.qss",
    "aoi_verification/app/utils/updater.py",
    "VERSION",
)


def _stage_tree(src_root: Path, staging: Path, emit) -> None:
    """새 앱 트리를 **통째로** 만든다(기존 트리에 병합하지 않는다).

    통째로 만들기 때문에 상류에서 **삭제된 파일이 자동으로 사라지고**(옛 미러링 방식은
    덮어쓰기만 해서 지운 모듈이 사용자 디스크에 영원히 남았다), ``__pycache__`` 의 낡은
    바이트코드도 따라오지 않는다."""
    import shutil

    staging.mkdir(parents=True, exist_ok=True)
    items = [p for p in sorted(src_root.iterdir(), key=lambda p: p.name)
             if p.name not in _UPDATE_SKIP_TOP]
    m = len(items)
    for i, item in enumerate(items, start=1):
        dst = staging / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
        emit(i, m, "준비 중…")

    # dev/ 는 통째로 건너뛰지만 그 안의 엑셀 템플릿은 구동에 필요하다 → 앱 루트로 옮긴다.
    # (실패를 삼키지 않는다 — _verify_staged 가 잡아서 업데이트를 중단시킨다.)
    tmpl = src_root / "dev" / "양식.xlsx"
    if tmpl.exists():
        shutil.copy2(tmpl, staging / "양식.xlsx")


def _verify_staged(staging: Path) -> str:
    """스테이징 트리 검증.  문제가 없으면 빈 문자열, 있으면 **사용자에게 보일 사유**.

    ``return True`` 가 '예외가 안 났다' 이상을 뜻하게 만드는 관문이다."""
    for rel in _REQUIRED_IN_STAGING:
        p = staging / rel
        if not p.is_file():
            return f"필수 파일 누락: {rel}"
        try:
            if p.stat().st_size <= 0:
                return f"파일이 비어 있음: {rel}"
        except OSError as exc:
            return f"파일 확인 실패: {rel} ({exc})"
    return ""


def _promote_in_place(staging: Path, app_root: Path, emit) -> None:
    """제자리 적용(포터블/온라인) — 항목 단위로 '옆으로 치우고 → 새것을 넣고 → 지운다'.

    실행 중인 앱이라 폴더 전체를 통째로 바꿀 수는 없지만, 최상위 항목마다 rename 을 써서
    중간 실패 시 **전부 되돌린다**(옛 방식은 되돌리지 않아 트리가 섞였다)."""
    import shutil

    def _discard(p: Path) -> None:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    items = [p for p in sorted(staging.iterdir(), key=lambda p: p.name)]
    m = len(items)
    done: list = []                       # [(dst, aside)] — 롤백용
    try:
        for i, item in enumerate(items, start=1):
            dst = app_root / item.name
            aside = app_root / (item.name + ".old-update")
            _discard(aside)
            moved_aside = False
            if dst.exists():
                dst.rename(aside)         # 실패하면 예외 → 아래 롤백
                moved_aside = True
            shutil.move(str(item), str(dst))
            done.append((dst, aside if moved_aside else None))
            emit(i, m, "적용 중…")
    except Exception:
        for dst, aside in reversed(done):  # 되돌리기 — 구버전 트리를 복원한다
            _discard(dst)
            if aside is not None:
                try:
                    aside.rename(dst)
                except OSError:
                    pass
        raise
    for _dst, aside in done:               # 성공 — 백업 정리
        if aside is not None:
            _discard(aside)


def _deps_mismatch(root: Path, staging: Path) -> bool:
    """새 requirements 가 **동봉된 python\\ 런타임이 만들어질 때의 것과 다른지**.

    사내망은 PyPI 가 막혀 있어 사용자 PC 에서 패키지 설치가 불가능하다.  그래서 패키지가
    바뀌는 업데이트는 **적용하면 안 된다** — 코드만 새것이고 패키지가 옛것이면 그게 바로
    '반쪽 업데이트'다.  판단 근거가 없으면(빌드가 표식을 안 남긴 옛 배포본) 막지 않는다."""
    from . import bootstrap

    new = _file_text(staging / "requirements.txt")
    if new is None:
        return False
    if not bootstrap.deps_marker(root).exists():
        return False                       # 비교할 기준이 없다 — 섣불리 막지 않는다
    return not bootstrap.deps_installed(root, new)


def download_and_apply(repo: str, branch: str, target_sha: str,
                       timeout: float = 60.0,
                       progress: Optional[callable] = None) -> bool:
    """브랜치 zip 을 받아 **앱 구동에 필요한 것을 전부** 담은 새 트리를 만들어 적용한다.

    적용 방식은 두 가지다:
      · **exe 모드**(``AOI_APP_HOME`` 있음) — ``app.new`` 로 스테이징만 하고 끝낸다.
        실제 교체는 다음 실행 때 런처가 한다(실행 중인 앱은 자기 폴더를 못 바꾼다).
      · **포터블/온라인** — 교체해 줄 런처가 없으므로 제자리에 항목 단위로 적용한다.

    어느 쪽이든 **완성·검증된 트리만** 적용되고, 실패하면 구버전과 ``VERSION`` 이 그대로
    남아 다음 실행에 다시 시도할 수 있다.

    ``progress(done, total, phase)`` 가 주어지면 다운로드/압축해제/준비/적용 단계의 진행을
    보고한다(로딩바가 0 에서 멈추지 않도록).  total<=0 이면 진행량 미상(busy) 의미."""
    import shutil
    import tempfile
    import zipfile

    global _last_error, _deps_changed, _deps_blocked
    _deps_changed = False
    _deps_blocked = False
    staging: Optional[Path] = None

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

        # 의존성 변경은 적용 **전에** 비교한다(자동 재설치는 안 함 — UI 가 안내).
        _deps_changed = _apply_requirements(src_root, app_root)

        # 3) 준비 — 새 트리를 통째로 만들고(삭제 전파·__pycache__ 정리가 여기서 공짜),
        #    VERSION 도 **스테이징 트리에** 써서 적용이 성공해야만 유효해지게 한다.
        install_root = _install_root()
        staging = _staging_dir(install_root, app_root)
        shutil.rmtree(staging, ignore_errors=True)
        _stage_tree(src_root, staging, _emit)
        _write_version_to(staging, target_sha, branch, repo)

        # 4) 검증 — 여기를 통과해야만 적용한다.
        reason = _verify_staged(staging)
        if reason:
            _last_error = f"업데이트 준비 검증 실패 — {reason}"
            return False

        # 5) 적용
        if install_root is not None:                 # exe 모드 — 교체는 런처에 위임
            if _deps_mismatch(install_root, staging):
                _deps_blocked = True     # UI 가 '새 배포본을 받으라' 고 안내한다
                _last_error = "이번 변경은 새 패키지를 필요로 합니다"
                return False
            pending = _pending_dir(install_root)
            shutil.rmtree(pending, ignore_errors=True)
            staging.rename(pending)                  # ★ 이 rename 이 '준비 완료' 신호
            staging = None                           # finally 가 지우지 않도록
        else:                                        # 포터블/온라인 — 제자리 적용
            _promote_in_place(staging, app_root, _emit)
            _write_version(target_sha, branch, repo)
        _emit(1, 1, "완료")
        return True
    except Exception as exc:
        _last_error = _describe_err(exc, url)
        return False
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
        if staging is not None:      # 남아 있으면 = 실패했다는 뜻 → 흔적 없이 정리
            shutil.rmtree(staging, ignore_errors=True)


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
