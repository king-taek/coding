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
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from . import paths

DEFAULT_REPO = "king-taek/coding"
_API = "https://api.github.com/repos/{repo}/commits/{branch}"
_ZIP = "https://github.com/{repo}/archive/refs/heads/{branch}.zip"
_UA = {"User-Agent": "AOI-Verify-Updater"}


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


def latest_commit(repo: str, branch: str, timeout: float = 8.0) -> Optional[dict]:
    """브랜치 HEAD 커밋 정보 ``{"sha","message","date"}`` (실패 시 None)."""
    url = _API.format(repo=repo, branch=branch)
    try:
        req = urllib.request.Request(
            url, headers={**_UA, "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    sha = data.get("sha")
    if not sha:
        return None
    commit = data.get("commit") or {}
    msg = (commit.get("message") or "").splitlines()
    date = (commit.get("committer") or {}).get("date", "")
    return {"sha": sha, "message": msg[0] if msg else "", "date": date}


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


def download_and_apply(repo: str, branch: str, target_sha: str,
                       timeout: float = 60.0) -> bool:
    """브랜치 zip 을 받아 앱 소스만 덮어쓴다.  성공 시 VERSION 갱신 후 True."""
    import shutil
    import tempfile
    import zipfile

    url = _ZIP.format(repo=repo, branch=branch)
    tmpd = Path(tempfile.mkdtemp(prefix="aoi_update_"))
    try:
        zip_path = tmpd / "src.zip"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r, \
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
    except Exception:
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
