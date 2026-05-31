"""portable_build.py — 자체 포함 CPython 포터블 폴더 빌드(파이썬 구현).

make_portable.bat 의 파이썬 버전.  python-build-standalone 의 install_only 런타임을
받아 ``dist_portable/python`` 에 풀고, 의존성 설치 + 앱 소스 복사 + 런처/업데이트
스크립트 동봉 + VERSION 스탬프를 만든다.  결과: ``dist_portable/`` (대상 PC 에서
``run_aoi.bat`` 더블클릭, 파이썬 불필요).

순수 경로/명령 구성은 부수효과 없이 분리해 테스트 가능하게 했고, 실제 다운로드/압축/
복사 같은 무거운 부수효과만 ``run_build`` 가 수행한다(보통 Windows 에서 실행).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional

OUT_DIRNAME = "dist_portable"
REPO_SLUG = "king-taek/coding"


def portable_python(out_dir: Path) -> Path:
    """포터블 폴더 안 CPython 실행 파일 경로."""
    if os.name == "nt":
        return out_dir / "python" / "python.exe"
    return out_dir / "python" / "bin" / "python3"


def version_stamp(sha: str, branch: str, repo: str = REPO_SLUG) -> str:
    """app/VERSION 에 기록할 JSON 문자열(자동 업데이트 식별자)."""
    return json.dumps({"sha": sha, "branch": branch, "repo": repo})


def _git_head(repo_root: Path) -> tuple[str, str]:
    """현재 커밋 SHA·브랜치(없으면 빈 문자열)."""
    def _q(args):
        try:
            return subprocess.check_output(
                ["git", "-C", str(repo_root), *args],
                stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        except Exception:
            return ""
    return _q(["rev-parse", "HEAD"]), _q(["rev-parse", "--abbrev-ref", "HEAD"])


def run_build(repo_root: Path, py_url: str,
              run: Callable = None, log: Callable = print) -> int:
    """포터블 빌드 수행.  ``run`` 은 명령 실행기(주입 가능).  반환: 0=성공."""
    if run is None:
        def run(cmd, cwd=None):
            log(">> " + " ".join(str(c) for c in cmd))
            return subprocess.call([str(c) for c in cmd],
                                   cwd=str(cwd) if cwd else None)

    out = repo_root / OUT_DIRNAME
    out.mkdir(parents=True, exist_ok=True)
    ppy = portable_python(out)

    # 1) 자체 포함 CPython 준비(이미 있으면 재사용).
    if ppy.exists():
        log(f"[1/4] reuse existing {ppy}")
    else:
        log(f"[1/4] downloading CPython: {py_url}")
        tgz = out / "python.tar.gz"
        try:
            import urllib.request
            urllib.request.urlretrieve(py_url, str(tgz))
        except Exception as exc:
            log(f"[FAILED] download error: {exc}")
            return 1
        if tgz.stat().st_size < 1_000_000:
            log("[FAILED] downloaded file too small — check PY_STANDALONE_URL")
            return 1
        log("       extracting ...")
        import tarfile
        with tarfile.open(str(tgz)) as tf:
            tf.extractall(str(out))
        tgz.unlink(missing_ok=True)
    if not ppy.exists():
        log(f"[FAILED] {ppy} not found after extract")
        return 1

    # 2) 의존성 설치 + 보안 가드.
    log("[2/4] installing dependencies (torch/openvino — takes a while) ...")
    if run([str(ppy), "-m", "pip", "install", "--upgrade", "pip"]) != 0:
        return 1
    if run([str(ppy), "-m", "pip", "install", "-r",
            str(repo_root / "requirements.txt")]) != 0:
        return 1
    if run([str(ppy), str(repo_root / "scripts" / "internal"
                          / "verify_no_forbidden.py")]) != 0:
        return 1

    # 3) 앱 소스 + 리소스 + 런처 스크립트 복사.
    log("[3/4] copying app source ...")
    app = out / "app"
    app.mkdir(parents=True, exist_ok=True)
    _copytree(repo_root / "aoi_verification", app / "aoi_verification")
    shutil.copy2(repo_root / "main.py", app / "main.py")
    # 엑셀 템플릿(dev/*.xlsx)을 app 루트로 — template_path 가 찾는다.
    for xlsx in (repo_root / "dev").glob("*.xlsx"):
        shutil.copy2(xlsx, app / xlsx.name)
    scripts = repo_root / "scripts"
    for bat in ("run_aoi.bat", "run_aoi_debug.bat", "update_app.bat"):
        src = scripts / bat
        if src.exists():
            shutil.copy2(src, out / bat)

    # 4) VERSION 스탬프(자동 업데이트용).
    sha, branch = _git_head(repo_root)
    if sha and branch:
        (app / "VERSION").write_text(version_stamp(sha, branch), encoding="utf-8")
        log(f"       VERSION: {branch} @ {sha}")
    log("[4/4] done. Zip the whole dist_portable/ folder; on target PC unzip "
        "and double-click run_aoi.bat (no Python needed).")
    return 0


def _copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
