"""Wafer map 좌표 — 결함 사진을 **웨이퍼 평면 µm 좌표**(중심 기준, +Y 위)로 놓는다.

세 소스(Camtek INI / LIVE 파일명 / KLA .001)가 내는 :class:`DefectCoord` 는 die 인덱스 +
die 내부 좌표라 그대로는 원 안에 찍을 수 없다.  여기서 폴더의 die 기하
(:mod:`.wafer_geometry`)와 웨이퍼 중심으로 **하나의 평면 좌표**로 되돌린다 —
장비가 달라도 같은 평면이라 기준/검증 맵을 같은 눈으로 볼 수 있다.

방향 규약(관측): 장비 화면은 **맵 왼쪽 맨 아래가 (col 0, row 0)** 이고 row 는 위로
는다(:func:`wafer_geometry._row_total`).  그래서 평면 +Y 가 화면 위다.  노치는 아래
고정이다(파일에 각도 정보가 없다 — 가정).

중심 출처 등급:

* ``관측`` — ``Center_X/Y``·``Wafer2Table.ini``(Camtek) / ``SampleCenterLocation``(KLA).
* ``가정`` — 그게 없으면 '온전히 들어오는 첫 die' 규칙을 거꾸로 써서 웨이퍼 가장자리가
  그 die 경계에서 **반 pitch** 안쪽에 있다고 본다.  오차는 최대 ±반 pitch 다.
  (die 인덱스 자체는 이 가정과 무관하게 정확하다 — 점이 원 안에서 통째로 반 pitch
  이하 밀릴 뿐 die 격자와의 상대 위치는 맞는다.)

순수 로직 — Qt 없이 헤드리스 테스트한다(``dev/tests/test_wafer_map.py``).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import kla_info, wafer_geometry as wg
from .models import DefectCoord

__all__ = ["WaferFrame", "MapPoint", "MapData", "frame_for_folder", "to_plane",
           "build_map", "grid_lines", "slot_maps", "ALL_SLOTS_KEY"]

_LOG = logging.getLogger("aoi.coords.wafer_map")

SOURCE_OBSERVED = "관측"
SOURCE_ASSUMED = "가정"


@dataclass(frozen=True)
class WaferFrame:
    """한 폴더(=한 웨이퍼 스캔)의 평면 기하.

    격자 경계는 ``grid_x0 + k·pitch_x`` / ``grid_y0 + k·pitch_y`` (k 는 임의 정수) —
    위상만 있으면 되므로 원점을 따로 두지 않는다.  ``pitch_*`` 가 ``None`` 이면 격자를
    모른다(절대좌표 폴더)."""
    diameter: float
    pitch_x: Optional[float]
    pitch_y: Optional[float]
    grid_x0: float
    grid_y0: float
    center_source: str          # SOURCE_OBSERVED | SOURCE_ASSUMED
    kind: str                   # "camtek" | "kla"

    @property
    def radius(self) -> float:
        return self.diameter / 2.0


@dataclass(frozen=True)
class MapPoint:
    path: Path
    x: float                    # 평면 µm(중심 기준, +Y 위)
    y: float
    col: Optional[int]
    row: Optional[int]
    matched: Optional[bool]     # None = 매칭 정보 없음(셋업 단계)


@dataclass(frozen=True)
class MapData:
    frame: Optional[WaferFrame]     # 격자·원 크기의 기준(첫 번째로 놓인 폴더의 것)
    points: tuple[MapPoint, ...]
    unplaced: tuple[Path, ...]      # 좌표를 못 놓은 사진


# ---------------------------------------------------------------------------
# 폴더 → 프레임
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Camtek:
    geom: Optional[wg.CamtekGeometry]
    diameter: float
    cx: Optional[float]         # stage 중심(Y 아래로 증가)
    cy: Optional[float]
    source: str


@dataclass(frozen=True)
class _Kla:
    geom: wg.KlaGeometry
    diameter: float
    cx: float                   # KLA 인덱스 프레임 중심(Y 위로 증가)
    cy: float
    source: str


def _assumed_edge(first_full_index: int, pitch: float) -> float:
    """'온전히 들어오는 첫 die' 경계에서 반 pitch 바깥 = 가정한 웨이퍼 가장자리."""
    return first_full_index * pitch - pitch / 2.0


@lru_cache(maxsize=256)
def _camtek(folder: Path) -> _Camtek:
    geom = wg.camtek_geometry(folder)
    dia = wg._read_diameter(folder)
    cx, cy = wg._wafer_center(folder)
    source = SOURCE_OBSERVED
    if (cx is None or cy is None) and geom is not None:
        source = SOURCE_ASSUMED
        if cx is None:
            cx = _assumed_edge(geom.col_origin, geom.pitch_x) + dia / 2.0
        if cy is None:
            # row_total 은 '온전히 들어오는 마지막 행' — 그 아래 경계에서 반 pitch 바깥.
            cy = (geom.row_total + 1) * geom.pitch_y + geom.pitch_y / 2.0 - dia / 2.0
    return _Camtek(geom=geom, diameter=dia, cx=cx, cy=cy, source=source)


@lru_cache(maxsize=256)
def _kla(folder: Path) -> Optional[_Kla]:
    geom = wg.kla_geometry(folder)
    dia = wg.DEFAULT_WAFER_DIAMETER
    cx = cy = None
    try:
        info = kla_info._find_info_file(folder)
        if info is not None:
            with info.open("rb") as fh:
                head = fh.read(kla_info._HEAD_BYTES).decode("utf-8", errors="replace")
            sm = wg._SAMPLE_SIZE_PAT.search(head)
            if sm:
                d = float(sm.group(1)) * 1000.0
                if wg._MIN_DIAMETER <= d <= wg._MAX_DIAMETER:
                    dia = d
            cm = wg._SAMPLE_CENTER_PAT.search(head)
            if cm:
                cx, cy = float(cm.group(1)), float(cm.group(2))
    except Exception:
        pass
    if cx is not None and cy is not None:
        return _Kla(geom=geom, diameter=dia, cx=cx, cy=cy, source=SOURCE_OBSERVED)
    return _Kla(geom=geom, diameter=dia,
                cx=_assumed_edge(-geom.zero_x, geom.pitch_x) + dia / 2.0,
                cy=_assumed_edge(-geom.zero_y, geom.pitch_y) + dia / 2.0,
                source=SOURCE_ASSUMED)


def frame_for_folder(folder: Path, kind: str) -> Optional[WaferFrame]:
    """폴더의 평면 기하.  ``kind`` 는 ``"camtek"`` / ``"kla"``.  못 만들면 None."""
    folder = Path(folder)
    if kind == "kla":
        k = _kla(folder)
        if k is None:
            return None
        return WaferFrame(diameter=k.diameter, pitch_x=k.geom.pitch_x,
                          pitch_y=k.geom.pitch_y, grid_x0=-k.cx, grid_y0=-k.cy,
                          center_source=k.source, kind="kla")
    c = _camtek(folder)
    if c.cx is None or c.cy is None:
        return None
    px = c.geom.pitch_x if c.geom is not None else None
    py = c.geom.pitch_y if c.geom is not None else None
    # stage y 는 아래로 증가 → 평면 y = cy − stage_y.  경계 stage_y = k·py 는
    # 평면에서 cy − k·py 라 위상은 cy 다.
    return WaferFrame(diameter=c.diameter, pitch_x=px, pitch_y=py,
                      grid_x0=-c.cx, grid_y0=c.cy,
                      center_source=c.source, kind="camtek")


def _kind_of(coord: DefectCoord) -> str:
    return "kla" if coord.source == "kla" else "camtek"


def to_plane(coord: DefectCoord, folder: Path) -> Optional[tuple[float, float]]:
    """DefectCoord → 평면 (x, y) µm.  기하를 못 찾으면 None.

    * camtek_ini / camtek_live: stage x = (col + col_origin)·px + x,
      stage y = (row_total − row)·py + y  → (sx − cx, cy − sy)
    * camtek_abs: (x, y) 가 이미 stage 절대좌표 → (x − cx, cy − y)
    * kla: 인덱스 프레임 x = (col − zero_x)·px + x,  y = (row − zero_y)·py + (py − y)
      (``DefectCoord.y = py − YREL`` 이라 되돌린다) → (kx − cx, ky − cy)
    """
    folder = Path(folder)
    try:
        if coord.source == "kla":
            k = _kla(folder)
            if k is None:
                return None
            g = k.geom
            kx = (coord.col - g.zero_x) * g.pitch_x + coord.x
            ky = (coord.row - g.zero_y) * g.pitch_y + (g.pitch_y - coord.y)
            return (kx - k.cx, ky - k.cy)
        c = _camtek(folder)
        if c.cx is None or c.cy is None:
            return None
        if coord.source == "camtek_abs":
            return (coord.x - c.cx, c.cy - coord.y)
        if c.geom is None:
            return None
        g = c.geom
        sx = (coord.col + g.col_origin) * g.pitch_x + coord.x
        sy = (g.row_total - coord.row) * g.pitch_y + coord.y
        return (sx - c.cx, c.cy - sy)
    except Exception:
        return None


def build_map(coords: dict, matched=None) -> MapData:
    """``{path: DefectCoord | None}`` → :class:`MapData`.

    ``matched`` 가 None 이면 모든 점의 ``matched`` 가 None(매칭 정보 없음), 아니면
    그 집합에 든 경로만 True.  여러 폴더(LOT 합산)를 섞어도 된다 — 점은 전부 평면
    좌표라 그대로 겹치고, 격자·원은 **첫 번째로 놓인 폴더**의 프레임을 쓴다.
    """
    points: list[MapPoint] = []
    unplaced: list[Path] = []
    frame: Optional[WaferFrame] = None
    for path, coord in coords.items():
        path = Path(path)
        if coord is None:
            unplaced.append(path)
            continue
        xy = to_plane(coord, path.parent)
        if xy is None:
            unplaced.append(path)
            continue
        if frame is None:
            frame = frame_for_folder(path.parent, _kind_of(coord))
        die_known = coord.source != "camtek_abs"
        points.append(MapPoint(
            path=path, x=xy[0], y=xy[1],
            col=coord.col if die_known else None,
            row=coord.row if die_known else None,
            matched=None if matched is None else (path in matched),
        ))
    return MapData(frame=frame, points=tuple(points), unplaced=tuple(unplaced))


def grid_lines(frame: WaferFrame) -> tuple[list[float], list[float]]:
    """원 안을 지나는 die 경계선 위치 — (세로선 x 목록, 가로선 y 목록), 평면 µm.

    die 가 8만 개(한 변 ~300)여도 선은 몇백 개라 그리기 비용이 거의 없다."""
    r = frame.radius

    def axis(x0: Optional[float], pitch: Optional[float]) -> list[float]:
        if not pitch or pitch <= 0:
            return []
        k_lo = math.ceil((-r - x0) / pitch)
        k_hi = math.floor((r - x0) / pitch)
        return [x0 + k * pitch for k in range(k_lo, k_hi + 1)]

    return axis(frame.grid_x0, frame.pitch_x), axis(frame.grid_y0, frame.pitch_y)


# ---------------------------------------------------------------------------
# 결과(FinalResult) → 슬롯별 / LOT 합산 맵
# ---------------------------------------------------------------------------
ALL_SLOTS_KEY = ""      # '전체(LOT 합산)' 를 뜻하는 슬롯 키


def slot_maps(result, slot: str = ALL_SLOTS_KEY) -> tuple[MapData, MapData]:
    """결과의 한 슬롯(또는 ``ALL_SLOTS_KEY`` = 전체)에 대한 (기준 맵, 검증 맵).

    ``result`` 는 :class:`~..models.result.FinalResult` — ``slot_images`` 와 ``matches``
    만 쓴다.  결과 화면과 엑셀 시트가 **같은 함수**로 만든다."""
    from . import resolve_batch          # 순환 import 회피(패키지 __init__)

    names = list(result.slot_images) if slot == ALL_SLOTS_KEY else [slot]
    ref_paths: list[Path] = []
    val_paths: list[Path] = []
    for n in names:
        r, v = result.slot_images.get(n, ((), ()))
        ref_paths += list(r)
        val_paths += list(v)
    matched = {Path(m.ref_path) for m in result.matches if m.slot in names}
    matched |= {Path(m.val_path) for m in result.matches if m.slot in names}
    return (build_map(resolve_batch(ref_paths), matched),
            build_map(resolve_batch(val_paths), matched))
