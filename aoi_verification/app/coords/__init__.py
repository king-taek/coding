"""좌표 기반 매칭 패키지.

우선순위:
    1. camtek_live  — LIVE 파일명에서 직접 파싱 (가장 빠름, 항상 정확)
    2. camtek_ini   — ColorImageGrabingInfo.ini 파싱
    3. kla_info     — KLA .001 정보 파일 파싱
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import camtek_ini, camtek_live, kla_info
from .models import DefectCoord

__all__ = ["resolve", "resolve_batch", "DefectCoord"]

_LOG = logging.getLogger("aoi.coords")


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

    ★ **한 실행 안에서 Camtek 좌표 프레임을 통일한다.**  die pitch 검산은 웨이퍼 폴더마다
    독립이라, ref 웨이퍼는 통과하고 val 웨이퍼는 실패할 수 있다.  그러면 한쪽은 die-내부
    (``row = row_total − y_index``), 다른 쪽은 절대좌표(``row = −Row``)가 돼
    ``(col,row) ±1`` 이웃 게이트가 절대 안 맞고 **그 슬롯이 통째로 '매치 실패'** 가 된다.
    섞이면 통과한 쪽도 절대좌표로 내려 양쪽을 맞춘다(정확도가 낮은 쪽으로 맞추는 게 아니다 —
    절대좌표 비교는 die-내부보다 오히려 엄격하다).
    """
    result: dict = {}
    for p in paths:
        result[p] = resolve(p)

    sources = {c.source for c in result.values() if c is not None}
    if not {"camtek_ini", "camtek_abs"} <= sources:
        return result

    _LOG.warning(
        "이 실행에 die pitch 를 확정한 Camtek 폴더와 못 한 폴더가 섞여 있습니다. "
        "좌표 프레임이 달라 매칭이 전멸하므로 **전부 절대 wafer 좌표로 통일**합니다"
        "(매칭은 정상, die 단위 표기만 불가). die 기하를 못 찾은 폴더는 "
        "dev/diagnose_die_geometry.py 로 확인하세요.")
    for p, c in result.items():
        if c is None or c.source != "camtek_ini":
            continue
        raw = camtek_ini.load_raw_folder(p.parent).get(p.stem.lower())
        result[p] = camtek_ini.abs_coord(*raw) if raw is not None else None
    return result
