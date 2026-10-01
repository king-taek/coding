"""Wafer map 좌표 — 결함 사진을 **웨이퍼 평면 µm 좌표**(중심 기준, +Y 위)로 놓는다.

세 소스(Camtek INI / LIVE 파일명 / KLA .001)가 내는 :class:`DefectCoord` 는 die 인덱스 +
die 내부 좌표라 그대로는 원 안에 찍을 수 없다.  여기서 폴더의 die 기하
(:mod:`.wafer_geometry`)와 웨이퍼 중심으로 **하나의 평면 좌표**로 되돌린다 —
장비가 달라도 같은 평면이라 기준/검증 맵을 같은 눈으로 볼 수 있다.

방향 규약(관측): 장비 화면은 **맵 왼쪽 맨 아래가 (col 0, row 0)** 이고 row 는 위로
는다(:func:`wafer_geometry._row_total`).  그래서 평면 +Y 가 화면 위다.  노치 방향은
파일에 없어(가정) 평면에서 **아래**로 둔다 — 실물과 다르면 화면에서 90° 단위로 돌린다
(:class:`~..ui.widgets.wafer_map_view.WaferMapView`).  회전은 보기 상태일 뿐이라 여기
평면 좌표는 그대로다.

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

from . import kla_info, wafer_geometry as wg, wafer_txt
from .models import DefectCoord
from ..models.result import EXTRACT_MODE

__all__ = ["WaferFrame", "MapPoint", "MapData", "frame_for_folder", "to_plane",
           "build_map", "grid_lines", "die_grid_segments", "cell_of",
           "cell_bounds", "defect_cells", "slot_maps", "ALL_SLOTS_KEY", "map_warnings",
           "WARN_OFF_DIE", "WARN_MIXED_FRAMES", "WARN_UNPLACED", "WARN_PITCH_ASSUMED",
           "WARN_LIVE_UNVERIFIED"]

_LOG = logging.getLogger("aoi.coords.wafer_map")

SOURCE_OBSERVED = "관측"
SOURCE_ASSUMED = "가정"


@dataclass(frozen=True)
class WaferFrame:
    """한 폴더(=한 웨이퍼 스캔)의 평면 기하.

    격자 경계는 ``grid_x0 + k·pitch_x`` / ``grid_y0 + k·pitch_y`` (k 는 임의 정수) —
    위상만 있으면 되므로 원점을 따로 두지 않는다.  ``pitch_*`` 가 ``None`` 이면 격자를
    모른다(절대좌표 폴더).

    ``die_cells`` 는 **장비 die 맵에 실제로 있는 칸**(출처 등급 ``파일``) — ``(kx, ky)`` 는
    그 칸의 왼쪽·아래 경계가 ``grid_x0 + kx·pitch_x`` / ``grid_y0 + ky·pitch_y`` 라는 뜻.
    ``None`` 이면 맵이 없거나 못 믿는 폴더라 격자를 **계산**(원 안에 온전히 드는 die)으로
    그린다 — 화면은 그 사실을 표기한다."""
    diameter: float
    pitch_x: Optional[float]
    pitch_y: Optional[float]
    grid_x0: float
    grid_y0: float
    center_source: str          # SOURCE_OBSERVED | SOURCE_ASSUMED
    kind: str                   # "camtek" | "kla"
    die_cells: Optional[frozenset] = None
    pitch_assumed: bool = False  # pitch 가 파일이 아니라 사진 좌표로 추정 — LIVE 전용 폴더

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
    # 점을 놓은 폴더들의 격자(pitch·위상)가 첫 폴더와 다르다 — LOT 합산에서 다른 슬롯
    # 점이 엉뚱한 칸에 찍힌다(실측: 파일명 (3,3) 이 (9,3) 칸).  :func:`map_warnings`.
    mixed_frames: bool = False
    # LIVE 파일명(`camtek_live`)으로 놓은 점 수 — 그 경로는 아직 검증이 충분하지 않다
    # (사용자 지시: 맵을 볼 때마다 재검토를 당부한다).
    live_points: int = 0


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
    cells: Optional[frozenset] = None   # die 맵의 (x_index, y_index) — stage 인덱스
    pitch_assumed: bool = False


@dataclass(frozen=True)
class _Kla:
    geom: wg.KlaGeometry
    diameter: float
    cx: float                   # KLA 인덱스 프레임 중심(Y 위로 증가)
    cy: float
    source: str


# LOT 범위를 넓힐 때 훑는 형제 폴더 상한 — 엉뚱한 상위 폴더를 골랐을 때 멈추지 않게.
_MAX_SIBLINGS = 200


def _live_extent(folder: Path) -> Optional[tuple[float, float]]:
    """같은 LOT 의 LIVE 사진 die 내부 x/y 최댓값 — 이 슬롯 + **형제 슬롯 폴더 전부**.

    ★ 슬롯 하나의 사진만 보면 사진 수에 따라 추정이 제각각이라(실측: 같은 LOT 에서
    1,632 ~ 14,942 µm) slot 을 바꿀 때마다 격자가 바뀌고, LOT 합산 화면에서는 첫 슬롯
    격자 위에 다른 슬롯 점이 엉뚱한 칸에 떨어졌다((3,3)→(9,3)).  같은 LOT 의 사진은
    :func:`camtek_live.lot_key` 로 묶는다 — 다른 자재가 섞인 상위 폴더에서도 안전하다."""
    return _lot_extent(folder.parent, _folder_lot_key(folder))


def _folder_lot_key(folder: Path):
    from .camtek_live import lot_key
    try:
        for f in folder.iterdir():
            k = lot_key(f.stem)
            if k is not None:
                return k
    except OSError:
        pass
    return None


@lru_cache(maxsize=64)
def _lot_extent(parent: Path, key) -> Optional[tuple[float, float]]:
    from .camtek_live import lot_key, parse_live_name
    if key is None:
        return None
    xs: list[float] = []
    ys: list[float] = []
    try:
        dirs = sorted(d for d in parent.iterdir() if d.is_dir())[:_MAX_SIBLINGS]
    except OSError:
        return None
    for d in dirs:
        try:
            names = [f.stem for f in d.iterdir()]
        except OSError:
            continue
        for stem in names:
            got = parse_live_name(stem)
            if got is not None and lot_key(stem) == key:
                xs.append(got.x)
                ys.append(got.y)
    return (max(xs), max(ys)) if xs else None


def _live_pitch_estimate(folder: Path) -> Optional[tuple[float, float]]:
    """die 크기 파일이 없을 때 — **같은 LOT** LIVE 파일명의 die 내부 x/y 최댓값으로 pitch 를 추정.

    die 내부 좌표는 pitch 를 넘을 수 없으므로 최댓값은 **하한**이다(추정 등급 ``가정``).
    5 % 여유를 둬 최댓값 사진이 옆 die 경계에 걸리지 않게 한다.  사진이 많을수록 실제
    pitch 에 가까워지고, col/row(파일명 값)는 추정과 무관하게 정확하다.
    상수(TB500)를 쓰지 않는 이유: 다른 자재에서 조용히 엉뚱한 자리에 찍힌다."""
    ext = _live_extent(folder)
    if ext is None:
        return None
    px, py = ext[0] * 1.05, ext[1] * 1.05
    if not (wg._MIN_PITCH <= px <= wg._MAX_PITCH and wg._MIN_PITCH <= py <= wg._MAX_PITCH):
        return None
    return px, py


def _txt_pitch(wt: "wafer_txt.WaferTxt", folder: Path
               ) -> tuple[Optional[tuple[float, float]], bool]:
    """웨이퍼 맵 .txt 슬롯의 pitch — ``((px, py), 추정여부)``.

    ``XDIES/YDIES`` 는 die **크기**라 간격보다 스크라이브 폭만큼 작지만(T254 0.8 %) 간격이
    파일에 없어 그대로 격자 간격으로 쓴다.  **같은 LOT 사진이 전부 그 안에 들어갈 때만**
    쓴다(같은 웨이퍼라도 파일마다 다른 값이 관측됐다 — :mod:`.wafer_txt`).  아니면 LOT
    사진으로 추정하고 :func:`_fit_to_wafer` 로 맵을 웨이퍼 원에 맞춘다."""
    ext = _live_extent(folder)
    if wt.die_x and wt.die_y and (ext is None or (ext[0] < wt.die_x and ext[1] < wt.die_y)):
        return (wt.die_x, wt.die_y), False
    if wt.die_x and wt.die_y:
        _LOG.warning("웨이퍼 맵 %s 의 XDIES/YDIES(%.1f/%.1f µm)보다 큰 사진 좌표(%.1f/%.1f)가 "
                     "있어 die 크기로 쓰지 않고 사진으로 추정한다", wt.path, wt.die_x,
                     wt.die_y, ext[0], ext[1])
    est = _live_pitch_estimate(folder)
    return (est, True) if est is not None else (None, False)


# die 맵이 웨이퍼 원을 채우는 비율 — 최외곽 die 모서리 / 반경.  ``XDIES`` 가 있던 실측
# 5 LOT(AST254·AST256·KENDALL 2·ASGH100)이 0.95~1.00(평균 0.97) 이었다(유도).
_FILL = 0.97


def _fit_to_wafer(cells, px: float, py: float, diameter: float,
                  ext: Optional[tuple[float, float]]) -> tuple[float, float]:
    """사진으로 추정한 pitch 를 **die 맵이 웨이퍼 원을 채우도록** 배율 조정한다.

    사진 최댓값 × 1.05 는 하한일 뿐이라 사진이 적은 LOT 은 맵이 원 가운데에 작게
    그려지고(실측 69 mm / 반경 150), 여유 5 % 때문에 원 밖으로 나가기도 했다(158 mm).
    가로세로 비는 사진 추정을 따르고, 사진 좌표보다 작아지지는 않는다(칸이 바뀌지 않게).
    칸(col/row)은 파일명·맵에서 오므로 이 조정과 무관하다."""
    i_lo = min(i for i, _ in cells)
    i_hi = max(i for i, _ in cells) + 1
    j_lo = min(j for _, j in cells)
    j_hi = max(j for _, j in cells) + 1
    ci, cj = (i_lo + i_hi) / 2.0, (j_lo + j_hi) / 2.0
    far = max(math.hypot((i + a - ci) * px, (j + b - cj) * py)
              for i, j in cells for a in (0, 1) for b in (0, 1))
    if far <= 0:
        return px, py
    s = _FILL * diameter / 2.0 / far
    lo_x, lo_y = (ext[0] * 1.001, ext[1] * 1.001) if ext else (0.0, 0.0)
    return max(px * s, lo_x), max(py * s, lo_y)


def _assumed_edge(first_full_index: int, pitch: float) -> float:
    """'온전히 들어오는 첫 die' 경계에서 반 pitch 바깥 = 가정한 웨이퍼 가장자리."""
    return first_full_index * pitch - pitch / 2.0


@lru_cache(maxsize=256)
def _camtek(folder: Path) -> _Camtek:
    geom = wg.camtek_geometry(folder)
    pitch_assumed = False
    # INI 항목이 **없는** 폴더(LIVE 파일명 슬롯)는 검산 재료가 없어 위가 늘 None 이다 —
    # 그러면 LIVE 사진이 전부 '좌표 없음' 이 된다.  파일 pitch 로, 없으면 사진 좌표로
    # 추정한 pitch 로 기하를 만든다.  INI 항목이 있는데 None 인 폴더(절대좌표)는 그대로.
    wt = None
    if geom is None and not wg.has_camtek_entries(folder):
        geom = wg.live_geometry(folder)
        if geom is None:
            # 1순위: LOT 폴더의 웨이퍼 맵 .txt — die 칸과 col/row 기준을 장비 맵에서 읽는다.
            wt = wafer_txt.load(folder)
            pitch = None
            if wt is not None:
                pitch, pitch_assumed = _txt_pitch(wt, folder)
                if pitch is None:
                    wt = None
            if wt is not None and pitch_assumed:
                pitch = _fit_to_wafer(wt.cells, *pitch, wg._read_diameter(folder),
                                      _live_extent(folder))
            if wt is not None:
                # 파일명 col 이 곧 맵 열, stage y 칸 j = rows−1−row (위에서부터) — 원점 보정 없음.
                geom = wg.CamtekGeometry(pitch_x=pitch[0], pitch_y=pitch[1], col_origin=0,
                                         row_total=wt.rows - 1, source=wt.path.name)
            else:
                est = _live_pitch_estimate(folder)
                if est is not None:
                    geom = wg._with_origins(folder, *est, "LIVE 사진 좌표 추정")
                    pitch_assumed = True
    dia = wg._read_diameter(folder)
    cx, cy = wg._wafer_center(folder)
    source = SOURCE_OBSERVED
    if wt is not None and (cx is None or cy is None):
        # 맵에 중심이 없다 — die 맵 외곽의 가운데를 웨이퍼 중심으로 가정한다.
        source = SOURCE_ASSUMED
        i_lo = min(i for i, _ in wt.cells)
        i_hi = max(i for i, _ in wt.cells) + 1
        j_lo = min(j for _, j in wt.cells)
        j_hi = max(j for _, j in wt.cells) + 1
        cx = (i_lo + i_hi) / 2.0 * geom.pitch_x if cx is None else cx
        cy = (j_lo + j_hi) / 2.0 * geom.pitch_y if cy is None else cy
    if (cx is None or cy is None) and geom is not None:
        source = SOURCE_ASSUMED
        if cx is None:
            cx = _assumed_edge(geom.col_origin, geom.pitch_x) + dia / 2.0
        if cy is None:
            # row_total 은 '온전히 들어오는 마지막 행' — 그 아래 경계에서 반 pitch 바깥.
            cy = (geom.row_total + 1) * geom.pitch_y + geom.pitch_y / 2.0 - dia / 2.0
    # die 맵은 **기하가 그 맵을 채택했을 때만** 쓴다 — 부분 맵(유도값과 ±1 밖)은
    # camtek_geometry 가 이미 버렸고, 그때는 격자도 계산으로 그린다.
    cells = None
    if wt is not None:
        cells = wt.cells
    elif geom is not None and geom.source.endswith(wg._DIE_MAP_FILE):
        cells = wg.die_map_cells(folder, geom.pitch_x, geom.pitch_y)
    return _Camtek(geom=geom, diameter=dia, cx=cx, cy=cy, source=source, cells=cells,
                   pitch_assumed=pitch_assumed)


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
    # 평면에서 cy − k·py 라 위상은 cy 다.  stage 칸 j 는 [j·py, (j+1)·py] 라 평면에서
    # 아래 경계가 cy − (j+1)·py → ky = −(j+1).
    cells = None
    if c.cells is not None:
        cells = frozenset((i, -(j + 1)) for i, j in c.cells)
    return WaferFrame(diameter=c.diameter, pitch_x=px, pitch_y=py,
                      grid_x0=-c.cx, grid_y0=c.cy,
                      center_source=c.source, kind="camtek", die_cells=cells,
                      pitch_assumed=c.pitch_assumed)


def _kind_of(coord: DefectCoord) -> str:
    return "kla" if coord.source == "kla" else "camtek"


def to_plane(coord: DefectCoord, folder: Path) -> Optional[tuple[float, float]]:
    """DefectCoord → 평면 (x, y) µm.  기하를 못 찾으면 None.

    * camtek_ini / camtek_live: stage x = (col + col_origin)·px + x,
      stage y = (row_total − row)·py + y  → (sx − cx, cy − sy)
    * camtek_live 인데 폴더에 INI 항목이 없으면 기하는 :func:`~.wafer_geometry.live_geometry`
      — 그것도 없으면 폴더 LIVE 사진의 die 내부 좌표 최댓값으로 pitch 를 추정(가정)
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
    live = 0
    frame: Optional[WaferFrame] = None
    folder_frames: dict = {}
    for path, coord in coords.items():
        path = Path(path)
        if coord is None:
            unplaced.append(path)
            continue
        xy = to_plane(coord, path.parent)
        if xy is None:
            unplaced.append(path)
            continue
        key = (path.parent, _kind_of(coord))
        if key not in folder_frames:
            folder_frames[key] = frame_for_folder(*key)
        if frame is None:
            frame = folder_frames[key]
        die_known = coord.source != "camtek_abs"
        live += coord.source == "camtek_live"
        points.append(MapPoint(
            path=path, x=xy[0], y=xy[1],
            col=coord.col if die_known else None,
            row=coord.row if die_known else None,
            matched=None if matched is None else (path in matched),
        ))
    grid = lambda f: None if f is None else (f.pitch_x, f.pitch_y,      # noqa: E731
                                             f.grid_x0, f.grid_y0)
    mixed = len({grid(f) for f in folder_frames.values()}) > 1
    return MapData(frame=frame, points=tuple(points), unplaced=tuple(unplaced),
                   mixed_frames=mixed, live_points=live)


# ---------------------------------------------------------------------------
# 경고 — 실제와 다르게 그려졌을 수 있는 맵
# ---------------------------------------------------------------------------
WARN_OFF_DIE = "off_die"                # die 가 없는 칸(또는 원 밖)에 찍힌 점이 있다
WARN_MIXED_FRAMES = "mixed_frames"      # 합친 폴더들의 격자가 서로 다르다
WARN_UNPLACED = "unplaced"              # 좌표를 못 놓은 사진이 있다
WARN_PITCH_ASSUMED = "pitch_assumed"    # die 크기를 사진으로 추정했다(크기·외곽 근사)
WARN_LIVE_UNVERIFIED = "live_unverified"  # LIVE 파일명으로 그린 맵 — 검증이 아직 충분하지 않다


def _cell_drawn(frame: WaferFrame, cell: tuple[int, int]) -> bool:
    """그 칸이 화면에 die 로 그려지는가 — :func:`die_grid_segments` 와 같은 기준."""
    if frame.die_cells:
        return cell in frame.die_cells
    x0, y0, x1, y1 = cell_bounds(frame, cell)
    r2 = frame.radius ** 2
    return all(x * x + y * y <= r2 for x in (x0, x1) for y in (y0, y1))


def map_warnings(data: Optional[MapData]) -> list[tuple[str, int]]:
    """이 맵이 실제와 다르게 그려졌을 수 있는 이유 — ``[(코드, 해당 사진 수)]``, 심각한 순.

    **칸이 틀렸을 수 있는 것**만 고른다.  중심 가정·계산 격자는 거의 모든 LIVE·INI
    폴더에 붙어 늘 뜨면 무시하게 되므로 범례에만 둔다(:class:`WaferFrame` 플래그).
    단 **LIVE 파일명으로 그린 맵은 늘 경고한다**(사용자 지시) — 그 경로의 검증이 아직
    충분하지 않아, 중요한 정보는 재검토하라고 매번 알린다.
    순수 — 헤드리스 테스트한다."""
    if data is None:
        return []
    out: list[tuple[str, int]] = []
    fr = data.frame
    if fr is not None and fr.pitch_x and fr.pitch_y:
        off = sum(1 for p in data.points
                  if not _cell_drawn(fr, cell_of(fr, p.x, p.y)))
        if off:
            out.append((WARN_OFF_DIE, off))
    if data.mixed_frames:
        out.append((WARN_MIXED_FRAMES, len(data.points)))
    if data.unplaced:
        out.append((WARN_UNPLACED, len(data.unplaced)))
    if fr is not None and fr.pitch_assumed:
        out.append((WARN_PITCH_ASSUMED, len(data.points)))
    if data.live_points:
        out.append((WARN_LIVE_UNVERIFIED, data.live_points))
    return out


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


def _cell_segments(frame: WaferFrame) -> list[tuple[float, float, float, float]]:
    """``frame.die_cells`` 의 칸을 감싸는 선분 — 한 경계선 위에서 이어지는 변은 하나로."""
    cells = frame.die_cells
    x_at = lambda k: frame.grid_x0 + k * frame.pitch_x          # noqa: E731
    y_at = lambda k: frame.grid_y0 + k * frame.pitch_y          # noqa: E731

    def runs(edges: set) -> list[tuple[int, int, int]]:
        """``{(선, 칸)}`` → ``(선, 시작칸, 끝칸+1)`` — 연속한 칸을 한 구간으로."""
        out = []
        for line, k in sorted(edges):
            if out and out[-1][0] == line and out[-1][2] == k:
                out[-1] = (line, out[-1][1], k + 1)
            else:
                out.append((line, k, k + 1))
        return out

    vert = {(i + d, j) for i, j in cells for d in (0, 1)}
    horz = {(j + d, i) for i, j in cells for d in (0, 1)}
    segs = [(x_at(i), y_at(a), x_at(i), y_at(b)) for i, a, b in runs(vert)]
    segs += [(x_at(a), y_at(j), x_at(b), y_at(j)) for j, a, b in runs(horz)]
    return segs


@lru_cache(maxsize=16)
def die_grid_segments(frame: WaferFrame
                      ) -> list[tuple[float, float, float, float]]:
    """die 가 **있는 칸만** 감싸는 격자 선분 ``(x1, y1, x2, y2)`` — 평면 µm.

    ``frame.die_cells``(장비 die 맵)가 있으면 그 칸을 그대로 그린다.  없으면 계산으로
    폴백한다 — die 가 '있다' 의 기준(유도)은 네 꼭짓점이 모두 원 안에 드는 것 — col/row 원점을 정하는
    '온전히 들어오는 첫 die' 규칙(:mod:`.wafer_geometry`)과 같다.  가장자리에서 잘리는
    die 에는 선을 긋지 않아 윤곽이 계단 모양이 된다.  선분 수는 :func:`grid_lines` 와
    같은 수준(경계선마다 1개)이다."""
    if frame.die_cells and frame.pitch_x and frame.pitch_y:
        return _cell_segments(frame)
    xs, ys = grid_lines(frame)
    r2 = frame.radius ** 2

    def spans(a: list[float], b: list[float]) -> list[Optional[tuple[float, float]]]:
        """``a`` 의 이웃한 두 경계 사이 띠마다, 온전한 die 가 차지하는 ``b`` 축 구간."""
        out: list[Optional[tuple[float, float]]] = []
        for lo, hi in zip(a, a[1:]):
            half = math.sqrt(max(0.0, r2 - max(lo * lo, hi * hi)))
            inside = [v for v in b if -half <= v <= half]
            out.append((inside[0], inside[-1]) if len(inside) >= 2 else None)
        return out

    def edges(a: list[float], b: list[float]):
        """경계선 ``a[i]`` 위의 선분 = 양옆 띠 구간 중 넓은 쪽(구간은 서로 포함 관계)."""
        band = [None, *spans(a, b), None]
        for i, v in enumerate(a):
            near = [s for s in (band[i], band[i + 1]) if s is not None]
            if near:
                yield v, min(s[0] for s in near), max(s[1] for s in near)

    segs = [(x, lo, x, hi) for x, lo, hi in edges(xs, ys)]
    segs += [(lo, y, hi, y) for y, lo, hi in edges(ys, xs)]
    return segs


# ---------------------------------------------------------------------------
# 결함이 든 die 칸 — 점 대신 칸을 통째로 칠할 때 쓴다
# ---------------------------------------------------------------------------
def cell_of(frame: WaferFrame, x: float, y: float) -> Optional[tuple[int, int]]:
    """평면 (x, y) 가 든 die 칸 ``(kx, ky)``.  pitch 를 모르면 None.

    칸 인덱스 규약은 :attr:`WaferFrame.die_cells` 와 같다 — 칸의 왼쪽·아래 경계가
    ``grid_x0 + kx·pitch_x`` / ``grid_y0 + ky·pitch_y`` 다.  그래서 :func:`_cell_segments`
    가 그리는 격자와 :func:`defect_cells` 가 칠하는 면이 같은 칸을 가리킨다."""
    if not frame.pitch_x or not frame.pitch_y:
        return None
    return (math.floor((x - frame.grid_x0) / frame.pitch_x),
            math.floor((y - frame.grid_y0) / frame.pitch_y))


def cell_bounds(frame: WaferFrame, cell: tuple[int, int]
                ) -> tuple[float, float, float, float]:
    """die 칸의 평면 경계 ``(x0, y0, x1, y1)`` — 왼쪽·아래·오른쪽·위."""
    kx, ky = cell
    x0 = frame.grid_x0 + kx * frame.pitch_x
    y0 = frame.grid_y0 + ky * frame.pitch_y
    return (x0, y0, x0 + frame.pitch_x, y0 + frame.pitch_y)


# 한 칸에 여러 결함이 들면 이 순서로 색이 정해진다.  기본은 **미매치가 이긴다** —
# 매치된 결함에 가려 '이 die 에 미매치가 있다' 가 사라지면 안 된다(정확도 우선).
# ``unmatched_first=False`` 면 매치됨이 이긴다(결과 화면 옵션 — 사용자 요청).
_CELL_RANK = {False: 2, True: 1, None: 0}
_CELL_RANK_MATCHED_FIRST = {True: 2, False: 1, None: 0}


def defect_cells(data: MapData, unmatched_first: bool = True
                 ) -> dict[tuple[int, int], Optional[bool]]:
    """결함이 든 die 칸 → 그 칸의 매칭 상태(``MapPoint.matched`` 와 같은 값).

    칸은 **그 칸 결함 점과 같은 색**으로 칠한다 — 셋업 단계는 매칭 전이라 전부 None
    (결함 색), 결과 단계는 매치됨/미매치.  한 칸에 섞이면 ``unmatched_first`` 면
    미매치가, 아니면 매치됨이 이긴다.

    pitch 를 모르는 폴더(절대좌표)면 빈 dict — 칸을 못 정하므로 화면은 점으로 돌아간다.
    순수 — 헤드리스 테스트한다."""
    if data.frame is None:
        return {}
    rank = _CELL_RANK if unmatched_first else _CELL_RANK_MATCHED_FIRST
    out: dict[tuple[int, int], Optional[bool]] = {}
    for p in data.points:
        cell = cell_of(data.frame, p.x, p.y)
        if cell is None:
            continue
        if cell not in out or rank[p.matched] > rank[out[cell]]:
            out[cell] = p.matched
    return out


# ---------------------------------------------------------------------------
# 결과(FinalResult) → 슬롯별 / LOT 합산 맵
# ---------------------------------------------------------------------------
ALL_SLOTS_KEY = ""      # '전체(LOT 합산)' 를 뜻하는 슬롯 키


def slot_maps(result, slot: str = ALL_SLOTS_KEY,
              progress=None) -> tuple[MapData, MapData]:
    """결과의 한 슬롯(또는 ``ALL_SLOTS_KEY`` = 전체)에 대한 (기준 맵, 검증 맵).

    ``result`` 는 :class:`~..models.result.FinalResult` — ``slot_images`` 와 ``matches``
    만 쓴다.  결과 화면과 엑셀 시트가 **같은 함수**로 만든다.
    ``progress(done, total)`` 은 기준+검증 합산 진행(로딩바용)."""
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
    # Defect 추출은 매칭을 하지 않는다 — '미매치' 로 칠하면 거짓이므로 매칭 정보 없음
    # (셋업 단계와 같은 결함 색)으로 둔다.
    if getattr(result, "mode", "") == EXTRACT_MODE:
        matched = None
    n_ref, total = len(ref_paths), len(ref_paths) + len(val_paths)
    ref_prog = val_prog = None
    if progress is not None:
        ref_prog = lambda d, _t: progress(d, total)             # noqa: E731
        val_prog = lambda d, _t: progress(n_ref + d, total)     # noqa: E731
    return (build_map(resolve_batch(ref_paths, ref_prog), matched),
            build_map(resolve_batch(val_paths, val_prog), matched))
