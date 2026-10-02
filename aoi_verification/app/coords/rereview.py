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
Camtek 의 ``row = row_total − y_index``(아래가 0) 를 그대로 따른 것이다.

정렬 채택 조건(:func:`align`) — 장비 die 맵(``s_DieLocation.dat``)의 die 가 **하나도
빠짐없이** 옮긴 맵의 die 칸 위에 놓여야 하고, 그런 평행이동이 **딱 하나**여야 한다.
맵에만 있는 칸은 :data:`MAX_UNSCANNED` 개까지 허용한다(장비가 검사하지 않은 die).
1칸이라도 장비 die 가 맵 밖에 떨어지면 엉뚱한 die 의 사진이 빠질 수 있어 쓰지 않는다
(사용자 결정: 자동 대조, 불일치면 경고 후 그 웨이퍼는 제외하지 않는다).

왜 '완전 일치' 가 아닌가(`관측`, 2026-10): 실물 PH3Q42 4장이 'Map 2962 ≠ 장비 2960/2961'
로 전부 거부됐다.  저장소의 실물 die 맵 PGEE48-13C5(같은 70×54 격자)를 PH8Q66-02B6 맵과
대조하니 **2,960칸이 평행이동 (2,2) 하나로 전부 겹치고**, 맵에만 2칸이 남았다 — 노치 옆
(6시, 중심에서 128 mm)과 3시 축(106 mm)의 die.  좌표는 die 중심(소수부 0.498)이라
같은 칸 충돌도 없다.  즉 장비는 몇몇 die 를 **검사 목록에서 뺀다**(이유는 미확인 — 노치·
기준 die 로 추정, `가정`).  die 수천 칸의 원형 영역에서 평행이동이 1칸만 달라도 수십 칸이
맵 밖으로 나가므로 '전부 포함 + 유일' 은 완전 일치만큼 안전하다.

경쟁 가설(R1) — 맵이 좌우·상하·180° 뒤집힌 경우: 위 실물 대조에서 **뒤집힌 세 가설은
어느 평행이동으로도 포함되지 않았다**(그대로일 때만 (2,2) 하나) → 이 격자(70×54)에서는
방향이 구분되어 확정이다.  대칭인 다른 격자에서는 다시 '미구분' 일 수 있어, 정렬 실패
때 뒤집힌 가설만 맞으면 :attr:`WaferPlan.flip_hint` 에 남긴다(쓰지는 않는다).

실물 골든(`관측`, PH3Q42-05F4·03G6 — ``dev/tests/test_rereview_golden.py``): 1차 리뷰
``#11 재 저장`` 사진 파일명의 (col, row)·x·y 45장이 이 모듈의 정렬로 구한 Map 칸·INI 변환과
**전부** 일치하고(같은 die, x·y ≤ 10 µm), 그 칸은 전부 Map Reject 다.  세 소스(장비 파일명·
Camtek INI+die 목록·1차 리뷰 Map)가 서로 독립이라 정렬 규칙의 실물 검증으로 본다.

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
           "load_map", "warning_lines", "lot_from_map_path", "align", "plan_wafer", "wafer_stats", "new_reject_cells", "confirmed_new_rejects",
           "W_NO_MAP", "W_MAP_INVALID", "W_NO_GEOMETRY", "W_NO_DIE_MAP",
           "W_ALIGN_FAIL", "W_UNPLACED", "W_OFF_MAP", "W_PLAN_FAILED",
           "W_RJ_NO_FOLDER", "W_RJ_BAD_NAME", "W_RJ_NO_MATCH", "find_reject_dir", "read_reject_dies"]

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
W_RJ_NO_FOLDER = "rj_no_folder"     # 1차 Reject 사진 폴더에 이 웨이퍼 폴더가 없다
W_RJ_BAD_NAME = "rj_bad_name"       # 파일명에서 die 를 못 읽은 Reject 사진 수
W_RJ_NO_MATCH = "rj_no_match"       # Reject 사진의 die 가 Scanresult 어느 사진과도 안 맞는다
W_PLAN_FAILED = "plan_failed"       # 예상 밖 예외 — 그 웨이퍼 전부 재리뷰
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
    pitch: Optional[tuple] = None                     # (x, y) µm — 맵 그림의 칸 비율용
    unscanned: list = field(default_factory=list)     # 맵에만 있는 칸(장비 미검사 die)
    no_map: bool = False                              # 사용자가 Map 경로를 안 줬다 — 전부 재리뷰(경고 아님)

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
# 맵에만 있고 장비 die 맵에는 없는 칸(장비가 검사하지 않은 die)의 허용 개수.
# 관측 최대 2(PH3Q42 4장: 1~2, PGEE48 대조: 2).  이보다 많으면 '부분 맵' 일 수 있다.
MAX_UNSCANNED = 5


def align(map_cells: Iterable, die_cells: Iterable) -> Optional[tuple[int, int]]:
    """맵 칸 ``(열, 줄)`` → stage ``(x_index, y_index)`` 평행이동 ``(dx, dy)``.

    장비 die 가 **전부** 옮긴 맵 칸 위에 놓이고(맵에만 있는 칸은
    :data:`MAX_UNSCANNED` 개까지), 그런 평행이동이 **유일할** 때만 돌려준다."""
    a, b = set(map_cells), set(die_cells)
    if not a or not b or len(b) > len(a) or len(a) - len(b) > MAX_UNSCANNED:
        return None
    # b 의 모든 칸이 a 를 (dx, dy) 옮긴 곳에 들어가려면 dx 는 [max(b)-max(a), min(b)-min(a)]
    # 안이어야 한다 — 그 범위를 전부 본다(창을 잘라 보면 창 밖의 두 번째 해를 놓친다).
    dx_hi = min(i for i, _ in b) - min(i for i, _ in a)
    dy_hi = min(j for _, j in b) - min(j for _, j in a)
    dx_lo = max(i for i, _ in b) - max(i for i, _ in a)
    dy_lo = max(j for _, j in b) - max(j for _, j in a)
    hits = []
    for dx in range(dx_lo, dx_hi + 1):
        for dy in range(dy_lo, dy_hi + 1):
            if all((i - dx, j - dy) in a for i, j in b):
                hits.append((dx, dy))
                if len(hits) > 1:
                    return None
    return hits[0] if len(hits) == 1 else None


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
               map_dir: Optional[Path], wafer: Optional[str] = None,
               reject_dir: Optional[Path] = None) -> WaferPlan:
    """웨이퍼 하나의 재리뷰 계획.  ``wafer`` 를 안 주면 폴더명이 WaferID 다.

    ``reject_dir`` — 1차 Reject 사진 폴더(웨이퍼마다 하위 폴더, 파일명에 die 좌표).  그 사진이
    있는 die 의 사진을 Map 과 **별개로** 뺀다(둘 다 주면 합집합).

    실패는 전부 '그 웨이퍼는 전부 재리뷰 + 경고' 로 떨어진다(전 구간 fail-safe)."""
    paths = [Path(p) for p in images]
    plan = WaferPlan(slot=slot, folder=folder)
    try:
        name = wafer or (folder.name if folder else slot)
        _plan_into(plan, paths, map_dir, name)
        if reject_dir is not None:
            _apply_reject_folder(plan, reject_dir, name)
    except Exception:
        _LOG.exception("재리뷰 계획 실패 — %s 전부 재리뷰", slot)
        plan.review, plan.excluded = list(paths), []
        plan.offset = None
        plan.cell_of.clear()
        plan.unscanned = []
        plan.warnings.append((W_PLAN_FAILED, None))
    return plan


def _plan_into(plan: WaferPlan, paths: list, map_dir: Optional[Path], wafer: str) -> None:
    plan.review = list(paths)          # 기본: 전부 재리뷰
    folder = plan.folder
    _forget_caches()          # 같은 경로의 INI 가 갱신됐을 수 있다 — 옛 좌표를 쓰지 않는다
    # 장비 화면 die (col, row) — 표시·die 개수용.  맵이 없어도 채운다.
    geom = _geometry(folder)
    if geom is not None:
        plan.pitch = (geom.pitch_x, geom.pitch_y)
    coords = camtek_ini.load_folder(folder) if (folder and geom) else {}
    # 같은 stem 에 항목이 둘 이상(확장자만 다른 사진·INI 섹션)이면 어느 쪽 좌표인지 모른다.
    ambiguous = _ambiguous_stems(folder, paths) if (folder and geom) else set()
    coords = {k: c for k, c in coords.items()
              if k not in ambiguous and c.source == "camtek_ini"}
    for p in paths:
        c = coords.get(p.stem.lower())
        if c is not None:
            plan.die_of[p] = (c.col, c.row)

    if map_dir is None:               # Map 경로 미지정 = 의도된 '전체 리뷰' (경고가 아니다)
        plan.no_map = True
        return
    rm, warn = load_map(map_dir, wafer)
    plan.reject_map = rm
    if rm is None:
        plan.warnings.append((warn, None))
        return
    if geom is None:
        plan.warnings.append((W_NO_GEOMETRY, None))
        return
    die_cells = wafer_geometry.die_map_cells(folder, geom.pitch_x, geom.pitch_y,
                                             local_only=True)
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
    plan.unscanned = sorted({(i, j) for i, j in rm.cells
                             if (i + dx, j + dy) not in die_cells})
    if plan.unscanned:
        _LOG.info("재리뷰: %s 장비 die 맵에 없는 맵 칸 %d개(장비 미검사 die) %s — 정렬은 유일",
                  wafer, len(plan.unscanned), plan.unscanned)
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


_PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"})


def find_reject_dir(root: Path, wafer: str) -> Optional[Path]:
    """1차 Reject 사진 폴더 안의 이 웨이퍼 하위 폴더(대소문자 무시).  없으면 ``None``."""
    try:
        for p in Path(root).iterdir():
            if p.is_dir() and p.name.upper() == wafer.upper():
                return p
    except OSError:
        pass
    return None


def read_reject_dies(folder: Path) -> tuple[frozenset, int]:
    """폴더(하위 포함)의 사진 파일명에서 읽은 die ``(col, row)`` 집합과, 못 읽은 사진 수.

    파일명 해석은 LIVE 파일명 규칙(:func:`camtek_live.parse_live_name`)을 그대로 쓴다 —
    ``col``/``row`` 는 장비 화면 표기라 앱의 INI 변환과 같다(실물 골든 45장 전부 일치)."""
    import os
    from . import camtek_live
    dies, bad = set(), 0
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if Path(name).suffix.lower() not in _PHOTO_EXT:
                continue
            ln = camtek_live.parse_live_name(Path(name).stem)
            if ln is None:
                bad += 1
            else:
                dies.add((ln.col, ln.row))
    return frozenset(dies), bad


def _apply_reject_folder(plan: WaferPlan, reject_dir: Path, wafer: str) -> None:
    """1차 Reject 사진이 있는 die 의 사진을 재리뷰에서 뺀다 — 못 믿으면 빼지 않고 경고."""
    d = find_reject_dir(reject_dir, wafer)
    if d is None:
        plan.warnings.append((W_RJ_NO_FOLDER, None))
        return
    dies, bad = read_reject_dies(d)
    if bad:
        plan.warnings.append((W_RJ_BAD_NAME, bad))
    if not dies:
        return
    if not plan.die_of:                       # 이 웨이퍼 사진의 die 를 모른다 — 비교할 수 없다
        if not any(c == W_NO_GEOMETRY for c, _ in plan.warnings):
            plan.warnings.append((W_NO_GEOMETRY, None))
        return
    hit = [p for p in plan.review if plan.die_of.get(p) in dies]
    if not hit:
        plan.warnings.append((W_RJ_NO_MATCH, len(dies)))
    gone = set(hit)
    plan.review = [p for p in plan.review if p not in gone]
    plan.excluded = plan.excluded + hit
    if not any(c == W_UNPLACED for c, _ in plan.warnings):
        unplaced = sum(1 for p in plan.review if p not in plan.die_of)
        if unplaced:
            plan.warnings.append((W_UNPLACED, unplaced))


def _forget_caches() -> None:
    for fn in (camtek_ini.load_folder, camtek_ini.load_raw_folder,
               camtek_ini.load_abs_folder, camtek_ini.load_recipe_folder,
               wafer_geometry.camtek_geometry):
        fn.cache_clear()


def _ambiguous_stems(folder: Path, paths: list) -> set:
    """소문자 stem 이 둘 이상의 사진 또는 INI 섹션에 걸려 있는 것들."""
    from .ini_text import read_ini_text
    seen: dict = {}
    for p in paths:
        seen.setdefault(p.stem.lower(), set()).add(p.name.lower())
    ini = camtek_ini._find_ini(folder)
    if ini is not None:
        parts = camtek_ini._SECTION_PAT.split(read_ini_text(ini) or "")
        for name in parts[1::2]:
            n = name.strip().lower()
            seen.setdefault(Path(n).stem, set()).add(n)
    return {stem for stem, names in seen.items() if len(names) > 1}


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
    confirmed = confirmed_new_rejects(plan, rej)
    dies = {plan.die_of[p] for p in confirmed if p in plan.die_of}
    unknown = len(rej) - len(confirmed)
    map_dies = len(plan.reject_map.rejects) if plan.reject_map else 0
    n_rev = len(plan.review)
    return WaferStats(
        slot=plan.slot, total=n_rev + len(plan.excluded),
        excluded=len(plan.excluded), reviewed=n_rev,
        good=n_rev - len(rej), reject=len(rej),
        new_reject_dies=len(dies), unknown_die_rejects=unknown,
        map_reject_dies=map_dies, total_reject_dies=map_dies + (len(dies) if plan.no_map
                                                    else len(new_reject_cells(plan, rej))))


def confirmed_new_rejects(plan: WaferPlan, reject_paths: Iterable) -> list:
    """Reject 사진 중 **확정 신규 Reject die** 에 놓인 것만.

    맵과 정렬이 됐고, 사진의 칸이 맵의 실제 die 칸이며, 그 칸이 1차 리뷰에서 이미 Reject 가
    아닐 때만 '새로 추가된 Reject' 라 부른다.  정렬 실패·맵 밖·좌표 없음은 die 번호를 알아도
    맵 대응이 미확정이라 여기서 뺀다(사진 행·엑셀 판정은 그대로 남는다)."""
    if plan.no_map:                   # 맵이 없으면 Reject 한 사진의 die 가 전부 '추가'다
        return [Path(p) for p in reject_paths if Path(p) in plan.die_of]
    rm = plan.reject_map
    if not plan.aligned or rm is None:
        return []
    out = []
    for p in reject_paths:
        cell = plan.cell_of.get(Path(p))
        if cell is not None and cell in rm.cells and cell not in rm.rejects:
            out.append(Path(p))
    return out


def new_reject_cells(plan: WaferPlan, reject_paths: Iterable) -> frozenset:
    """확정 신규 Reject 사진이 떨어진 **맵 칸** — Wafer Map 그림·개수 공용."""
    return frozenset(plan.cell_of[p] for p in confirmed_new_rejects(plan, reject_paths)
                     if p in plan.cell_of)


def warning_lines(plans: dict) -> list[str]:
    """웨이퍼별 재리뷰 경고 → 사람이 읽는 문장들(맵 대조 요약·결과 화면·엑셀 공용)."""
    fmts = {W_NO_MAP: i18n.KO.REREVIEW_WARN_NO_MAP_FMT,
            W_MAP_INVALID: i18n.KO.REREVIEW_WARN_MAP_INVALID_FMT,
            W_NO_GEOMETRY: i18n.KO.REREVIEW_WARN_NO_GEOMETRY_FMT,
            W_NO_DIE_MAP: i18n.KO.REREVIEW_WARN_NO_DIE_MAP_FMT,
            W_ALIGN_FAIL: i18n.KO.REREVIEW_WARN_ALIGN_FAIL_FMT,
            W_UNPLACED: i18n.KO.REREVIEW_WARN_UNPLACED_FMT,
            W_OFF_MAP: i18n.KO.REREVIEW_WARN_OFF_MAP_FMT,
            W_PLAN_FAILED: i18n.KO.REREVIEW_WARN_PLAN_FAILED_FMT,
            W_RJ_NO_FOLDER: i18n.KO.REREVIEW_WARN_RJ_NO_FOLDER_FMT,
            W_RJ_BAD_NAME: i18n.KO.REREVIEW_WARN_RJ_BAD_NAME_FMT,
            W_RJ_NO_MATCH: i18n.KO.REREVIEW_WARN_RJ_NO_MATCH_FMT}
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


# 1차 리뷰 Map 경로의 LOT 폴더 이름 — ``288. PH3Q42.00 (FSX)`` (번호. LOT명 (S/M)).
_LOT_DIR = re.compile(r"^\s*\d+\s*\.\s*(\S+)\s*\(\s*([^()]+?)\s*\)\s*$")


def lot_from_map_path(path: str) -> str:
    """Map 폴더 경로에서 ``LOT명 (S/M)`` — 못 찾으면 ``""``.

    예: ``\\\\k5cifsn2\\…\\288. PH3Q42.00 (FSX)\\2. FVI\\1. OR`` → ``PH3Q42.00 (FSX)``.
    Windows UNC 경로를 어느 OS 에서든 같게 나누려고 ``\\``·``/`` 둘 다 구분자로 본다.
    가장 깊은 일치 폴더를 쓴다."""
    for part in reversed(re.split(r"[\\/]+", path or "")):
        m = _LOT_DIR.match(part)
        if m:
            return f"{m.group(1)} ({m.group(2)})"
    return ""
