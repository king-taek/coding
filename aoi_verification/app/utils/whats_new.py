"""업데이트 후 첫 실행 때 띄울 '바뀐 점' 고르기·문구 만들기 — 순수 함수(헤드리스 테스트).

마지막으로 본 항목 id 는 prefs(``whats_new_seen``)에 남는다.  그보다 새 항목을 전부
보여 준다 — 업데이트를 여러 번 건너뛰었으면 밀린 안내가 한 번에 뜬다.
"""
from __future__ import annotations

from .. import i18n

__all__ = ["pending", "render"]


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


def render(entries) -> str:
    """팝업 본문 — 날짜 한 줄 + 항목마다 한 줄.  여러 업데이트면 빈 줄로 나눈다."""
    blocks = []
    for e in entries:
        lines = [e["date"]] + [i18n.KO.WHATS_NEW_ITEM_FMT.format(text=t)
                               for t in e["items"]]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
