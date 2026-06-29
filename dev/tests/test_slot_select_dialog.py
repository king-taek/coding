"""'일부 슬롯만 진행' 카드 그리드 선택 다이얼로그 동작 검증.

작은 체크박스 대신 카드 전체 클릭 토글로 바뀐 SlotSelectDialog 의 선택 로직을
헤드리스(offscreen)로 단위 검증한다.  PyQt6 미설치 환경에선 자동 skip.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.ui.widgets.slot_select_dialog import (  # noqa: E402
    SlotSelectDialog)

_NAMES = ["S01", "S02", "S03", "S04", "S05"]


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_preselected_reflected_in_selected(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected={"S02", "S04"})
    assert dlg.selected == {"S02", "S04"}


def test_default_preselects_all(qapp):
    # preselected 미지정 → 전체 선택으로 시작(기존 동작 보존).
    dlg = SlotSelectDialog(_NAMES)
    assert dlg.selected == set(_NAMES)


def test_tile_toggle_updates_selection(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected=set())
    assert dlg.selected == set()
    dlg._tiles["S03"]._toggle()           # 카드 클릭(좌클릭) 동작과 동일
    assert dlg.selected == {"S03"}
    dlg._tiles["S03"]._toggle()           # 다시 누르면 해제
    assert dlg.selected == set()


def test_set_all(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected={"S01"})
    dlg._set_all(True)
    assert dlg.selected == set(_NAMES)
    dlg._set_all(False)
    assert dlg.selected == set()


def test_count_label_tracks_selection(qapp):
    dlg = SlotSelectDialog(_NAMES, preselected={"S01", "S02"})
    assert "2 / 5" in dlg._count_label.text()
    dlg._set_all(True)
    assert "5 / 5" in dlg._count_label.text()
