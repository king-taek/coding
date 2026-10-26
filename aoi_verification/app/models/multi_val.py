"""기준 1 ↔ 검증 여러 장비 — 순수 로직(Qt 없음).

사용자 결정(2026-10-26):
  · 폴더 읽기·슬롯 짝짓기·좌표 보정·매칭은 **검증 장비마다 따로** 한다('구간' = leg).
    좌표 규칙(한 매칭 실행 안에 Camtek 좌표 프레임 하나 · 보정값은 실행마다 추정)이
    장비 단위로 지켜지게 하려는 것이다 — 장비를 한 실행에 섞으면 두 규칙이 다 깨진다.
  · 후보 선별은 기준 사진이라 **한 번**만 하고, 다른 구간은 같은 기준 사진(경로)을 쓴다.
  · 검토는 **한 번** — 기준 사진 1장 아래에 장비별 후보 줄(`MatchResult.equip`).
  · 교차검증 2차(검증 → 기준)는 장비마다 한 번씩.
  · 엑셀은 파일 하나 — 맨 앞 요약 시트 + 장비별 시트(지금 양식 그대로).

구간 안의 데이터는 장비 번호를 모른다(늘 ``equip=0``) — 기존 단일 실행 코드를 그대로
쓰기 위해서다.  번호는 합친 검토 화면에 실을 때만 붙이고(`tag`), 검토가 끝나면
떼어 구간으로 되돌린다(`split`).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional

from .result import FinalResult, MatchResult, MissEntry

# 요약 시트의 장비별 상태.
STATUS_FOUND = "found"          # 이 장비가 짝을 찾았다(검토로 확정)
STATUS_MISSED = "missed"        # 이 장비 쪽에 짝이 없다
STATUS_NO_SLOT = "no_slot"      # 이 장비와 짝지은 슬롯이 없다(그 웨이퍼를 비교하지 못함)


def tag(matches: Iterable[MatchResult], equip: int) -> list[MatchResult]:
    """구간의 매치에 장비 번호를 붙인다(합친 검토 화면에 싣기 전)."""
    return [replace(m, equip=equip) for m in matches]


def split(kept: Iterable[MatchResult], misses: Iterable[MissEntry], n: int
          ) -> list[tuple[list[MatchResult], list[MissEntry]]]:
    """합친 검토 결과 → 장비별 (매치, 미매칭).  장비 번호는 떼어 0 으로 돌린다."""
    out: list[tuple[list, list]] = [([], []) for _ in range(n)]
    for m in kept:
        out[m.equip][0].append(replace(m, equip=0))
    for u in misses:
        out[u.equip][1].append(replace(u, equip=0))
    return out


def review_order(matches: Iterable[MatchResult]) -> list[MatchResult]:
    """합친 검토 화면의 행 순서 — 같은 기준 사진의 장비별 줄이 붙어 있게.

    슬롯 → 기준 파일명 → 장비 번호.  (단일 실행의 좌표 모드는 점수순이지만, 여러
    장비면 '기준 사진 1장에 장비별 줄' 이 먼저다 — 사용자 제안.)"""
    return sorted(matches, key=lambda m: (m.slot, m.ref_path.name.lower(),
                                          str(m.ref_path), m.equip))


def leg_targets(ref_paths: Iterable, slots: dict, common: Iterable[str]
                ) -> dict[str, list]:
    """첫 구간에서 고른 기준 사진(경로)을 다른 구간의 선별 결과로 옮긴다.

    슬롯 **이름**은 구간마다 다를 수 있다(KLA WaferID 해석·수동 짝짓기가 이름을
    바꾼다).  그래서 경로로 찾는다 — 같은 기준 폴더라 경로는 같다.
    ``slots`` 는 그 구간의 ``ScanResult.slots``, ``common`` 은 양쪽이 다 있는 슬롯."""
    wanted = {Path(p) for p in ref_paths}
    out: dict[str, list] = {}
    for name in common:
        picked = [it for it in slots[name].ref_images if Path(it.path) in wanted]
        if picked:
            out[name] = picked
    return out


def summary_rows(results: list[FinalResult]) -> list[tuple[str, Path, list[tuple[str, Optional[Path]]]]]:
    """요약 시트 행 — 기준 사진마다 ``(슬롯, 기준 사진, [(상태, 검증 사진), …장비 순])``.

    기준 사진은 **어느 장비에서든** 결과에 오른 것(매치 또는 기준 쪽 미매칭)이다.
    슬롯 이름은 처음 나온 구간의 것을 쓴다(구간마다 다를 수 있다).
    검증 쪽에만 있는 사진(교차검증 2차의 '검증에만 있음')은 기준 사진이 없어 여기
    오르지 않는다 — 그 장비 시트에 있다."""
    slot_of: dict[Path, str] = {}
    per_leg: list[tuple[dict, set]] = []
    for res in results:
        found: dict[Path, Path] = {}
        for m in res.matches:
            p = Path(m.ref_path)
            found.setdefault(p, Path(m.val_path))
            slot_of.setdefault(p, m.slot)
        missed: set[Path] = set()
        for u in res.unmatched_refs:
            if u.side != "ref":
                continue
            p = Path(u.path)
            missed.add(p)
            slot_of.setdefault(p, u.slot)
        per_leg.append((found, missed))
    rows = []
    for p in sorted(slot_of, key=lambda q: (slot_of[q], q.name.lower(), str(q))):
        cells: list[tuple[str, Optional[Path]]] = []
        for found, missed in per_leg:
            if p in found:
                cells.append((STATUS_FOUND, found[p]))
            elif p in missed:
                cells.append((STATUS_MISSED, None))
            else:
                cells.append((STATUS_NO_SLOT, None))
        rows.append((slot_of[p], p, cells))
    return rows
