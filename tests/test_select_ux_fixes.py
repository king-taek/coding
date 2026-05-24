"""후보 선택/실패 검토 UX 수정 회귀 테스트.

- 메인 후보 패널: 헤더 인라인 툴바 제거(‘선택 모드’ 버튼만), 타일 더블클릭=해제,
  ThumbGrid 반응형 컬럼(가로 스크롤 방지), 우측 패널 중복 버튼 제거.
- 선택 모드 팝업: 드래그(러버밴드) 다중선택.
- 매치 실패 검토: 확정 시 목록에서 제거 + 다음으로 이동, 후보 테두리 셀렉터
  스코프, 목록 썸네일.
- GPU/NPU 미감지 시 진단 사유.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt   # noqa: E402
from PyQt6.QtGui import QMouseEvent                            # noqa: E402
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from aoi_verification.app.models.result import MissEntry       # noqa: E402
from aoi_verification.app.models.slot import ImageItem         # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dbl_event() -> QMouseEvent:
    return QMouseEvent(QEvent.Type.MouseButtonDblClick, QPointF(5, 5),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


# ---------------------------------------------------------------------------
# 메인 후보 패널
# ---------------------------------------------------------------------------
def test_left_panel_header_has_only_select_mode_button(qapp):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    page = SelectPage()
    lp = page.left_panel
    assert hasattr(lp, "_select_btn")        # ‘선택 모드’ 버튼 유지
    assert not hasattr(lp, "_sel_count")     # 인라인 카운트/툴바 제거
    assert not hasattr(lp, "_action_btns")


def test_right_panel_dropped_redundant_remove_action(qapp):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    page = SelectPage()
    action_ids = [a[0] for a in page.right_panel._actions]
    assert "remove" not in action_ids
    assert action_ids == ["to_exclude", "recenter"]


def test_tile_double_click_deselects(qapp):
    from aoi_verification.app.ui.widgets.thumb_grid import _ThumbTile, ThumbEntry
    tile = _ThumbTile(
        ThumbEntry(item=ImageItem(slot="A1", path=Path("/tmp/c.jpg"), side="ref")),
        inline_select=True,
    )
    emitted: list[bool] = []
    tile.sel_toggled.connect(lambda ent, sel: emitted.append(sel))

    tile.set_inline_selected(True)
    tile.mouseDoubleClickEvent(_dbl_event())
    assert tile.is_inline_selected() is False     # 선택→더블클릭=해제
    assert emitted[-1] is False

    # 미선택 상태에서 더블클릭해도 선택되지 않는다(항상 해제로 끝).
    tile.set_inline_selected(False)
    tile.mouseDoubleClickEvent(_dbl_event())
    assert tile.is_inline_selected() is False


def test_thumbgrid_responsive_columns_shrink_when_narrow(qapp):
    from PyQt6.QtGui import QResizeEvent
    from aoi_verification.app.ui.widgets.thumb_grid import ThumbGrid, ThumbEntry
    g = ThumbGrid(columns=3, inline_select=True, tile_px=120)
    g.set_entries([
        ThumbEntry(item=ImageItem(slot="A1", path=Path(f"/tmp/c{i}.jpg"), side="ref"))
        for i in range(6)
    ])
    g.resize(900, 600)
    assert g._effective_columns() == 3            # 넓으면 설정값(3) 유지
    g.resize(150, 600)
    g.resizeEvent(QResizeEvent(g.size(), g.size()))
    assert g._active_cols == 1                     # 좁으면 1열로 reflow


# ---------------------------------------------------------------------------
# 선택 모드 팝업 — 드래그 다중선택
# ---------------------------------------------------------------------------
def test_bulk_dialog_drag_selects_intersecting_tiles(qapp):
    from aoi_verification.app.ui.widgets.bulk_select_dialog import BulkSelectDialog
    items = [ImageItem(slot="A1", path=Path(f"/tmp/x{i}.jpg"), side="ref")
             for i in range(4)]
    dlg = BulkSelectDialog("t", {"A1": items},
                           [("to_exclude", "제외로 이동", "warn")])
    dlg.resize(900, 700)
    dlg.show()
    qapp.processEvents()
    dlg._relayout_grids()
    vp = dlg._scroll.viewport()
    dlg._select_in_rect(QRect(QPoint(0, 0), vp.size()))   # 전체 덮는 드래그
    assert len(dlg._selected_keys) == len(dlg._tiles_by_key) == 4
    # 선택 테두리는 최외곽 프레임(#selTile)에만 — 내부 라벨엔 번지지 않음.
    tile = next(iter(dlg._tiles_by_key.values()))
    assert "#selTile" in tile.styleSheet()


# ---------------------------------------------------------------------------
# 매치 실패 검토
# ---------------------------------------------------------------------------
def _make_review_dialog():
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import (
        UnmatchedReviewDialog,
    )
    slots = ["A1", "A2", "A3"]
    unmatched = [MissEntry(slot=s, side="ref", path=Path(f"/tmp/ref_{s}.jpg"))
                 for s in slots]
    val_pool = {s: [ImageItem(slot=s, path=Path(f"/tmp/val_{s}.jpg"), side="val")]
                for s in slots}
    dlg = UnmatchedReviewDialog(unmatched, val_pool)
    dlg._lookup_or_compute_score = lambda ref, val: 0.9    # 무거운 pipeline 회피
    return dlg, val_pool


def test_confirm_removes_entry_and_advances(qapp, monkeypatch):
    import aoi_verification.app.ui.widgets.unmatched_review_dialog as M
    monkeypatch.setattr(M.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))
    dlg, val_pool = _make_review_dialog()
    dlg.resize(1000, 700)
    dlg.show()
    qapp.processEvents()

    assert dlg.fail_list.count() == 3
    # 목록 항목은 파일명이 아니라 썸네일 아이콘 + 슬롯 태그.
    assert not dlg.fail_list.item(0).icon().isNull()
    assert dlg.fail_list.item(0).text() == "[A1]"

    dlg._idx = 0
    dlg._render_current()
    dlg._on_tile_selected(val_pool["A1"][0])   # 후보 보류 선택
    dlg._on_confirm()                          # 매치 확정

    assert dlg.fail_list.count() == 2          # A1 이 목록에서 사라짐
    remaining = {dlg._unmatched[dlg._row_to_idx[r]].slot
                 for r in range(dlg.fail_list.count()) if r in dlg._row_to_idx}
    assert remaining == {"A2", "A3"}
    assert dlg._unmatched[dlg._idx].slot == "A2"   # 다음 미해결로 이동
    assert len(dlg.new_matches) == 1
    assert len(dlg.resolved_refs) == 1


def test_candidate_tile_border_is_object_scoped(qapp):
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import _CandidateTile
    tile = _CandidateTile(
        ImageItem(slot="A1", path=Path("/tmp/v.jpg"), side="val"), 0.8,
    )
    tile.set_selected(True)
    # objectName 스코프 셀렉터 → 내부 QLabel(이미지/점수/캡션)엔 테두리가 번지지 않음.
    assert "#candTile" in tile.styleSheet()
    assert tile._img_label.styleSheet() == ""


# ---------------------------------------------------------------------------
# GPU/NPU 감지 진단
# ---------------------------------------------------------------------------
def test_accelerator_presence_reports_reason():
    from aoi_verification.app.learning import embedder_openvino as ov
    info = ov.accelerator_presence()
    assert set(info) >= {"GPU", "NPU", "devices", "reason"}
    # 가속 장치가 안 잡히면 사유 문자열이 채워져야(툴팁 진단용).
    if not (info["GPU"] or info["NPU"]):
        assert info["reason"]
