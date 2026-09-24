"""Wafer map ↔ txt — 셋업 화면의 'Map 저장' / 'Map 합치기'.

맵을 **다시 읽을 수 있는 txt** 로 쓴다.  점은 이미 평면 µm 좌표(:mod:`.wafer_map`)라
원본 폴더·INI 없이도 그대로 다시 그릴 수 있다 — 다른 PC 에서 합쳐도 된다.
사람이 메모장으로 봐도 읽히게 탭 구분 한 줄에 한 항목이다::

    # AOI Wafer Map v1
    frame   <diameter> <pitch_x> <pitch_y> <grid_x0> <grid_y0> <center_source> <kind> <pitch_assumed>
    cells   kx,ky;kx,ky;...          (장비 die 맵이 있을 때만)
    point   <x> <y> <col> <row> <path>
    unplaced <path>

합치기(:func:`merge`)는 점을 전부 모으고 원·격자는 **첫 파일**의 프레임을 쓴다 —
여러 폴더를 한 맵에 합산하는 :func:`.wafer_map.build_map` 과 같은 규칙이다.

순수 로직 — Qt 없이 헤드리스 테스트한다(``dev/tests/test_wafer_map.py``).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from .wafer_map import MapData, MapPoint, WaferFrame

__all__ = ["HEADER", "dumps", "loads", "save", "load", "merge"]

HEADER = "# AOI Wafer Map v1"


def _num(v: Optional[float]) -> str:
    return "" if v is None else repr(float(v))


def _opt_float(s: str) -> Optional[float]:
    return float(s) if s else None


def _opt_int(s: str) -> Optional[int]:
    return int(s) if s else None


def dumps(data: MapData) -> str:
    lines = [HEADER]
    f = data.frame
    if f is not None:
        lines.append("\t".join(["frame", _num(f.diameter), _num(f.pitch_x), _num(f.pitch_y),
                                _num(f.grid_x0), _num(f.grid_y0), f.center_source, f.kind,
                                "1" if f.pitch_assumed else "0"]))
        if f.die_cells:
            lines.append("cells\t" + ";".join(f"{i},{j}" for i, j in sorted(f.die_cells)))
    for p in data.points:
        lines.append("\t".join(["point", _num(p.x), _num(p.y),
                                "" if p.col is None else str(p.col),
                                "" if p.row is None else str(p.row), str(p.path)]))
    for u in data.unplaced:
        lines.append(f"unplaced\t{u}")
    return "\n".join(lines) + "\n"


def loads(text: str) -> MapData:
    """txt → :class:`MapData`.  형식이 아니면 ``ValueError``(첫 줄 머리표로 판정)."""
    rows = text.splitlines()
    if not rows or rows[0].strip() != HEADER:
        raise ValueError("Wafer map txt 가 아닙니다")
    frame: Optional[WaferFrame] = None
    cells = None
    points: list[MapPoint] = []
    unplaced: list[Path] = []
    for line in rows[1:]:
        if not line.strip() or line.startswith("#"):
            continue
        tag, _, rest = line.partition("\t")
        t = rest.split("\t")
        if tag == "frame":
            frame = WaferFrame(diameter=float(t[0]), pitch_x=_opt_float(t[1]),
                               pitch_y=_opt_float(t[2]), grid_x0=float(t[3]),
                               grid_y0=float(t[4]), center_source=t[5], kind=t[6],
                               pitch_assumed=t[7] == "1")
        elif tag == "cells":
            cells = frozenset(tuple(int(v) for v in c.split(","))
                              for c in rest.split(";") if c)
        elif tag == "point":
            # 경로에 탭이 있을 리는 없지만, 있더라도 마지막 칸을 통째로 경로로 본다.
            points.append(MapPoint(path=Path("\t".join(t[4:])), x=float(t[0]),
                                   y=float(t[1]), col=_opt_int(t[2]),
                                   row=_opt_int(t[3]), matched=None))
        elif tag == "unplaced":
            unplaced.append(Path(rest))
    if frame is not None and cells:
        frame = replace(frame, die_cells=cells)
    return MapData(frame=frame, points=tuple(points), unplaced=tuple(unplaced))


def save(data: MapData, path: Path) -> None:
    Path(path).write_text(dumps(data), encoding="utf-8")


def load(path: Path) -> MapData:
    return loads(Path(path).read_text(encoding="utf-8-sig"))


def merge(maps: list[MapData]) -> MapData:
    """여러 맵을 하나로 — 점·못 놓은 사진은 전부 모으고 프레임은 첫 번째 것."""
    frame = next((m.frame for m in maps if m.frame is not None), None)
    return MapData(frame=frame,
                   points=tuple(p for m in maps for p in m.points),
                   unplaced=tuple(u for m in maps for u in m.unplaced))
