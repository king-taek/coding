"""업데이트 후 첫 실행 때 띄울 '바뀐 점' 고르기·합치기 — 순수 함수(헤드리스 테스트).

마지막으로 본 항목 id 는 prefs(``whats_new_seen``)에 남는다.  그보다 새 항목을 전부
보여 준다 — 업데이트를 여러 번 건너뛰었으면 밀린 안내가 한 창에 합쳐진다.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["View", "pending", "merge"]


@dataclass(frozen=True)
class View:
    """팝업 한 장 — 최신 항목이 대표, 나머지는 '그 밖에' 로."""
    date: str
    headline: str
    summary: str
    image: str
    others: tuple            # ((화면 이름, 한 줄), ...)
    notices: tuple           # 주의 문구들(최신 먼저)


def pending(seen: str, entries) -> list:
    """아직 안 본 항목(최신이 앞).

    ``seen`` 이 비었거나 목록에 없으면(이 기능 이전 사용자·지워진 항목) **최신 1개만**
    보여 준다 — 과거 이력 전체를 쏟아 내지 않는다."""
    entries = list(entries)
    ids = [e["id"] for e in entries]
    if not entries:
        return []
    if not seen or seen not in ids:
        return entries[:1]
    return entries[:ids.index(seen)]


def merge(entries) -> View:
    """여러 업데이트를 한 장으로 — 최신의 대표 기능을 크게, 이전 업데이트의 대표 기능은
    '그 밖에' 맨 뒤에 한 줄로(제목의 줄바꿈은 공백으로) 붙인다."""
    head, rest = entries[0], entries[1:]
    others = list(head.get("others", ()))
    for e in rest:
        others.append(("", e["headline"].replace("\n", " ")))
        others.extend(e.get("others", ()))
    notices = tuple(e["notice"] for e in entries if e.get("notice"))
    return View(date=head["date"].replace("-", "."), headline=head["headline"],
                summary=head.get("summary", ""), image=head.get("image", ""),
                others=tuple(tuple(o) for o in others), notices=notices)
