"""모델 파일 레지스트리.

- 디스크에서 모델 목록 조회
- ``active.txt`` 로 현재 사용 모델 추적 (``basic`` 또는 모델 이름)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..utils import paths


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BASIC = "basic"             # 학습 모델 미사용 (기본 탐지 모드) 식별자
_ACTIVE_FILE = "active.txt"
_WEIGHTS_EXT = ".pt"


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------
@dataclass
class ModelInfo:
    name: str
    weights_path: Path


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def find(name: str) -> Optional[ModelInfo]:
    pt = paths.models_dir() / f"{name}{_WEIGHTS_EXT}"
    return ModelInfo(name=name, weights_path=pt) if pt.is_file() else None


# ---------------------------------------------------------------------------
# Active model pointer
# ---------------------------------------------------------------------------
def _active_file() -> Path:
    return paths.models_dir() / _ACTIVE_FILE


def get_active() -> str:
    try:
        v = _active_file().read_text(encoding="utf-8").strip()
    except OSError:
        return BASIC
    # 파일 실제 존재 검증 — 없으면 basic 으로 fallback
    return v if v and v != BASIC and find(v) is not None else BASIC


def set_active(name: str) -> None:
    if name != BASIC and find(name) is None:
        name = BASIC
    _active_file().write_text(name, encoding="utf-8")
