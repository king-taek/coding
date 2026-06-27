"""온라인 다운로드형 launcher — 파이썬 없는 사용자용 **작은 exe** 의 진입점.

PyInstaller 로 이 파일을 얼려 작은 exe 하나(`AOI_Verify_Online.exe`)를 만든다.
사용자가 처음 실행하면 앱 소스를 GitHub 에서 받아 쓰기 가능한 데이터 폴더에 풀고,
requirements 를 인터넷에서 pip 로 설치한 뒤 앱을 실행한다.  이후 실행은 이미 받아둔
것을 바로 쓰며, 앱 내 자동 업데이트가 그 폴더를 갱신한다.

빌드:  scripts\internal\build_online.bat  (PyInstaller onefile)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _ensure_importable() -> None:
    """얼린 exe 안에는 aoi_verification 패키지의 utils 만 동봉된다(작게).
    개발 실행(파이썬)에서는 저장소 루트를 path 에 추가해 import 가능하게 한다."""
    here = Path(__file__).resolve().parent
    root = here.parent                      # scripts/ 의 부모 = 저장소 루트
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def main() -> int:
    _ensure_importable()
    from aoi_verification.app.utils import bootstrap, updater

    frozen = bool(getattr(sys, "frozen", False))
    root = bootstrap.data_root()
    repo, branch = updater.DEFAULT_REPO, updater.DEFAULT_BRANCH

    # 앱·패키지·캐시를 모두 설치 폴더("AOI Recipe Verification") 안에 담기 위해, 앱이
    # 캐시를 이 폴더 아래에 두도록 환경변수로 지정한다(paths.cache_root 가 읽음).
    import os
    os.environ["AOI_DATA_HOME"] = str(root)

    def fetch_app(dest: Path) -> bool:
        # 데이터 폴더를 app_root 로 고정하고 최신 브랜치 zip 을 받아 푼다(=자동 업데이트와 동일 경로).
        updater._app_root = lambda: dest          # type: ignore[attr-defined]
        info = updater.latest_commit(repo, branch)   # VERSION 기록용 최신 SHA(조회 실패 시 빈값)
        sha = str(info.get("sha") or "") if isinstance(info, dict) else ""
        return updater.download_and_apply(repo, branch, sha or "online")

    def run(cmd):
        return subprocess.call(cmd)

    return bootstrap.bootstrap(
        root, repo=repo, branch=branch,
        fetch_app=fetch_app, run=run, log=lambda m: print("[AOI]", m, flush=True),
        frozen=frozen, sys_executable=sys.executable)


if __name__ == "__main__":
    raise SystemExit(main())
