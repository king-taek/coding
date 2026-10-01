"""AVAGO 재리뷰 — 1차 리뷰 맵에서 **Reject die 의 사진을 재리뷰 대상에서 뺀다**.

입력(사용자 확인):

* **Map 폴더** — 1차 리뷰 결과 맵이 웨이퍼마다 ``<WaferID>.txt`` 로 있다
  (예: ``…\\2. FVI\\1. OR\\PH8Q66-02B6.txt``).  형식은 :mod:`.wafer_txt` 와 같다
  (``ROWCT``/``COLCT``/``BCEQU``/``RowData``, 첫 줄 = 맨 위 행, ``___`` = die 없음).
* **Scanresult LOT 폴더** — 웨이퍼 폴더(``…\\NHW\\PH8Q66-02B6``)마다 Camtek INI 와
  ``s_DieLocation.dat`` 이 있다.  웨이퍼 폴더명 = 맵 파일명(대소문자 무시)으로 짝짓는다.

Reject 판정(사용자 결정): ``BCEQU``(양품 bin, 없으면 ``000``) 이 아닌 bin 은 **전부** Reject.

맵 칸 ↔ Camtek die 정렬 — **평행이동만**, 그리고 **die 영역이 정확히 같을 때만** 쓴다::

    x_index = 맵 열(왼쪽부터 0)   + dx        x_index = floor(X / pitch_x)
    y_index = 맵 줄(위에서부터 0) + dy        y_index = floor(Y / pitch_y)

방향(왼→오, 위→아래)은 :mod:`.wafer_txt` 의 관측 규약(LIVE 9 LOT, 185칸 중 184칸)과
Camtek 의 ``row = row_total − y_index``(아래가 0) 를 그대로 따른 것이다.  ``dx``/``dy`` 는
맵의 die 칸 집합과 장비 die 맵(``s_DieLocation.dat``) 집합의 최솟값을 맞춰 구하고,
**두 집합이 한 칸도 빠짐없이 같아야** 채택한다(사용자 결정: 자동 대조, 불일치면 경고 후
그 웨이퍼는 제외하지 않는다).  1칸이라도 어긋나면 엉뚱한 die 의 사진이 빠지기 때문이다.

⚠ 경쟁 가설(R1): 맵이 좌우·상하로 뒤집힌 경우.  웨이퍼 die 영역은 거의 대칭이라
**모양 대조만으로는 뒤집힘을 구분하지 못할 수 있다** — 대칭인 웨이퍼에서는 '미구분' 이다.
그래서 방향은 위 관측 규약에 고정하고, 뒤집힌 가설만 맞는 웨이퍼는 쓰지 않는다
(진단용으로 :attr:`WaferPlan.flip_hint` 에 남긴다).  AVAGO 실물로 Reject die 의 장비 화면
(col,row) 를 한 번 대조하면 이 미구분이 풀린다.

맵이 없거나·못 믿거나·정렬이 안 되거나·좌표를 못 읽은 사진은 **재리뷰에 넣는다**
(사용자 결정 — 놓치는 쪽보다 한 번 더 보는 쪽이 안전하다).  순수 로직이라 Qt 없이
헤드리스 테스트한다(``dev/tests/test_rereview.py``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .. import i18n
from . import camtek_ini, wafer_geometry, wafer_txt
from .ini_text import read_ini_text

__all__ = ["RejectMap", "WaferPlan", "WaferStats", "parse_map", "find_map",
           "load_map", "warning_lines", "align", "plan_wafer", "wafer_stats", "new_reject_cells",
           "W_NO_MAP", "W_MAP_INVALID", "W_NO_GEOMETRY", "W_NO_DIE_MAP",
           "W_ALIGN_FAIL", "W_UNPLACED", "W_OFF_MAP"]

_LOG = logging.getLogger("aoi.coords")

_EMPTY = re.compile(r"^[_.\-]+$")            # die 없음 표기(:mod:`.wafer_txt` 와 같다)
_KEY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
_DEFAULT_GOOD_BIN = "000"
_MAX_BYTES = 2 * 1024 * 1024                  # 맵 파일 상한(:mod:`.wafer_txt` 와 같다)

# 경고 코드 — 문구는 :func:`warning_lines` 가 i18n 에서 만든다(판정 로직은 문구를 모른다).
W_NO_MAP = "no_map"               # 맵 파일이 없다 → 전부 재리뷰
W_MAP_INVALID = "map_invalid"     # 맵 형식·WAFER·FNLOC 를 못 믿는다 → 전부 재리뷰
W_NO_GEOMETRY = "no_geometry"     # die pitch 를 못 정했다 → 전부 재리뷰
W_NO_DIE_MAP = "no_die_map"       # s_DieLocation.dat 이 없다/못 읽는다 → 전부 재리뷰
W_ALIGN_FAIL = "align_fail"       # 맵과 장비 die 영역이 다르다 → 전부 재리뷰
W_UNPLACED = "unplaced"           # 좌표를 못 읽은 사진(재리뷰에 넣음)
W_OFF_MAP = "off_map"             # 맵의 die 없는 칸에 떨어진 사진(재리뷰에 넣음)


@dataclass(frozen=True)
class RejectMap:
    """1차 리뷰 맵 한 장.  칸은 ``(열, 위에서부터 줄)`` — :class:`.wafer_txt.WaferTxt` 와 같다."""
    path: Path
    wafer: str
    rows: int
    cols: int
    cells: frozenset              # die 가 있는 칸 전부
    rejects: frozenset            # 그중 Reject(양품 bin 이 아닌) 칸
    good_bin: str


@dataclass
class WaferPlan:
    """웨이퍼 하나의 재리뷰 계획 — 어떤 사진을 보고 어떤 사진을 뺐는가."""
    slot: str
    folder: Optional[Path]
    review: list = field(default_factory=list)       # 재리뷰 대상 사진 경로
    excluded: list = field(default_factory=list)     # 맵 Reject die 라서 뺀 사진 경로
    reject_map: Optional[RejectMap] = None
    offset: Optional[tuple] = None                    # (dx, dy) — 정렬 성공 시
    die_of: dict = field(default_factory=dict)        # 경로 → 장비 화면 (col, row)
    cell_of: dict = field(default_factory=dict)       # 경로 → 맵 칸 (열, 위에서부터 줄)
    warnings: list = field(default_factory=list)      # [(코드, 숫자 또는 None)]
    flip_hint: str = ""                               # 정렬 실패 시 뒤집힌 가설 진단

    @property
    def aligned(self) -> bool:
        return self.offset is not None


@dataclass(frozen=True)
class WaferStats:
    """결과 화면·엑셀 요약이 **같은 숫자**를 보도록 한 곳에서 센다."""
    slot: str
    total: int                    # 웨이퍼 사진 전체
    excluded: int                 # 맵 Reject die 라서 뺀 사진
    reviewed: int                 # 재리뷰한 사진
    good: int
    reject: int                   # 재리뷰에서 Reject 한 사진
    new_reject_dies: int          # 그 사진들의 서로 다른 die 수(좌표를 아는 것만)
    unknown_die_rejects: int      # Reject 했지만 die 를 모르는 사진 수
    map_reject_dies: int          # 맵에 이미 Reject 인 die 수
    total_reject_dies: int        # 맵 Reject + 신규 Reject die


# ---------------------------------------------------------------------------
# 맵 읽기
# ---------------------------------------------------------------------------
def parse_map(text: str, path: Path, wafer: str = "") -> Optional[RejectMap]:
    """맵 텍스트 → :class:`RejectMap`.  못 믿으면 ``None``.

    모양·``WAFER``·``FNLOC`` 검증은 :func:`.wafer_txt.parse` 를 **그대로** 쓴다
    (같은 형식을 두 벌로 판정하지 않는다).  여기서는 bin 값만 더 읽는다."""
    base = wafer_txt.parse(text, path, wafer)
    if base is None:
        return None
    head: dict[str, str] = {}
    grid: list[list[str]] = []
    for ln in text.splitlines():
        if ln.strip().lower().startswith("rowdata"):
            grid.append(ln.split(":", 1)[1].split() if ":" in ln else [])
            continue
        m = _KEY.match(ln)
        if m and m.group(1).upper() not in head:
            head[m.group(1).upper()] = m.group(2)
    good = head.get("BCEQU", "").strip() or _DEFAULT_GOOD_BIN
    rejects = frozenset((ci, j) for j, r in enumerate(grid)
                        for ci, tok in enumerate(r)
                        if not _EMPTY.match(tok) and tok != good)
    return RejectMap(path=path, wafer=head.get("WAFER", "").strip() or wafer,
                     rows=base.rows, cols=base.cols, cells=base.cells,
                     rejects=rejects, good_bin=good)


def find_map(map_dir: Path, wafer: str) -> Optional[Path]:
    """Map 폴더에서 ``<wafer>.txt`` — 대소문자 무시.  없으면 ``None``."""
    try:
        exact = map_dir / f"{wafer}.txt"
        if exact.is_file():
            return exact
        want = f"{wafer}.txt".lower()
        for p in map_dir.iterdir():
            if p.name.lower() == want and p.is_file():
                return p
    except OSError:
        pass
    return None


def load_map(map_dir: Path, wafer: str) -> tuple[Optional[RejectMap], Optional[str]]:
    """``(맵, 경고코드)`` — 맵이 없으면 :data:`W_NO_MAP`, 못 믿으면 :data:`W_MAP_INVALID`."""
    p = find_map(map_dir, wafer)
    if p is None:
        return None, W_NO_MAP
    try:
        if p.stat().st_size > _MAX_BYTES:
            return None, W_MAP_INVALID
        text = read_ini_text(p)
    except OSError:
        return None, W_MAP_INVALID
    rm = parse_map(text, p, wafer) if text else None
    return (rm, None) if rm is not None else (None, W_MAP_INVALID)


# ---------------------------------------------------------------------------
# 정렬
# ---------------------------------------------------------------------------
def align(map_cells: Iterable, die_cells: Iterable) -> Optional[tuple[int, int]]:
    """맵 칸 ``(열, 줄)`` → stage ``(x_index, y_index)`` 평행이동 ``(dx, dy)``.

    두 집합이 평행이동으로 **정확히** 겹칠 때만 돌려준다.  겹친다면 최솟값끼리
    맞아야 하므로 후보는 하나뿐이다."""
    a, b = set(map_cells), set(die_cells)
    if not a or len(a) != len(b):
        return None
    dx = min(i for i, _ in b) - min(i for i, _ in a)
    dy = min(j for _, j in b) - min(j for _, j in a)
    if {(i + dx, j + dy) for i, j in a} == b:
        return dx, dy
    return None


def _flip_hint(map_cells: frozenset, die_cells: frozenset, rows: int, cols: int) -> str:
    """정렬 실패 시 진단 — 뒤집힌 맵이면 맞는지(쓰지는 않는다, 모듈 docstring R1)."""
    flips = (("lr", lambda i, j: (cols - 1 - i, j)),
             ("ud", lambda i, j: (i, rows - 1 - j)),
             ("rot180", lambda i, j: (cols - 1 - i, rows - 1 - j)))
    hits = [name for name, f in flips
            if align({f(i, j) for i, j in map_cells}, die_cells) is not None]
    return ",".join(hits)


# ---------------------------------------------------------------------------
# 웨이퍼 하나
# ---------------------------------------------------------------------------
def plan_wafer(slot: str, folder: Optional[Path], images: Iterable,
               map_dir: Optional[Path], wafer: Optional[str] = None) -> WaferPlan:
    """웨이퍼 하나의 재리뷰 계획.  ``wafer`` 를 안 주면 폴더명이 WaferID 다.

    실패는 전부 '그 웨이퍼는 전부 재리뷰 + 경고' 로 떨어진다(전 구간 fail-safe)."""
    paths = [Path(p) for p in images]
    plan = WaferPlan(slot=slot, folder=folder)
    try:
        _plan_into(plan, paths, map_dir, wafer or (folder.name if folder else slot))
    except Exception:
        _LOG.exception("재리뷰 계획 실패 — %s 전부 재리뷰", slot)
        plan.review, plan.excluded = list(paths), []
        plan.offset = None
    return plan


def _plan_into(plan: WaferPlan, paths: list, map_dir: Optional[Path], wafer: str) -> None:
    plan.review = list(paths)          # 기본: 전부 재리뷰
    folder = plan.folder
    # 장비 화면 die (col, row) — 표시·die 개수용.  맵이 없어도 채운다.
    geom = _geometry(folder)
    coords = camtek_ini.load_folder(folder) if (folder and geom) else {}
    for p in paths:
        c = coords.get(p.stem.lower())
        if c is not None:
            plan.die_of[p] = (c.col, c.row)

    rm, warn = load_map(map_dir, wafer) if map_dir else (None, W_NO_MAP)
    plan.reject_map = rm
    if rm is None:
        plan.warnings.append((warn, None))
        return
    if geom is None:
        plan.warnings.append((W_NO_GEOMETRY, None))
        return
    die_cells = wafer_geometry.die_map_cells(folder, geom.pitch_x, geom.pitch_y)
    if not die_cells:
        plan.warnings.append((W_NO_DIE_MAP, None))
        return
    off = align(rm.cells, die_cells)
    if off is None:
        plan.flip_hint = _flip_hint(rm.cells, die_cells, rm.rows, rm.cols)
        plan.warnings.append((W_ALIGN_FAIL, (len(rm.cells), len(die_cells))))
        _LOG.warning("재리뷰: %s 맵 die %d칸 ≠ 장비 die 맵 %d칸 — 제외하지 않음%s",
                     wafer, len(rm.cells), len(die_cells),
                     f" (뒤집힌 맵과는 일치: {plan.flip_hint})" if plan.flip_hint else "")
        return
    plan.offset = off
    dx, dy = off
    review, excluded = [], []
    unplaced = off_map = 0
    for p in paths:
        c = coords.get(p.stem.lower())
        if c is None:
            unplaced += 1
            review.append(p)
            continue
        # DefectCoord 의 (col,row) 는 stage 인덱스에서 만든 값이다(camtek_ini) —
        # 같은 geom 으로 되돌린다(INI 파싱을 두 벌로 하지 않는다).
        x_index = c.col + geom.col_origin
        y_index = geom.row_total - c.row
        cell = (x_index - dx, y_index - dy)
        plan.cell_of[p] = cell
        if cell in rm.rejects:
            excluded.append(p)
        else:
            if cell not in rm.cells:
                off_map += 1
            review.append(p)
    plan.review, plan.excluded = review, excluded
    if unplaced:
        plan.warnings.append((W_UNPLACED, unplaced))
    if off_map:
        plan.warnings.append((W_OFF_MAP, off_map))


def _geometry(folder: Optional[Path]):
    if folder is None:
        return None
    try:
        return wafer_geometry.camtek_geometry(folder)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 집계
# ---------------------------------------------------------------------------
def wafer_stats(plan: WaferPlan, reject_paths: Iterable) -> WaferStats:
    """재리뷰 판정(``reject_paths`` = Reject 로 고른 사진)을 숫자로."""
    rej = [Path(p) for p in reject_paths]
    dies = {plan.die_of[p] for p in rej if p in plan.die_of}
    unknown = sum(1 for p in rej if p not in plan.die_of)
    map_dies = len(plan.reject_map.rejects) if plan.reject_map else 0
    n_rev = len(plan.review)
    return WaferStats(
        slot=plan.slot, total=n_rev + len(plan.excluded),
        excluded=len(plan.excluded), reviewed=n_rev,
        good=n_rev - len(rej), reject=len(rej),
        new_reject_dies=len(dies), unknown_die_rejects=unknown,
        map_reject_dies=map_dies, total_reject_dies=map_dies + len(dies))


def new_reject_cells(plan: WaferPlan, reject_paths: Iterable) -> frozenset:
    """신규 Reject 사진이 떨어진 **맵 칸** — Wafer Map 그림용(정렬된 웨이퍼만)."""
    return frozenset(plan.cell_of[Path(p)] for p in reject_paths
                     if Path(p) in plan.cell_of)


def warning_lines(plans: dict) -> list[str]:
    """웨이퍼별 재리뷰 경고 → 사람이 읽는 문장들(맵 대조 요약·결과 화면·엑셀 공용)."""
    fmts = {W_NO_MAP: i18n.KO.REREVIEW_WARN_NO_MAP_FMT,
            W_MAP_INVALID: i18n.KO.REREVIEW_WARN_MAP_INVALID_FMT,
            W_NO_GEOMETRY: i18n.KO.REREVIEW_WARN_NO_GEOMETRY_FMT,
            W_NO_DIE_MAP: i18n.KO.REREVIEW_WARN_NO_DIE_MAP_FMT,
            W_ALIGN_FAIL: i18n.KO.REREVIEW_WARN_ALIGN_FAIL_FMT,
            W_UNPLACED: i18n.KO.REREVIEW_WARN_UNPLACED_FMT,
            W_OFF_MAP: i18n.KO.REREVIEW_WARN_OFF_MAP_FMT}
    out: list[str] = []
    for slot in sorted(plans):
        plan = plans[slot]
        for code, val in plan.warnings:
            fmt = fmts.get(code)
            if fmt is None:
                continue
            if code == W_ALIGN_FAIL:
                a, b = val
                line = fmt.format(wafer=slot, a=a, b=b)
                if plan.flip_hint:
                    line += i18n.KO.REREVIEW_WARN_FLIP_SUFFIX
            else:
                line = fmt.format(wafer=slot, n=val)
            out.append(line)
    return out
