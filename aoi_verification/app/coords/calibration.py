"""KLA ↔ Camtek 좌표 보정 — 사용자가 고른 '같은 결함' 사진 쌍으로 어긋남을 구한다.

두 장비의 die-내부 좌표(:class:`~.models.DefectCoord` ``x``/``y``)는 같은 틀로
변환되지만, 실측 쌍에서도 같은 결함이 100 µm 넘게 떨어져 있었다
(``docs/좌표_검증_필요자료.md`` — 119 µm).  허용 오차(기본 200 µm)의 절반을 그
고정 어긋남이 먹는다.  매칭 직전에 사용자가 쌍 몇 개를 고르면 그 차이로
**평행이동 보정값** ``(dx, dy)`` 를 구해 KLA 쪽 좌표에 더한다(사용자 결정:
평행이동만·실행 전체 1회·저장 안 함).  쌍을 고르기 전에 자료에서 자동으로 추정한 값
(:func:`auto_offset`)을 먼저 내밀고, 믿을 만하면 사용자가 그대로 적용할 수 있다
(사용자 결정 2026-10-24 — 하드코딩 대신 실행마다 계산).

★ **보정은 매칭 계산 안에서만 쓴다**(사용자 지시).  :func:`apply_offset` 은 새 dict 를
돌려줄 뿐 ``coords.resolve`` 의 캐시·엑셀·화면 좌표는 건드리지 않는다.

부호 규약 — 보정값은 **Camtek − KLA** 다(KLA 가 기준이든 검증이든 같다)::

    kla_보정 = (kla.x + dx, kla.y + dy)   ≈ cam

Qt 없이 테스트되는 순수 함수만 둔다(``dev/tests/test_coord_calibration.py``).
"""

from __future__ import annotations

import math
from dataclasses import replace
from statistics import median
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

from .models import DefectCoord

__all__ = ["Offset", "ZERO", "SEARCH_RADIUS_UM", "is_zero", "pair_offset",
           "estimate_offset", "residuals", "guess_offset", "AutoOffset", "auto_offset",
           "is_confident", "build_entries", "order_candidates",
           "near_candidates",
           "apply_offset"]

Offset = Tuple[float, float]
ZERO: Offset = (0.0, 0.0)

# 보정 화면에서 짝 후보로 보여 줄 반경(µm, 보정 전 거리).  보정할 어긋남 자체가 이
# 안에 들어와야 고를 수 있다 — 실측 어긋남(119 µm)의 몇 배, 매칭 후보 반경(3×tol =
# 600 µm)보다 넓게 잡는다.
SEARCH_RADIUS_UM = 1000.0
# :func:`guess_offset` 의 투표 칸 크기(µm).
_VOTE_BIN_UM = 20.0
# :func:`auto_offset` 의 신뢰 기준.  관측(2026-10-24, TB500 RDL4 LOT 2개 44장 —
# VPK 4F-K1↔AOI-20 · LSU ↔AOI-23, 서로 다른 결함 수로 셈): LOT 하나를 한 실행으로 보면
# 추정값 주변 VPK 42 · LSU 122, 그 밖 가장 큰 무리 15 · 31.  웨이퍼 한 장씩 보면 결함이
# 몰린 웨이퍼(31266524EWG5)가 (117, −72) 를 냈는데 근거가 5뿐이었고, 한 장으로 '믿을 만'
# 판정을 받은 3장은 모두 정답(87, −91)에서 3 µm 안이었다.  아래 두 값은 그 사이의 **가정**이다.
_SUPPORT_UM = 30.0          # 추정값 주변 이 거리 안에 표를 던진 결함을 '지지' 로 센다
MIN_AUTO_SUPPORT = 8        # 지지 결함이 이보다 적으면 자동값을 믿지 않는다
AUTO_DOMINANCE = 2.0        # 지지 결함이 두 번째 무리의 이 배수를 넘어야 한다


def is_zero(offset: Optional[Offset]) -> bool:
    return offset is None or (offset[0] == 0.0 and offset[1] == 0.0)


def pair_offset(kla: DefectCoord, cam: DefectCoord) -> Offset:
    """같은 결함인 KLA·Camtek 좌표 한 쌍의 어긋남 ``cam − kla``."""
    return (cam.x - kla.x, cam.y - kla.y)


def estimate_offset(pairs: Sequence[Offset]) -> Offset:
    """쌍별 어긋남 → 보정값.  축마다 **중앙값**(잘못 고른 쌍 하나에 끌려가지 않게)."""
    if not pairs:
        return ZERO
    return (float(median(p[0] for p in pairs)), float(median(p[1] for p in pairs)))


def residuals(pairs: Sequence[Offset], offset: Offset) -> List[float]:
    """보정 뒤 쌍마다 남는 거리(µm) — 크면 그 쌍을 잘못 골랐거나 회전이 있다."""
    return [math.hypot(p[0] - offset[0], p[1] - offset[1]) for p in pairs]


def _neighbors(src: DefectCoord,
               others: Iterable[Tuple[object, DefectCoord]],
               radius: float) -> List[Tuple[object, DefectCoord, float]]:
    """``src`` 와 (col,row) ±1 이내이고 보정 전 거리가 ``radius`` 이내인 것들.

    die 게이트는 매칭(`coord_matcher._match_neighbors`)과 같은 ±1 이다."""
    out = []
    for key, c in others:
        if abs(c.col - src.col) > 1 or abs(c.row - src.row) > 1:
            continue
        d = math.hypot(c.x - src.x, c.y - src.y)
        if d <= radius:
            out.append((key, c, d))
    return out


def _cam_minus_kla(src: DefectCoord, other: DefectCoord, src_is_kla: bool) -> Offset:
    return pair_offset(src, other) if src_is_kla else pair_offset(other, src)


def guess_offset(groups: Iterable[Tuple[Sequence[DefectCoord], Sequence[DefectCoord]]],
                 *, src_is_kla: bool,
                 radius: float = SEARCH_RADIUS_UM) -> Optional[Offset]:
    """사용자가 아직 쌍을 고르기 전, **가장 많은 쌍이 공유하는 어긋남**을 추정한다.

    ``groups`` 는 슬롯마다 ``(기준 좌표들, 검증 좌표들)`` — 매칭처럼 같은 슬롯끼리만
    본다.  이웃 후보 전부의 ``cam − kla`` 를 ``_VOTE_BIN_UM`` 칸에 투표해 가장 많이
    모인 칸(과 그 이웃 칸)의 중앙값을 낸다.  진짜 같은 결함들은 같은 어긋남을
    공유하고, 우연한 이웃은 흩어진다.  후보 정렬 힌트이자 :func:`auto_offset` 의 추정값
    이다 — 보정값으로 쓸 때는 늘 사용자가 화면에서 확인하고 적용한다.  표가 2표 미만이면 None."""
    votes: List[Offset] = []
    for src, others in groups:
        indexed = list(enumerate(others))
        for s in src:
            for _k, c, _d in _neighbors(s, indexed, radius):
                votes.append(_cam_minus_kla(s, c, src_is_kla))
    if len(votes) < 2:
        return None
    bins: Dict[Tuple[int, int], int] = {}
    for dx, dy in votes:
        k = (math.floor(dx / _VOTE_BIN_UM), math.floor(dy / _VOTE_BIN_UM))
        bins[k] = bins.get(k, 0) + 1

    def score(k):
        return sum(bins.get((k[0] + a, k[1] + b), 0)
                   for a in (-1, 0, 1) for b in (-1, 0, 1))
    best = max(bins, key=lambda k: (score(k), bins[k]))
    if score(best) < 2:
        return None
    near = [(dx, dy) for dx, dy in votes
            if abs(math.floor(dx / _VOTE_BIN_UM) - best[0]) <= 1
            and abs(math.floor(dy / _VOTE_BIN_UM) - best[1]) <= 1]
    return estimate_offset(near)


class AutoOffset(NamedTuple):
    """자료에서 자동으로 추정한 보정값과 그 근거(표 수)."""
    offset: Offset
    support: int        # 추정값 주변 ``_SUPPORT_UM`` 안에 표를 던진 서로 다른 결함 수
    runner_up: int      # 그 밖에서 가장 큰 무리의 서로 다른 결함 수
    votes: int          # 전체 표


def _votes(groups, *, src_is_kla: bool, radius: float) -> List[Tuple[Offset, int]]:
    """이웃 후보마다 ``(cam − kla, 출발 결함 번호)`` — 한 결함이 이웃 여럿에 표를 던지므로
    근거는 표 수가 아니라 **서로 다른 결함 수**로 센다."""
    out: List[Tuple[Offset, int]] = []
    n = 0
    for src, others in groups:
        indexed = list(enumerate(others))
        for s in src:
            for _k, c, _d in _neighbors(s, indexed, radius):
                out.append((_cam_minus_kla(s, c, src_is_kla), n))
            n += 1
    return out


def auto_offset(groups: Iterable[Tuple[Sequence[DefectCoord], Sequence[DefectCoord]]],
                *, src_is_kla: bool,
                radius: float = SEARCH_RADIUS_UM) -> Optional[AutoOffset]:
    """매칭할 때마다 **자료에서** 보정값을 추정한다 — 값을 저장하거나 하드코딩하지 않는다.

    원인(2026-10-24 분석, ``docs/좌표_KLA_Camtek_오프셋.md``): KLA·Camtek 레시피가 die
    기준점을 서로 다른 곳에 정의해 둔 것이라 웨이퍼·Camtek 장비와 무관하게 일정했지만,
    제품이 바뀌면 달라지는지는 모른다.  그래서 실행마다 다시 잰다.

    추정 자체는 :func:`guess_offset`(투표)이고, 여기서는 **믿을 만한지**를 판단할 근거를
    붙인다(:func:`is_confident`).  ``groups`` 는 실행 전체 슬롯을 넘긴다 — 웨이퍼 한 장
    으로는 결함이 몰린 곳에서 짝이 섞여 틀린 값이 나왔다(위 상수 주석).

    * ``support`` — 추정값에서 ``_SUPPORT_UM`` 안에 표를 던진 **서로 다른 결함** 수.
    * ``runner_up`` — 그 밖의 표 **전부**(빈 구간 없이)를 ``_SUPPORT_UM`` 칸으로 묶어,
      3×3 칸 안 서로 다른 결함 수가 가장 많은 무리.  칸 단위라 표 수에 비례해 돈다."""
    groups = [(list(s), list(o)) for s, o in groups]
    guess = guess_offset(groups, src_is_kla=src_is_kla, radius=radius)
    if guess is None:
        return None
    votes = _votes(groups, src_is_kla=src_is_kla, radius=radius)
    gx, gy = guess
    near_src = set()
    bins: Dict[Tuple[int, int], set] = {}
    for (dx, dy), sid in votes:
        if math.hypot(dx - gx, dy - gy) <= _SUPPORT_UM:
            near_src.add(sid)
        else:
            k = (math.floor(dx / _SUPPORT_UM), math.floor(dy / _SUPPORT_UM))
            bins.setdefault(k, set()).add(sid)
    runner = 0
    for kx, ky in bins:
        srcs = set()
        for a in (-1, 0, 1):
            for b in (-1, 0, 1):
                srcs |= bins.get((kx + a, ky + b), set())
        runner = max(runner, len(srcs))
    return AutoOffset(offset=guess, support=len(near_src), runner_up=runner,
                      votes=len(votes))


def is_confident(auto: Optional[AutoOffset]) -> bool:
    """자동값을 사용자에게 '그대로 적용' 으로 내밀어도 되는가 — 경계(정확히 2배)는 믿지 않는다."""
    return (auto is not None and auto.support >= MIN_AUTO_SUPPORT
            and auto.support > AUTO_DOMINANCE * auto.runner_up)


def build_entries(groups, *, src_is_kla: bool, radius: float = SEARCH_RADIUS_UM):
    """보정 화면 데이터 — 짝 후보가 하나라도 있는 기준 사진 목록.

    ``groups`` 는 슬롯마다 ``([(기준키, 좌표)], [(검증키, 좌표)])``(좌표 None 은 뺀다).
    반환 ``[(기준키, 좌표, [(검증키, 좌표, 보정 전 거리)])]`` — 후보가 적은 사진이
    먼저다(짝이 분명해 고르기 쉽다).  후보 순서는 화면이 :func:`order_candidates` 로
    매번 정한다(사용자가 쌍을 고를수록 힌트가 바뀐다)."""
    out = []
    for src, others in groups:
        others = [(k, c) for k, c in others if c is not None]
        for key, c in src:
            if c is None:
                continue
            found = _neighbors(c, others, radius)
            if found:
                out.append((key, c, found))
    out.sort(key=lambda e: len(e[2]))
    return out


def order_candidates(src: DefectCoord, found, *, src_is_kla: bool,
                     hint: Optional[Offset]):
    """:func:`build_entries` 의 후보 목록을 ``hint`` 적용 거리 오름차순으로."""
    hx, hy = hint if hint is not None else ZERO

    def corrected(item):
        _k, c, _d = item
        dx, dy = _cam_minus_kla(src, c, src_is_kla)
        return math.hypot(dx - hx, dy - hy)
    return sorted(found, key=corrected)


def near_candidates(src: DefectCoord,
                    others: Iterable[Tuple[object, DefectCoord]],
                    *, src_is_kla: bool, hint: Optional[Offset] = None,
                    radius: float = SEARCH_RADIUS_UM) -> List[Tuple[object, float]]:
    """``src`` 의 짝 후보 ``(key, 보정 전 거리 µm)`` 를 그럴듯한 순으로.

    순서는 ``hint``(지금까지의 보정값 또는 :func:`guess_offset`)를 적용한 거리 오름차순
    — 힌트가 맞으면 진짜 짝이 맨 앞에 온다.  표시 거리는 **보정 전** 원래 거리다."""
    found = order_candidates(src, _neighbors(src, others, radius),
                             src_is_kla=src_is_kla, hint=hint)
    return [(k, d) for k, _c, d in found]


def apply_offset(coord_map: Dict[object, Optional[DefectCoord]],
                 offset: Optional[Offset]) -> Dict[object, Optional[DefectCoord]]:
    """KLA 좌표(``source == "kla"``)에만 보정값을 더한 **새** dict.

    원본 dict·:class:`DefectCoord`(frozen) 는 그대로다 — 보정이 엑셀·화면 좌표로
    새지 않는다.  ``native_x``/``native_y``(KLA 원본값)도 건드리지 않는다.
    die 경계를 넘는 경우 col/row 를 다시 매기지 않는다 — 매칭의 die 게이트가 ±1 이라
    인덱스는 그대로 두는 편이 기존 동작과 같다."""
    if is_zero(offset):
        return dict(coord_map)
    dx, dy = offset
    out: Dict[object, Optional[DefectCoord]] = {}
    for k, c in coord_map.items():
        if c is not None and c.source == "kla":
            c = replace(c, x=c.x + dx, y=c.y + dy)
        out[k] = c
    return out
