"""Notch 방향 보정 — 스캔 때 웨이퍼가 다르게 놓인 폴더의 좌표를 **같은 방향**으로 돌린다.

장비·스캔마다 notch 가 위/아래/오른쪽/왼쪽으로 달라질 수 있다.  웨이퍼가 90° 단위로 돌아
놓이면 같은 결함이 전혀 다른 (col, row, x, y) 로 기록돼 좌표 매칭이 맞지 않는다.  이 모듈은
notch 가 **아래**인 방향(표준)을 기준으로, 다른 방향으로 스캔한 폴더의 좌표를 웨이퍼 중심을
축으로 돌려 표준 방향의 번호로 바꿔 준다.

★ **매칭 계산 전용**이다(KLA ↔ Camtek 좌표 보정과 같은 규칙).  :func:`apply_notch` 는 새
dict 를 돌려줄 뿐 ``coords.resolve`` 캐시·엑셀·화면 좌표는 건드리지 않는다.

좌표 틀 — die 격자 연속 좌표 ``u = col + x/pitch_x``, ``v = row + 1 − y/pitch_y`` (u 오른쪽,
v 위쪽).  Camtek(INI·LIVE)와 KLA 모두 같은 식이다(``camtek_ini``·``kla_info`` 변환식).
웨이퍼 중심의 격자 좌표 ``(cu, cv)`` 는 폴더의 실제 번호 기준에서 유도한다::

    Camtek  cu = Center_X/pitch_x − col_origin     cv = row_total + 1 − Center_Y/pitch_y
    KLA     cu = cx/pitch_x + zero_x               cv = cy/pitch_y + zero_y

회전 후 번호는 '웨이퍼 안에 온전히 드는 첫 열/마지막 행이 0' 이라는 장비 규칙
(``wafer_geometry._col_origin``·``_row_total``)으로 다시 매긴다.  die 가 직사각형이면 90° 돌 때
pitch 의 가로·세로가 바뀐다.  (근거 등급: 변환식 ``파일`` · 회전 후 번호 규칙 ``유도`` ·
실물 notch 스캔 쌍으로 검증한 적 없음 ``가정``.)  die-내부 좌표의 회전 방향은 실물로 맞았다
(``관측`` 2026-10-28, Kendall RDL4 KLA(아래) ↔ Camtek(오른쪽) 짝 10쌍: ``x_cam = y_kla − 635``,
``y_cam = 26848 − x_kla``, 흩어짐 7~9 µm — :data:`RIGHT` 회전 뒤 평행이동 하나로 맞는 관계).

die 격자 기하(중심·pitch)를 못 구하는 폴더를 돌려야 하면 **추측하지 않고** :class:`NotchError`
를 낸다 — 틀린 좌표로 조용히 매칭하느니 멈추는 편이 낫다.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import kla_info, wafer_geometry as wg
from .models import DEFAULT_WAFER_DIAMETER, DefectCoord

__all__ = ["DOWN", "UP", "LEFT", "RIGHT", "DIRECTIONS", "DEFAULT", "NotchError",
           "Frame", "is_none", "normalize", "detect", "rotate_coord", "notch_for_path",
           "apply_notch"]

_LOG = logging.getLogger("aoi.coords")

DOWN, RIGHT, UP, LEFT = "down", "right", "up", "left"
DIRECTIONS = (UP, LEFT, RIGHT, DOWN)     # 화면 배치 순서(위·왼·오른·아래)
DEFAULT = DOWN                           # 표준 방향 — 이 방향은 건드리지 않는다

_KLA_MARK = re.compile(r"OrientationMarkLocation\s+(\w+)", re.IGNORECASE)


class NotchError(RuntimeError):
    """돌려야 하는데 좌표를 믿고 돌릴 재료(중심·pitch)가 없을 때."""


@dataclass(frozen=True)
class Frame:
    """폴더의 die 격자 기하 — pitch(µm) · 웨이퍼 중심의 격자 좌표 · 웨이퍼 반지름(µm)."""
    px: float
    py: float
    cu: float
    cv: float
    radius: float


def normalize(direction: Optional[str]) -> str:
    d = (direction or "").strip().lower()
    return d if d in DIRECTIONS else DEFAULT


def is_none(direction: Optional[str]) -> bool:
    return normalize(direction) == DEFAULT


def _rot(direction: str, a: float, b: float) -> Tuple[float, float]:
    """notch 가 ``direction`` 인 틀의 벡터를 notch 아래 틀로 돌린다(a 오른쪽 · b 위)."""
    if direction == RIGHT:
        return (b, -a)
    if direction == UP:
        return (-a, -b)
    if direction == LEFT:
        return (-b, a)
    return (a, b)


# ---------------------------------------------------------------------------
# 폴더 기하
# ---------------------------------------------------------------------------
def detect(folder: Path) -> str:
    """폴더에서 notch 방향을 읽는다 — KLA 정보파일 ``OrientationMarkLocation`` 만 믿는다.

    Camtek 은 방향을 담은 값이 확인되지 않았다(``FlatNotchOrientation=3`` 이 119건 전부
    같은 값) → 표준(아래)으로 둔다.  읽지 못하면 표준."""
    try:
        info = kla_info._find_info_file(Path(folder))
        if info is None:
            return DEFAULT
        with info.open("rb") as fh:
            head = fh.read(kla_info._HEAD_BYTES).decode("utf-8", errors="replace")
        m = _KLA_MARK.search(head)
        return normalize(m.group(1)) if m else DEFAULT
    except Exception:
        return DEFAULT


def _kla_frame(folder: Path) -> Optional[Frame]:
    info = kla_info._find_info_file(folder)
    if info is None:
        return None
    with info.open("rb") as fh:
        head = fh.read(kla_info._HEAD_BYTES).decode("utf-8", errors="replace")
    cm = wg._SAMPLE_CENTER_PAT.search(head)
    if cm is None:
        return None
    geom = wg.kla_geometry(folder)
    if geom.source == "fallback":           # DiePitch 를 못 읽어 TB500 값을 댄 것 — 추측하지 않는다
        return None
    sm = wg._SAMPLE_SIZE_PAT.search(head)
    dia = float(sm.group(1)) * 1000.0 if sm else DEFAULT_WAFER_DIAMETER
    cx, cy = float(cm.group(1)), float(cm.group(2))
    return Frame(geom.pitch_x, geom.pitch_y,
                 cx / geom.pitch_x + geom.zero_x, cy / geom.pitch_y + geom.zero_y,
                 dia / 2.0)


def _camtek_frame(folder: Path, live: bool) -> Optional[Frame]:
    geom = wg.live_geometry(folder) if live else wg.camtek_geometry(folder)
    if geom is None:
        return None
    cx, cy = wg._wafer_center(folder)
    if cx is None or cy is None:
        return None
    return Frame(geom.pitch_x, geom.pitch_y,
                 cx / geom.pitch_x - geom.col_origin,
                 geom.row_total + 1 - cy / geom.pitch_y,
                 wg._read_diameter(folder) / 2.0)


def folder_frame(folder: Path, source: str) -> Optional[Frame]:
    try:
        if source == "kla":
            return _kla_frame(folder)
        if source in ("camtek_ini", "camtek_live"):
            return _camtek_frame(folder, live=(source == "camtek_live"))
    except Exception:
        _LOG.warning("notch 보정용 die 격자 기하를 읽지 못했다: %s", folder, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# 회전
# ---------------------------------------------------------------------------
def rotate_coord(c: DefectCoord, direction: str, f: Frame) -> DefectCoord:
    """좌표 하나를 notch 아래 틀로 돌린다(순수 함수).  표준 방향이면 그대로."""
    direction = normalize(direction)
    if direction == DEFAULT:
        return c
    swap = direction in (LEFT, RIGHT)
    px2, py2 = (f.py, f.px) if swap else (f.px, f.py)

    u = c.col + c.x / f.px
    v = c.row + 1.0 - c.y / f.py
    da, db = _rot(direction, (u - f.cu) * f.px, (v - f.cv) * f.py)

    # 돌린 틀의 격자선이 돌린 점들과 맞게 중심의 소수 부분을 정하고, 정수 부분은
    # '온전히 드는 첫 열/마지막 행이 0' 규칙으로 매긴다(모듈 docstring).
    oa, ob = _rot(direction, -f.cu * f.px, -f.cv * f.py)
    r_u, r_v = f.radius / px2, f.radius / py2
    cu2 = r_u - ((r_u - (-oa / px2) % 1.0) % 1.0)
    cv2 = r_v - ((r_v - (-ob / py2) % 1.0) % 1.0)

    u2 = cu2 + da / px2
    v2 = cv2 + db / py2
    col = math.floor(u2 + 1e-9)
    row = math.floor(v2 + 1e-9)
    return replace(c, col=col, row=row,
                   x=(u2 - col) * px2, y=(1.0 - (v2 - row)) * py2)


def notch_for_path(dirs, path: Path) -> str:
    """``dirs`` = ``((폴더 루트, 방향), ...)`` 에서 ``path`` 를 품은 **가장 깊은** 루트의 방향."""
    best, best_len = DEFAULT, -1
    p = Path(path)
    for root, d in dirs or ():
        r = Path(root)
        if (r == p.parent or r in p.parents) and len(r.parts) > best_len:
            best, best_len = normalize(d), len(r.parts)
    return best


def apply_notch(coord_map: Dict[Path, Optional[DefectCoord]],
                notch_of: Callable[[Path], str]) -> Dict[Path, Optional[DefectCoord]]:
    """``notch_of(사진 경로)`` 가 표준이 아닌 사진의 좌표만 돌린 **새** dict.

    원본 dict·:class:`DefectCoord`(frozen) 는 그대로다 — 엑셀·화면 좌표로 새지 않는다.
    돌릴 재료가 없으면 :class:`NotchError`."""
    out: Dict[Path, Optional[DefectCoord]] = {}
    frames: Dict[Tuple[Path, str], Optional[Frame]] = {}
    for p, c in coord_map.items():
        d = normalize(notch_of(p))
        if c is None or d == DEFAULT:
            out[p] = c
            continue
        key = (Path(p).parent, c.source)
        if key not in frames:
            frames[key] = folder_frame(*key)
        f = frames[key]
        if f is None:
            raise NotchError(f"{key[0]} — notch 를 돌릴 웨이퍼 중심·die 크기를 찾지 못했습니다")
        out[p] = rotate_coord(c, d, f)
    return out
