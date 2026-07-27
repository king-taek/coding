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


# ---------------------------------------------------------------------------
# 로고 리소스
# ---------------------------------------------------------------------------
def logo_path(name: str) -> Path:
    """로고 파일(``logo.ico``·``logo_big.png``·``logo_clear.png``) 의 절대 경로.

    앱 폴더 안(`ui/assets/`)에 두어 포터블 빌드 복사·자동 업데이트 미러에
    자동으로 따라오게 한다."""
    return resource_path(f"aoi_verification/app/ui/assets/{name}")


# ---------------------------------------------------------------------------
# Shared cache directory — always in the user's home, never inside source data
# ---------------------------------------------------------------------------
_CACHE_DIRNAME = ".aoi_verification_cache"


def cache_root() -> Path:
    """캐시(썸네일/특징/임베딩/점수/세션) 폴더.

    기본은 사용자 홈의 ``~/.aoi_verification_cache``.  단, 환경변수 ``AOI_DATA_HOME``
    이 지정되면 그 폴더 아래(``<AOI_DATA_HOME>/cache``)에 둔다 — exe 설치 시 launcher 가
    설치 폴더("AOI Recipe Verification")를 가리키게 해, **앱·패키지·캐시를 모두 한 폴더**
    안에 담기 위함."""
    home = os.environ.get("AOI_DATA_HOME")
    root = (Path(home) / "cache") if home else (Path.home() / _CACHE_DIRNAME)
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


def embedding_cache_dir() -> Path:
    """GPU/OpenVINO 임베딩 벡터(.npy) 영속 캐시 — 재실행 시 재추출 생략(#3).

    썸네일/중간이미지와 달리 1일 TTL 정리 대상이 아니다(재계산 비용이 크고
    원본 mtime 키라 자동 무효화됨)."""
    d = cache_root() / "embeddings"
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


def bundled_torch_home() -> Path | None:
    """동봉된 torchvision 모델 가중치 폴더(``runtime/torch``) — exe 설치에서만.

    앱은 첫 매칭 때 ImageNet 가중치를 ``download.pytorch.org`` 에서 받는데, 사내망은 이
    경로가 막혀 있을 수 있다(PyPI 도 막혀 있다).  그래서 빌드 때 미리 받아 동봉하고
    ``TORCH_HOME`` 을 여기로 돌린다.  **``app\\`` 바깥**이라 업데이트 교체에 쓸리지 않는다.
    동봉본이 없으면(개발/포터블) None — 기존 동작(사용자 홈 캐시) 그대로."""
    home = os.environ.get("AOI_APP_HOME")
    if not home:
        return None
    d = Path(home) / "runtime" / "torch"
    return d if d.is_dir() else None


# ---------------------------------------------------------------------------
# 모델 디렉토리
# ---------------------------------------------------------------------------
def models_dir() -> Path:
    """projection_head 가중치(.pt) 보관 폴더."""
    d = cache_root() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 양식.xlsx 위치 찾기 — ‘양식’ 폴더 안의 ‘양식.xlsx’ 를 우선 탐색한다.
# ---------------------------------------------------------------------------
def template_path() -> Path:
    """엑셀 출력용 ``양식.xlsx`` 의 실제 경로를 찾는다 (없으면 후보 중 가장 가까운 것).

    개발 트리에서는 사용자가 직접 건드리지 않는 파일을 모은 ``dev/`` 안에 둔다.
    포터블/PyInstaller 빌드에서는 호환을 위해 루트·_MEIPASS 후보도 함께 본다."""
    candidates = [
        _project_root() / "dev" / "양식.xlsx",      # 개발 트리(dev/ 로 정리)
        _project_root() / "양식" / "양식.xlsx",      # 폴더형(호환)
        _project_root() / "양식.xlsx",              # 루트 fallback(포터블 빌드)
        resource_path("양식.xlsx"),                 # PyInstaller 번들(_MEIPASS) 대응
    ]
    for c in candidates:
        if c.exists():
            return c
    # 존재하지 않으면 첫 후보 경로를 그대로 돌려준다 (호출자가 존재 여부 검사)
    return candidates[0]


def results_dir() -> Path:
    """결과 엑셀이 저장될 기본 폴더 — 프로젝트 루트의 ‘결과’ 폴더에 자동 생성한다.

    단 'exe + app 폴더' 설치(런처가 ``AOI_APP_HOME`` 을 넘겨준 경우)에서는 **설치 루트
    바로 아래**(=``app\\`` 바깥)에 둔다.  자동 업데이트가 ``app\\`` 을 통째로 새 트리로
    교체하므로, 안에 두면 사용자가 만든 결과 파일이 업데이트 때 사라진다."""
    home = os.environ.get("AOI_APP_HOME")
    d = (Path(home) if home else _project_root()) / "결과"
    d.mkdir(parents=True, exist_ok=True)
    return d
