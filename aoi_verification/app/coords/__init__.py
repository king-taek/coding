"""좌표 기반 매칭 패키지.

우선순위:
    1. camtek_live  — LIVE 파일명에서 직접 파싱 (가장 빠름, 항상 정확)
    2. camtek_ini   — ColorImageGrabingInfo.ini 파싱
    3. kla_info     — KLA .001 정보 파일 파싱
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import camtek_ini, camtek_live, kla_info
from .models import DefectCoord

__all__ = ["resolve", "resolve_batch", "DefectCoord"]


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """이미지 경로 → DefectCoord. 세 소스를 순서대로 시도, 모두 실패하면 None."""
    coord = camtek_live.resolve(image_path)
    if coord is not None:
        return coord
    coord = camtek_ini.resolve(image_path)
    if coord is not None:
        return coord
    return kla_info.resolve(image_path)


def resolve_batch(paths) -> dict:
    """여러 이미지 경로를 한꺼번에 resolve → {path: DefectCoord | None}.

    INI/KLA 파일은 폴더별로 한 번만 파싱(lru_cache 활용)하므로
    같은 폴더 내 여러 이미지를 반복 파싱하지 않는다.
    """
    result: dict = {}
    for p in paths:
        result[p] = resolve(p)
    return result
