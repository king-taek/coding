"""GroupingResult.detach + GroupReviewPage 기본 동작."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aoi_verification.app.models.group import (
    GroupingResult, PhotoGroup,
)
from aoi_verification.app.models.slot import ImageItem


def _item(slot: str, name: str) -> ImageItem:
    return ImageItem(slot=slot, path=Path(f"/tmp/{slot}/{name}"), side="ref")


def _make_grouping():
    """3 장으로 묶인 그룹 + 단독 사진 1 장."""
    rep = _item("S1", "a.jpeg")
    sib1 = _item("S1", "b.jpeg")
    sib2 = _item("S1", "c.jpeg")
    single = _item("S1", "z.jpeg")
    grp = PhotoGroup(slot="S1", rep=rep, siblings=[sib1, sib2])
    return GroupingResult(
        representatives=[rep, single],
        by_rep={rep.key: grp},
        item_to_group={rep.key: grp, sib1.key: grp, sib2.key: grp},
    ), rep, sib1, sib2, single


def test_detach_sibling_adds_to_representatives():
    g, rep, sib1, sib2, single = _make_grouping()
    added = g.detach(sib1)
    assert added == [sib1]
    # sib1 이 representatives 에 들어가야.
    keys = {r.key for r in g.representatives}
    assert sib1.key in keys
    # 그룹은 여전히 살아있음 (sib2 가 남음).
    assert rep.key in g.by_rep


def test_detach_rep_dissolves_group_and_promotes_siblings():
    g, rep, sib1, sib2, single = _make_grouping()
    added = g.detach(rep)
    # siblings 가 모두 representatives 에 추가됨.
    assert sib1 in added and sib2 in added
    keys = {r.key for r in g.representatives}
    assert sib1.key in keys and sib2.key in keys
    # 그룹은 dissolved.
    assert rep.key not in g.by_rep


def test_detach_singleton_no_op():
    g, _rep, _sib1, _sib2, single = _make_grouping()
    added = g.detach(single)
    # single 은 그룹에 속하지 않음 → no-op.
    assert added == []


def test_detach_idempotent():
    g, _rep, sib1, _sib2, _single = _make_grouping()
    g.detach(sib1)
    again = g.detach(sib1)
    # 두 번째 detach 는 추가 변화 없음.
    assert again == []


# ---- GUI smoke ----------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication                    # noqa: E402

from aoi_verification.app.ui.pages.group_review_page import (  # noqa: E402
    GroupReviewPage,
)
from aoi_verification.app.ui.pages.match_review_page import (  # noqa: E402
    MatchReviewPage,
)
from aoi_verification.app.models.result import MatchResult     # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_group_review_page_initial_state(qapp):
    p = GroupReviewPage()
    # 빈 grouping 으로 load
    p.load_state(GroupingResult(representatives=[]))
    assert p.get_queue() == []


def test_group_review_page_with_groups(qapp):
    g, _rep, _sib1, _sib2, _single = _make_grouping()
    p = GroupReviewPage()
    p.load_state(g)
    queue = p.get_queue()
    # rep + single 이 큐에 있어야.
    assert len(queue) == 2


def test_match_review_page_kept_and_unmatched(qapp):
    p = MatchReviewPage()
    m1 = MatchResult(slot="S1", ref_path=Path("/r1"), val_path=Path("/v1"),
                     score=0.9, direction="A→B")
    m2 = MatchResult(slot="S1", ref_path=Path("/r2"), val_path=Path("/v2"),
                     score=0.7, direction="A→B")
    p.load_state([m1, m2])
    # m1 만 ‘매치 없음’ 으로 표시.
    p._on_toggle(m1)
    assert m1.key in p._unmatched_keys
    # 다시 토글하면 복원.
    p._on_toggle(m1)
    assert m1.key not in p._unmatched_keys
    # m1 다시 toggle 후 finished 시그널.
    p._on_toggle(m1)
    captured = []
    p.finished.connect(lambda kept, unm: captured.append((list(kept), list(unm))))
    p._on_done()
    kept, unmatched = captured[0]
    assert len(kept) == 1 and kept[0].ref_path == m2.ref_path
    assert len(unmatched) == 1 and unmatched[0].path == m1.ref_path
