"""IDE(VS Code 등) 와 PyInstaller 빌드 양쪽에서 안전한 경로 헬퍼."""

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

    IDE 의 작업 디렉토리가 어디로 설정돼 있든 일관된 경로를 보장하기 위해
    모든 리소스는 이 헬퍼를 통해 접근해야 한다.  PyInstaller `--onefile`
    모드에서는 `sys._MEIPASS` 가 임시 디렉토리를 가리키므로 그것을 우선 사용.
    """
    rel = Path(relative)

    # 1) PyInstaller frozen executable
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / rel
        if candidate.exists():
            return candidate

    # 2) Repo root (개발 환경)
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


def score_cache_dir() -> Path:
    """(ref, val) 유사도 점수의 슬롯 단위 영속 캐시 폴더 (#5B)."""
    d = cache_root() / "scores"
    d.mkdir(parents=True, exist_ok=True)
    return d


def session_cache_dir() -> Path:
    d = cache_root() / "session"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 학습 / 모델 / 평가 디렉토리
# ---------------------------------------------------------------------------
def training_data_dir() -> Path:
    """매칭 쌍이 누적되는 폴더 (pairs.jsonl 보관)."""
    d = cache_root() / "training_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    """학습된 projection_head 가중치 + 메타 JSON 보관 폴더."""
    d = cache_root() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def evaluations_dir() -> Path:
    """모델별 매칭 결정 로그(.jsonl) 보관 폴더."""
    d = cache_root() / "evaluations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_archive_dir() -> Path:
    """모델 export/import 용 임시 작업 폴더."""
    d = cache_root() / "models_archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def slot_mapping_path() -> Path:
    """수동 슬롯 매핑 룰(JSON) 저장 경로 — 사용자 결정을 영속화."""
    return cache_root() / "slot_mapping.json"


# ---------------------------------------------------------------------------
# 양식.xlsx 위치 찾기 — ‘양식’ 폴더 안의 ‘양식.xlsx’ 를 우선 탐색한다.
# ---------------------------------------------------------------------------
def template_path() -> Path:
    """`양식/양식.xlsx` 의 실제 경로를 찾는다 (없으면 후보 중 가장 가까운 것)."""
    candidates = [
        _project_root() / "양식" / "양식.xlsx",
        package_root().parent / "양식" / "양식.xlsx",
        _project_root() / "양식.xlsx",        # 호환을 위한 fallback
        resource_path("양식.xlsx"),            # PyInstaller 번들(_MEIPASS) 대응
    ]
    for c in candidates:
        if c.exists():
            return c
    # 존재하지 않으면 첫 후보 경로를 그대로 돌려준다 (호출자가 존재 여부 검사)
    return candidates[0]


def template_dir() -> Path:
    """‘양식’ 폴더의 경로 (없을 수 있음 — 호출자가 mkdir 등을 결정)."""
    return _project_root() / "양식"


def results_dir() -> Path:
    """결과 엑셀이 저장될 기본 폴더 — 양식 폴더와 같은 부모 디렉토리.

    프로젝트 루트의 ‘결과’ 폴더에 자동 생성한다.
    """
    d = _project_root() / "결과"
    d.mkdir(parents=True, exist_ok=True)
    return d
