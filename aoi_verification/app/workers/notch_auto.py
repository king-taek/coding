"""Notch 방향 자동 감지 — 검증 폴더를 네 방향으로 돌려 보고 기준과 가장 많이 맞는 방향을 고른다.

좌표 매칭이 쓰는 같은 판정(`coord_matcher._match_neighbors`: (col,row) ±1 · die-내부 거리 ≤ tol)
으로, 기준 사진이 몇 장이나 짝을 찾는지 센다.  틀린 방향은 짝이 거의 안 나오므로 맞는 방향이
뚜렷이 많다.  뚜렷하지 않으면(지지 수가 적거나 2등과 비슷) 값을 내지 않는다 —
``coords.calibration.auto_offset`` 과 같은 태도다(``MIN_SUPPORT``·``DOMINANCE`` 는 **가정**).

사진 수가 많아도 느리지 않게 앞쪽 슬롯 몇 개만 본다(:data:`MAX_SLOTS`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QThread, pyqtSignal

from .. import config as _config
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
    n = len(notch.DIRECTIONS)
    for i, d in enumerate(notch.DIRECTIONS):
        if progress:
            progress(i, n)
        dirs = ((str(ref_root), ref_dir), (str(val_root), d))
        try:
            fixed = notch.apply_notch(
                coords, lambda p: notch.notch_for_path(dirs, p))
        except notch.NotchError:
            continue                      # 이 방향은 돌릴 재료가 없다 — 후보에서 뺀다
        hit = 0
        for refs, vals in groups:
            vmap: Dict[Tuple[int, int], List] = {}
            for v in vals:
                c = fixed.get(v)
                if c is not None:
                    vmap.setdefault((c.col, c.row), []).append((v, c.x, c.y))
            for r in refs:
                c = fixed.get(r)
                if c is not None and _match_neighbors(c.x, c.y, c.col, c.row, vmap, tol):
                    hit += 1
        scores[d] = hit
    if progress:
        progress(n, n)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    if not ranked:
        return AutoNotch(None, 0, 0, total)
    best_d, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    ok = best >= MIN_SUPPORT and best > second * DOMINANCE
    _LOG.info("Notch 자동 감지: %s (짝 %d / 2등 %d / 기준 %d장)", scores, best, second, total)
    return AutoNotch(best_d if ok else None, best, second, total)


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
