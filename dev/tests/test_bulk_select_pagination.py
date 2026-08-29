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


def test_offpage_warning_follows_the_page(qapp):
    """'이 페이지 밖 m 장' 은 **페이지를 넘길 때마다** 다시 계산돼야 한다.

    ★ 이 문구는 선택 집합과 현재 페이지의 차집합이라, 선택을 건드리지 않아도
      페이지만 넘기면 값이 바뀐다.  갱신을 선택 이벤트에만 걸어 두었을 때는
      1 페이지에서 [전체 선택] 한 뒤 다음 페이지로 가도 경고가 없는 채
      "선택됨: 1000 장" 만 남아, 보이는 타일만 동작한다고 믿고 액션을 누를 수 있었다."""
    from aoi_verification.app import i18n
    off = i18n.KO.BULK_SELECT_SUMMARY_OFFPAGE_FMT.format
    plain = i18n.KO.BULK_SELECT_SUMMARY_FMT.format
    data = {"S1": _data("S1", 1000)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    try:
        assert dlg._paginated is True
        dlg._select_all()
        # 1 페이지: 200 장이 이 페이지에 보이고 800 장이 밖에 있다.
        assert dlg._summary_label.text() == off(n=1000, m=800)
        dlg._go_page(4)                    # 마지막 페이지로 이동(선택은 그대로)
        assert dlg._summary_label.text() == off(n=1000, m=800)

        dlg._clear_selection()
        dlg._go_page(0)
        # 이 페이지 것만 고르면 경고가 사라진다 — 값이 정말 페이지를 따라간다는 뜻.
        for _slot, item in dlg._page_slice():
            dlg._selected_keys.add(item.key)
            dlg._selected_items_by_key[item.key] = item
        dlg._render_page()
        assert dlg._summary_label.text() == plain(n=200)
        dlg._go_page(1)                    # 같은 200 장이 이제 전부 페이지 밖이다
        assert dlg._summary_label.text() == off(n=200, m=200)
    finally:
        dlg.deleteLater()


def test_size_slider_changes_tile_px(qapp):
    """슬라이더 값은 즉시, 타일 적용은 디바운스 뒤에.

    ★ 드래그 중 tick 마다 페이지를 통째로 재생성하면 999장 화면에서 한 번 드래그에
    수 초가 사라진다.  그래서 `_tile_px` 만 동기로 갱신하고 타일 재스케일은 150ms
    디바운스 타이머가 맡는다 — 테스트는 그 타이머를 강제로 발화시킨다."""
    data = {"S1": _data("S1", 10)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    dlg._on_size_changed(260)
    assert dlg._tile_px == 260                      # 값은 즉시
    assert dlg._resize_timer.isActive()             # 적용은 예약만
    dlg._apply_tile_size()                          # 디바운스 강제 발화
    any_tile = next(iter(dlg._tiles_by_key.values()))
    assert any_tile._tile_px == 260
    dlg.deleteLater()


def test_size_change_reuses_tiles_instead_of_rebuilding(qapp):
    """크기 변경이 타일을 **재생성하지 않는다**(같은 객체를 재스케일)."""
    data = {"S1": _data("S1", 6)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    before = dict(dlg._tiles_by_key)
    dlg._on_size_changed(240)
    dlg._apply_tile_size()
    assert {k: id(v) for k, v in dlg._tiles_by_key.items()} == \
           {k: id(v) for k, v in before.items()}, "타일이 재생성됐다"
    dlg.deleteLater()


def test_action_buttons_disabled_until_something_is_selected(qapp):
    """0 장일 때 액션 버튼을 눌러도 조용히 아무 일도 안 일어나던 '먹은 클릭' 방지."""
    data = {"S1": _data("S1", 4)}
    dlg = BulkSelectDialog("t", data, actions=[("x", "X", "primary")])
    assert all(not b.isEnabled() for b in dlg._action_buttons)
    dlg._select_all()
    assert all(b.isEnabled() for b in dlg._action_buttons)
    dlg._clear_selection()
    assert all(not b.isEnabled() for b in dlg._action_buttons)
    dlg.deleteLater()


def test_sheet_title_is_not_drawn_twice(qapp):
    """시트 chrome 이 windowTitle 을 그리므로 본문에 같은 제목을 또 그리지 않는다."""
    from PyQt6.QtWidgets import QLabel
    title = "검증 후보들 — 다중 선택"
    dlg = BulkSelectDialog(title, {"S1": _data("S1", 2)},
                           actions=[("x", "X", "primary")])
    same = [w for w in dlg.findChildren(QLabel) if w.text() == title]
    assert same == [], "본문에 시트 제목이 중복으로 그려졌다"
    dlg.deleteLater()
