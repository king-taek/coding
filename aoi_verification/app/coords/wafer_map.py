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

from . import kla_info, wafer_geometry as wg
from .models import DefectCoord

__all__ = ["WaferFrame", "MapPoint", "MapData", "frame_for_folder", "to_plane",
           "build_map", "grid_lines", "die_grid_segments", "cell_of",
           "cell_bounds", "defect_cells", "slot_maps", "ALL_SLOTS_KEY"]

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
    pitch_assumed: bool = False  # pitch 가 파일이 아니라 상수(가정) — LIVE 전용 폴더

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
    cells: Optional[frozenset] = None   # die 맵의 (x_index, y_index) — stage 인덱스
    pitch_assumed: bool = False


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
    pitch_assumed = False
    # INI 항목이 **없는** 폴더(LIVE 파일명 슬롯)는 검산 재료가 없어 위가 늘 None 이다 —
    # 그러면 LIVE 사진이 전부 '좌표 없음' 이 된다.  파일 pitch(없으면 상수=가정)로
    # 기하를 만든다.  INI 항목이 있는데 None 인 폴더(절대좌표)는 건드리지 않는다.
    if geom is None and not wg.has_camtek_entries(folder):
        geom = wg.live_geometry(folder)
        pitch_assumed = geom.source in (wg._CONST_SOURCE, "fallback")
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
    # die 맵은 **기하가 그 맵을 채택했을 때만** 쓴다 — 부분 맵(유도값과 ±1 밖)은
    # camtek_geometry 가 이미 버렸고, 그때는 격자도 계산으로 그린다.
    cells = None
    if geom is not None and geom.source.endswith(wg._DIE_MAP_FILE):
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
      (pitch 가 가정이면 die 내부 좌표가 pitch 를 넘는 사진은 None)
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
        # 가정한 pitch 보다 die 내부 좌표가 크면 가정이 반증된 것 — 틀린 자리에 찍지 않는다.
        if c.pitch_assumed and not (0 <= coord.x <= g.pitch_x
                                    and 0 <= coord.y <= g.pitch_y):
            return None
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


def defect_cells(data: MapData) -> set[tuple[int, int]]:
    """결함이 하나라도 든 die 칸들.  한 칸에 여러 결함이 들면 칸 하나로 합쳐진다.

    **매칭 상태는 담지 않는다** — 이 칸들을 칠하는 것은 셋업 단계 화면뿐이고, 거기서는
    모든 점이 같은 '결함' 색이다(매칭 전이라 ``matched`` 가 전부 None).  결과 단계는
    점 표시 그대로다(사용자 결정).

    pitch 를 모르는 폴더(절대좌표)면 빈 집합 — 칸을 못 정하므로 화면은 점으로 돌아간다.
    순수 — 헤드리스 테스트한다."""
    if data.frame is None:
        return set()
    cells = {cell_of(data.frame, p.x, p.y) for p in data.points}
    cells.discard(None)
    return cells


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
    n_ref, total = len(ref_paths), len(ref_paths) + len(val_paths)
    ref_prog = val_prog = None
    if progress is not None:
        ref_prog = lambda d, _t: progress(d, total)             # noqa: E731
        val_prog = lambda d, _t: progress(n_ref + d, total)     # noqa: E731
    return (build_map(resolve_batch(ref_paths, ref_prog), matched),
            build_map(resolve_batch(val_paths, val_prog), matched))
