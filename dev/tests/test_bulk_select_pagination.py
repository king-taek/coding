"""요청 5 — 선택 모드 다이얼로그 페이지네이션 / 크기 슬라이더 / 선택 유지."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.models.slot import ImageItem          # noqa: E402
from aoi_verification.app.ui.widgets.bulk_select_dialog import (  # noqa: E402
    BulkSelectDialog, _PAGE_SIZE, _PAGINATE_THRESHOLD)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _data(slot: str, n: int) -> list[ImageItem]:
    return [ImageItem(slot=slot, path=Path(f"/tmp/{slot}_{i}.jpg"), side="ref")
            for i in range(n)]


def test_no_pagination_below_threshold(qapp):
    data = {"S1": _data("S1", 50), "S2": _data("S2", 50)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    assert dlg._paginated is False
    assert dlg._page_count == 1
    # 모든 항목이 한 페이지(=flat 전체)에 들어간다.
    assert len(dlg._page_slice()) == 100
    dlg.deleteLater()


def test_pagination_200_per_page(qapp):
    total = 1000
    assert total >= _PAGINATE_THRESHOLD
    data = {"S1": _data("S1", total)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    assert dlg._paginated is True
    assert dlg._page_count == total // _PAGE_SIZE        # 1000/200 = 5
    assert len(dlg._page_slice()) == _PAGE_SIZE
    # 마지막 페이지로 이동.
    dlg._go_page(dlg._page_count - 1)
    assert dlg._page == dlg._page_count - 1
    assert len(dlg._page_slice()) == _PAGE_SIZE
    dlg.deleteLater()


def test_selection_persists_across_pages(qapp):
    data = {"S1": _data("S1", 1000)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])

    # 1 페이지의 첫 타일 선택.
    first_item = dlg._page_slice()[0][1]
    dlg._on_tile_toggle(first_item, True)
    assert first_item.key in dlg._selected_keys

    # 다음 페이지로 이동해도 선택 상태(키)는 유지.
    dlg._go_page(1)
    assert first_item.key in dlg._selected_keys
    # 1 페이지로 돌아오면 타일 시각 상태도 복원.
    dlg._go_page(0)
    tile = dlg._tiles_by_key[first_item.key]
    assert tile._selected is True
    dlg.deleteLater()


def test_select_all_covers_all_pages(qapp):
    data = {"S1": _data("S1", 600)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    dlg._select_all()
    assert len(dlg._selected_keys) == 600
    dlg.deleteLater()


def test_size_slider_changes_tile_px(qapp):
    data = {"S1": _data("S1", 10)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    dlg._on_size_changed(260)
    assert dlg._tile_px == 260
    # 재렌더된 타일이 새 크기를 반영.
    any_tile = next(iter(dlg._tiles_by_key.values()))
    assert any_tile._tile_px == 260
    dlg.deleteLater()
