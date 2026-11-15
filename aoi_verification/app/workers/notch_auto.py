"""Notch 방향 자동 감지 — 검증 폴더를 네 방향으로 돌려 보고 기준과 가장 많이 맞는 방향을 고른다.

좌표 매칭이 쓰는 같은 판정(`coord_matcher._match_neighbors`: (col,row) ±1 · die-내부 거리 ≤ tol)
으로, 기준 사진이 몇 장이나 짝을 찾는지 센다.  틀린 방향은 짝이 거의 안 나오므로 맞는 방향이
뚜렷이 많다.

★ 짝을 세기 전에 방향마다 **두 폴더가 공유하는 어긋남**(``coords.calibration`` 의 투표)을 먼저
빼고 센다.  KLA ↔ Camtek 은 die 기준점이 달라 같은 결함이 수백 µm 떨어진다(관측 2026-10-28,
Kendall RDL4 Notch Right: 90° 돌린 뒤 약 635 µm) — 그대로 tol 로 세면 맞는 방향도 0 이 돼
방향을 못 찾았다.  뚜렷하지 않으면(지지 수가 적거나 2등과 비슷) 값을 내지 않는다 —
``coords.calibration.auto_offset`` 과 같은 태도다(``MIN_SUPPORT``·``DOMINANCE`` 는 **가정**).

★ die 가 정사각형이 아니면 두 폴더 파일의 die pitch(가로 · 세로)로 90° 돌림이 되는지부터 가른다
(:func:`_turn_from_pitches`) — 틀린 방향이 짝을 더 많이 낸 일이 있었다(TB500, 관측 2026-11-15).

사진 수가 많아도 느리지 않게 앞쪽 슬롯 몇 개만 본다(:data:`MAX_SLOTS`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from .. import config as _config
from ..coords import calibration as _calib
from ..coords import notch
from .coord_matcher import _match_neighbors

__all__ = ["AutoNotch", "score_directions", "NotchAutoWorker", "MAX_SLOTS"]

_LOG = logging.getLogger("aoi.match")

MAX_SLOTS = 5
MIN_SUPPORT = 8        # 짝을 찾은 기준 사진이 이보다 적으면 믿지 않는다
DOMINANCE = 2.0        # 1등이 2등의 이 배수를 넘어야 한다


@dataclass(frozen=True)
class AutoNotch:
    direction: Optional[str]      # None = 판단 못 함
    support: int                  # 1등이 짝을 찾은 기준 사진 수
    runner_up: int
    total: int                    # 본 기준 사진 수
    offset: Tuple[float, float] = (0.0, 0.0)   # 1등 방향에서 뺀 공유 어긋남(검증 − 기준, µm)


def _shared_offset(groups, fixed) -> Tuple[float, float]:
    """돌린 좌표에서 두 폴더가 공유하는 어긋남(검증 − 기준) — 없으면 0.

    매칭과 같은 ±1 die 이웃 중 ``calibration.SEARCH_RADIUS_UM`` 안의 차이를 투표한다."""
    votes: List[Tuple[float, float]] = []
    for refs, vals in groups:
        vmap: Dict[Tuple[int, int], List] = {}
        for v in vals:
            c = fixed.get(v)
            if c is not None:
                vmap.setdefault((c.col, c.row), []).append(c)
        for r in refs:
            c = fixed.get(r)
            if c is None:
                continue
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    for o in vmap.get((c.col + dc, c.row + dr), ()):
                        dx, dy = o.x - c.x, o.y - c.y
                        if dx * dx + dy * dy <= _calib.SEARCH_RADIUS_UM ** 2:
                            votes.append((dx, dy))
    return _calib._guess_from(votes) or (0.0, 0.0)


def _pitch_of(folder: Path) -> Optional[Tuple[float, float]]:
    """폴더의 die pitch ``(x, y)`` — KLA 정보파일 ``DiePitch``, 없으면 Camtek 설정 파일 값.  없으면 None.

    둘 다 그 장비 **좌표 틀**의 값이다(그 좌표를 die 번호로 바꿀 때 쓰는 값).  가벼운 머리 읽기만 한다."""
    from ..coords import kla_info, wafer_geometry
    v = kla_info.peek_die_pitch(folder)
    if v:
        return v
    w = wafer_geometry.peek_die_pitch(folder)
    return (w[0], w[1]) if w else None


def _turn_from_pitches(a: Tuple[float, float], b: Tuple[float, float]) -> Optional[bool]:
    """기준 ``a`` · 검증 ``b`` 의 die pitch 로 본 90° 돌림 — True 면 가로 · 세로가 바뀌어 90° 만 가능,
    False 면 그대로라 0° · 180° 만 가능, None 이면 가를 수 없다(정사각형 · 애매 · 단위가 다름).

    고정 % 기준은 두지 않는다 — 경계는 검증 die 의 가로 · 세로 **가운데**다(각 축이 어느 쪽에 더
    가까운가).  관측(2026-11-15): TB500 두 장비 37,247.9 × 44,905.3 = 그대로, Kendall KLA 27,474.6 ×
    47,836.0 ↔ Camtek 47,835.6 × 27,474.8 = 바뀜(실물 짝도 90°)."""
    half = abs(b[0] - b[1]) / 2
    if half <= 0:
        return None
    same = abs(a[0] - b[0]) < half and abs(a[1] - b[1]) < half
    swap = abs(a[0] - b[1]) < half and abs(a[1] - b[0]) < half
    return None if same == swap else swap


def _turn_allowed(groups, pitch_of: Callable = _pitch_of) -> Optional[bool]:
    """슬롯마다 :func:`_turn_from_pitches` — 모두 같은 답일 때만 그 답, 하나라도 못 가르면 None."""
    seen = set()
    for refs, vals in groups:
        if not refs or not vals:
            continue
        a, b = pitch_of(Path(refs[0]).parent), pitch_of(Path(vals[0]).parent)
        if not a or not b:
            continue
        t = _turn_from_pitches(a, b)
        if t is None:
            return None
        seen.add(t)
    return next(iter(seen)) if len(seen) == 1 else None


def score_directions(groups: Sequence[Tuple[Sequence, Sequence]],
                     ref_root, val_root, ref_dir: str, tol: float, *,
                     resolve: Callable = None,
                     progress: Callable[[int, int], None] = None) -> AutoNotch:
    """``groups`` = 슬롯마다 ``(기준 경로들, 검증 경로들)``.  기준 방향은 ``ref_dir`` 로 고정."""
    if resolve is None:
        from ..coords import resolve_batch as resolve
    paths = [p for refs, vals in groups for p in (*refs, *vals)]
    coords = resolve(paths)
    total = sum(1 for refs, _v in groups for p in refs if coords.get(p) is not None)
    scores: Dict[str, int] = {}
    offsets: Dict[str, Tuple[float, float]] = {}
    n = len(notch.DIRECTIONS)
    # die 가 정사각형이 아니면 가로 · 세로(두 폴더 파일 값)로 90° 돌림이 되는지부터 가른다 —
    # 짝 수는 우연히 틀린 방향에서 많을 수 있다(TB500: 90° 방향 374 > 맞는 방향 150, 관측 2026-11-15).
    turn = _turn_allowed(groups)
    quarter = ({notch.LEFT, notch.RIGHT} if ref_dir in (notch.UP, notch.DOWN)
               else {notch.UP, notch.DOWN})
    if turn is not None:
        _LOG.info("Notch 자동 감지: die 가로·세로(파일 값)로 보아 %s 방향은 후보에서 뺍니다",
                  sorted(d for d in notch.DIRECTIONS if (d in quarter) != turn))
    for i, d in enumerate(notch.DIRECTIONS):
        if progress:
            progress(i, n)
        if turn is not None and (d in quarter) != turn:
            continue                      # die 모양으로 보아 불가능한 돌림
        dirs = ((str(ref_root), ref_dir), (str(val_root), d))
        try:
            fixed = notch.apply_notch(
                coords, lambda p: notch.notch_for_path(dirs, p))
        except notch.NotchError:
            continue                      # 이 방향은 돌릴 재료가 없다 — 후보에서 뺀다
        ox, oy = _shared_offset(groups, fixed)
        hit = 0
        for refs, vals in groups:
            vmap: Dict[Tuple[int, int], List] = {}
            for v in vals:
                c = fixed.get(v)
                if c is not None:
                    vmap.setdefault((c.col, c.row), []).append((v, c.x, c.y))
            for r in refs:
                c = fixed.get(r)
                if c is not None and _match_neighbors(c.x + ox, c.y + oy, c.col, c.row,
                                                      vmap, tol):
                    hit += 1
        scores[d] = hit
        offsets[d] = (ox, oy)
    if progress:
        progress(n, n)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked:
        return AutoNotch(None, 0, 0, total)
    best_d, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    ok = best >= MIN_SUPPORT and best > second * DOMINANCE
    _LOG.info("Notch 자동 감지: %s (짝 %d / 2등 %d / 기준 %d장 · 뺀 어긋남 %s)",
              scores, best, second, total, offsets.get(best_d))
    return AutoNotch(best_d if ok else None, best, second, total, offsets.get(best_d, (0.0, 0.0)))


class NotchAutoWorker(QThread):
    """백그라운드에서 :func:`score_directions` — 결과는 ``done`` 시그널(메인 스레드로)."""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)

    def __init__(self, groups, ref_root, val_root, ref_dir: str, tol: float,
                 parent=None) -> None:
        super().__init__(parent)
        self._args = (groups, ref_root, val_root, ref_dir, tol)

    def run(self) -> None:  # type: ignore[override]
        try:
            self.done.emit(score_directions(*self._args, progress=self.progress.emit))
        except Exception:
            _LOG.exception("Notch 자동 감지 실패")
            self.done.emit(AutoNotch(None, 0, 0, 0))
