"""교차검증(양방향) — 기준→검증(A) 다음에 검증→기준(B)을 한 번 더 도는 실행의 순수 로직.

한 방향(A)만 돌면 **기준 장비가 놓친 결함**(검증에만 있는 사진)은 결과에 나오지 않는다.
교차검증은 A 를 끝낸 뒤 검증 사진을 큐로 삼아 같은 선별·매칭·검토를 한 번 더 돈다.

사용자 결정(2026-10-24):
  · B 의 사진도 하나씩 검토한다(선별 → 매칭 → 검토, A 와 같은 화면).
  · A 에서 이미 짝지은 검증 사진은 B 의 선별·검토에 **다시 나타나지 않고** 결과에만 남는다.

★ B 의 후보 풀은 기준 사진 **전부**다(A 에서 짝지은 것 포함).  빼면 '기준 결함 하나 ↔
  검증 사진 둘' 인 경우 두 번째 검증 사진이 짝을 잃고 '기준 미검출' 로 잘못 찍힌다 —
  없는 미탐을 만들어 내는 정확도 사고다.  빼는 것은 B 의 **큐**뿐이다.

B 의 매칭 페이지는 큐 쪽을 ``ref_path`` 로 담아 낸다(화면 코드는 방향을 모른다).
그래서 B 에서 나온 :class:`MatchResult` 는 '큐 방향' 이고, 결과로 보낼 때
:func:`swap` 으로 ``ref_path`` = 기준 사진인 '정방향' 으로 되돌린다.  Qt 없이 테스트한다
(``dev/tests/test_cross_check.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .result import MatchResult, MissEntry

# `FinalResult.mode`·`SetupInput.mode` 값.
CROSS_MODE = "cross"
SINGLE_MODE = "single"


def swap(m: MatchResult) -> MatchResult:
    """기준·검증 자리를 맞바꾼 새 매치 — 큐 방향 ↔ 정방향 변환(자기 역함수)."""
    return MatchResult(slot=m.slot, ref_path=m.val_path, val_path=m.ref_path,
                       score=m.score)


def reverse_queue(val_by_slot: dict, a_matches: Iterable[MatchResult]) -> dict:
    """B 의 큐 — 슬롯별 검증 사진에서 **A 에서 짝지은 사진을 뺀** 것.

    경로로 비교한다(슬롯명은 KLA 해석으로 바뀔 수 있지만 파일 경로는 그대로다).
    순서는 원래 순서를 지킨다."""
    used = {Path(m.val_path) for m in a_matches}
    return {slot: [it for it in items if Path(it.path) not in used]
            for slot, items in val_by_slot.items()}


def as_val_misses(entries: Iterable[MissEntry]) -> list[MissEntry]:
    """B 에서 짝을 못 찾은 사진(큐 방향 ``side="ref"``) → 검증 쪽 미매칭(``side="val"``).

    '검증 장비는 봤는데 기준 장비가 못 본' 결함이다."""
    return [MissEntry(slot=e.slot, side="val", path=Path(e.path), note=e.note)
            for e in entries]


def merge(a_matches: Iterable[MatchResult], a_misses: Iterable[MissEntry],
          b_matches: Iterable[MatchResult], b_misses: Iterable[MissEntry]
          ) -> tuple[list[MatchResult], list[MissEntry]]:
    """A·B 결과를 하나로 — ``b_matches`` 는 **정방향**(``ref_path`` = 기준 사진).

    불변식:
      · 같은 쌍(슬롯·기준·검증)은 한 번만 싣는다.
      · 짝을 찾은 사진은 그 쪽 미매칭에 남지 않는다 — A 에서 놓친 기준 사진이 B 에서
        검증 사진과 짝지어졌으면 그 기준 사진은 더 이상 미매칭이 아니다.
      · 같은 사진의 미매칭은 한 줄만.
    """
    matches: list[MatchResult] = []
    seen: set = set()
    for m in (*a_matches, *b_matches):
        if m.key in seen:
            continue
        seen.add(m.key)
        matches.append(m)
    matched = {"ref": {Path(m.ref_path) for m in matches},
               "val": {Path(m.val_path) for m in matches}}
    misses: list[MissEntry] = []
    seen_miss: set = set()
    for u in (*a_misses, *b_misses):
        p = Path(u.path)
        key = (u.side, p)
        if p in matched.get(u.side, ()) or key in seen_miss:
            continue
        seen_miss.add(key)
        misses.append(u)
    return matches, misses


def split_new_matches(matches: Iterable[MatchResult],
                      known: Iterable[MatchResult],
                      b_miss_paths: Iterable) -> tuple[list[MatchResult], list[MatchResult]]:
    """결과 화면(실패 검토)에서 **새로 생긴** 매치를 A 몫·B 몫으로 가른다(정방향 그대로).

    검증 쪽 미매칭(B 에서 놓친 검증 사진)을 풀어 생긴 매치는 그 검증 사진이 B 미매칭
    경로에 있다 → B 몫.  나머지(기준 쪽 미매칭을 푼 것)는 A 몫."""
    known_keys = {m.key for m in known}
    b_paths = {Path(p) for p in b_miss_paths}
    new_a: list[MatchResult] = []
    new_b: list[MatchResult] = []
    for m in matches:
        if m.key in known_keys:
            continue
        (new_b if Path(m.val_path) in b_paths else new_a).append(m)
    return new_a, new_b
