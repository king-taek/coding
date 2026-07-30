"""온라인 다운로드형 launcher — **작은 exe 하나**만 전달하는 배포의 진입점.

PyInstaller 로 이 파일을 얼려 작은 exe(`AOI_Verify_Online.exe`)를 만든다.  첫 실행에
**전부 내려받는다**: 자체 포함 CPython → 앱 소스(GitHub) → 라이브러리(pip).  이후
실행은 받아둔 것을 바로 쓰고, 앱 내 자동 업데이트가 그 폴더를 갱신한다.

**파이썬이 없는 PC 에서도 동작한다** — 예전에는 번들 파이썬을 받지 않아 시스템
``python`` 에 위임했고, 그래서 파이썬 미설치 PC 에서는 그냥 실패했다.

백본 IR 은 앱 트리에 포함돼 함께 내려온다(저장소에 커밋돼 있다) — 없으면 고효율
모드의 GPU 가속이 조용히 CPU 로 떨어진다.

빌드:  python scripts\\build.py online
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
    from aoi_verification.app.utils.bootstrap import PY_STANDALONE_URL

    frozen = bool(getattr(sys, "frozen", False))
    root = bootstrap.data_root()
    # 최초 설치(클론)도 자동 업데이트와 동일하게 **저장소 GitHub 기본 브랜치**를 받는다.
    # (api.github.com 차단 시 updater.DEFAULT_BRANCH 폴백 — _default_branch 내부 처리.)
    repo = updater.DEFAULT_REPO
    branch = updater._default_branch(repo)

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
        frozen=frozen, sys_executable=sys.executable,
        python_url=PY_STANDALONE_URL)


if __name__ == "__main__":
    raise SystemExit(main())
