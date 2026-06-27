"""KLA .001 정보 파일 파싱 → DefectCoord.

변환식 (TB500 기준):
    col = XINDEX + KLA_ZERO_X   (= XINDEX + 3)
    row = YINDEX + KLA_ZERO_Y   (= YINDEX + 3)
    x   = round(XREL)
    y   = round(DiePitchY - YREL)

파일 선택 우선순위:
    1. 확장자 .001 파일
    2. .jpg / .pass 가 아닌 파일(단, 폴더 내에 1개만 있을 때)
    단, .pass 파일은 무시.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import DefectCoord, KLA_ZERO_X, KLA_ZERO_Y

__all__ = ["resolve", "load_folder"]

# DefectList 행: 공백/탭 구분 숫자 필드
# 필드 순서(0-based): 0=ID, 1=X, 2=Y, 3=XREL, 4=YREL, 5=XINDEX, 6=YINDEX, ...
_DEFECT_PAT = re.compile(
    r'^\s*([\d.eE+\-]+)'   # 0: ID
    r'\s+([\d.eE+\-]+)'    # 1: X
    r'\s+([\d.eE+\-]+)'    # 2: Y
    r'\s+([\d.eE+\-]+)'    # 3: XREL
    r'\s+([\d.eE+\-]+)'    # 4: YREL
    r'\s+([-\d]+)'         # 5: XINDEX
    r'\s+([-\d]+)'         # 6: YINDEX
)

# DiePitch X Y; 헤더 라인
_DIEPITCH_PAT = re.compile(r'DiePitch\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)', re.IGNORECASE)

# TiffFileName 라인 (파일명 추출)
_TIFF_PAT = re.compile(r'TiffFileName\s+(\S+)', re.IGNORECASE)


def _find_info_file(folder: Path) -> Optional[Path]:
    """폴더에서 KLA .001 정보 파일 탐색."""
    # 1순위: .001 확장자
    for p in folder.glob("*.001"):
        return p
    # 2순위: .jpg/.pass 가 아닌 파일 (확장자 없는 파일 포함), .pass 제외
    candidates = [
        p for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() not in ('.jpg', '.jpeg', '.pass', '.ini', '.py')
        and not p.name.startswith('.')
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


@lru_cache(maxsize=256)
def load_folder(folder: Path) -> dict[str, DefectCoord]:
    """폴더의 KLA 정보 파일을 파싱해 {stem(소문자) → DefectCoord} 맵 반환."""
    info = _find_info_file(folder)
    if info is None:
        return {}
    try:
        return _parse_info(info)
    except Exception:
        return {}


def _parse_info(path: Path) -> dict[str, DefectCoord]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # DiePitchY 추출
    die_pitch_y: Optional[float] = None
    for line in lines:
        m = _DIEPITCH_PAT.search(line)
        if m:
            # DiePitch X Y → 두 번째 값이 Y pitch
            die_pitch_y = float(m.group(2))
            break

    if die_pitch_y is None:
        return {}

    result: dict[str, DefectCoord] = {}
    current_stem: Optional[str] = None

    for line in lines:
        # TiffFileName 라인 → 현재 이미지 stem 갱신
        tm = _TIFF_PAT.match(line)
        if tm:
            tiff_name = tm.group(1).strip()
            current_stem = Path(tiff_name).stem.lower()
            continue

        # DefectList 데이터 행
        if current_stem is not None:
            dm = _DEFECT_PAT.match(line)
            if dm:
                try:
                    xrel = float(dm.group(4))
                    yrel = float(dm.group(5))
                    xindex = int(dm.group(6))
                    yindex = int(dm.group(7))
                except ValueError:
                    continue
                col = xindex + KLA_ZERO_X
                row = yindex + KLA_ZERO_Y
                x = round(xrel)
                y = round(die_pitch_y - yrel)
                result[current_stem] = DefectCoord(
                    col=col, row=row, x=float(x), y=float(y), source="kla"
                )
                current_stem = None  # 한 TiffFileName 당 하나의 DefectList 행

    return result


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """이미지 1장 → DefectCoord. 정보 파일이 없거나 섹션이 없으면 None."""
    coords = load_folder(image_path.parent)
    return coords.get(image_path.stem.lower())
