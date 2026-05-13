"""Spyder/IPython 및 PyInstaller 양쪽에서 모두 안전한 경로 헬퍼."""

from __future__ import annotations

import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Resource resolution
# ---------------------------------------------------------------------------
def _project_root() -> Path:
    """`aoi_verification/` 패키지 루트의 부모 (저장소 루트)."""
    return Path(__file__).resolve().parents[3]


def resource_path(relative: str | os.PathLike[str]) -> Path:
    """애플리케이션 리소스(스타일시트, 양식 파일 등) 의 절대 경로를 돌려준다.

    Spyder 의 작업 디렉토리가 예측 불가하기 때문에 모든 리소스는 이 헬퍼를
    통해 접근해야 한다.  PyInstaller `--onefile` 모드에서는 `sys._MEIPASS` 가
    임시 디렉토리를 가리키므로 그것을 우선 사용한다.
    """
    rel = Path(relative)

    # 1) PyInstaller frozen executable
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / rel
        if candidate.exists():
            return candidate

    # 2) Repo root (Spyder/일반 Python 모두 동일)
    candidate = _project_root() / rel
    if candidate.exists():
        return candidate

    # 3) Package-relative fallback
    candidate = Path(__file__).resolve().parents[2] / rel
    return candidate


def package_root() -> Path:
    """`aoi_verification` 패키지 루트 (코드 디렉토리)."""
    return Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Shared cache directory — always in the user's home, never inside source data
# ---------------------------------------------------------------------------
_CACHE_DIRNAME = ".aoi_verification_cache"


def cache_root() -> Path:
    """사용자 홈 디렉토리 아래의 공용 캐시 폴더."""
    root = Path.home() / _CACHE_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def thumb_cache_dir() -> Path:
    d = cache_root() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def mid_cache_dir() -> Path:
    d = cache_root() / "mid"
    d.mkdir(parents=True, exist_ok=True)
    return d


def feature_cache_dir() -> Path:
    d = cache_root() / "features"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_cache_dir() -> Path:
    d = cache_root() / "session"
    d.mkdir(parents=True, exist_ok=True)
    return d
