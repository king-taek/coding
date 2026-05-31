"""MatchExpandView — 후보 이동/확정 동작 단위 테스트."""

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from aoi_verification.app.models.slot import ImageItem         # noqa: E402
from aoi_verification.app.ui.widgets.match_expand_view import (  # noqa: E402
    MatchExpandView,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(n: int):
    return [ImageItem(slot="S1", path=Path(f"/tmp/S1/{i}.jpeg"), side="val")
            for i in range(n)]


def test_load_initial_index_zero(qapp):
    v = MatchExpandView()
    cands = _items(3)
    v.load_candidates("S1", cands, start_index=0)
    assert v.current_item() is cands[0]
    assert v.btn_prev.isEnabled() is False
    assert v.btn_next.isEnabled() is True


def test_load_initial_index_middle(qapp):
    v = MatchExpandView()
    cands = _items(3)
    v.load_candidates("S1", cands, start_index=1)
    assert v.current_item() is cands[1]
    assert v.btn_prev.isEnabled() is True
    assert v.btn_next.isEnabled() is True


def test_navigate_prev_next(qapp):
    v = MatchExpandView()
    cands = _items(3)
    v.load_candidates("S1", cands, start_index=1)
    v._on_next()
    assert v.current_item() is cands[2]
    assert v.btn_next.isEnabled() is False        # 마지막
    v._on_prev()
    assert v.current_item() is cands[1]


def test_confirm_emits_current(qapp):
    v = MatchExpandView()
    cands = _items(2)
    v.load_candidates("S1", cands, start_index=1)
    seen = []
    v.confirm_match.connect(lambda it: seen.append(it))
    v._on_confirm()
    assert seen == [cands[1]]


def test_empty_candidates_disables_buttons(qapp):
    v = MatchExpandView()
    v.load_candidates("S1", [], start_index=0)
    assert v.current_item() is None
    assert v.btn_prev.isEnabled() is False
    assert v.btn_next.isEnabled() is False
    assert v.btn_confirm.isEnabled() is False


def test_position_label_format(qapp):
    v = MatchExpandView()
    cands = _items(5)
    v.load_candidates("S1", cands, start_index=2)
    assert "3 / 5" in v.pos_label.text()
