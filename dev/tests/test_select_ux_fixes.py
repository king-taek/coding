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
    dlg._lookup_or_compute_score = lambda ref, val, allow_compute=True: 0.9    # 무거운 pipeline 회피
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


def test_candidates_keep_two_columns_when_narrow(qapp):
    slots = ["A1"]
    unmatched = [MissEntry(slot="A1", side="ref", path=Path("/tmp/ref_A1.jpg"))]
    val_pool = {"A1": [ImageItem(slot="A1", path=Path(f"/tmp/v{i}.jpg"), side="val")
                       for i in range(5)]}
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import (
        UnmatchedReviewDialog,
    )
    dlg = UnmatchedReviewDialog(unmatched, val_pool)
    dlg._lookup_or_compute_score = lambda r, v, allow_compute=True: 0.9
    dlg.resize(1000, 700)
    dlg.show()
    qapp.processEvents()
    dlg._idx = 0
    dlg._render_current()
    # 후보 영역을 아주 좁게 → 타일이 축소되며 2열은 유지돼야 한다.
    dlg._scroll.setFixedWidth(360)
    qapp.processEvents()
    dlg._relayout_candidates()
    used_cols = max((dlg._grid.getItemPosition(i)[1]
                     for i in range(dlg._grid.count())), default=-1) + 1
    assert used_cols >= 2
    assert dlg._cand_tiles[0]._size < 260      # 슬라이더 기본보다 축소됨


def test_side_by_side_pane_image_does_not_grow(qapp):
    from PyQt6.QtWidgets import QSizePolicy
    from aoi_verification.app.ui.widgets.side_by_side_viewer import _Pane
    pane = _Pane("t")
    pol = pane._img.sizePolicy()
    # Ignored 정책 + 1×1 최소크기 → pixmap 이 레이아웃/창을 키우지 못함(성장 버그 방지).
    assert pol.horizontalPolicy() == QSizePolicy.Policy.Ignored
    assert pol.verticalPolicy() == QSizePolicy.Policy.Ignored
    assert pane._img.minimumSize().width() == 1


def test_ref_right_click_opens_compare_in_score_order(qapp, monkeypatch):
    # 기준 우클릭 → 후보와 동일한 SideBySideViewer 를, 유사도 1위(start=0)부터.
    import aoi_verification.app.ui.widgets.unmatched_review_dialog as M
    captured = {}

    class _FakeViewer:
        action_requested = type("S", (), {"connect": lambda self, *a: None})()
        def __init__(self, ref, candidates, start, **kw):
            captured["candidates"] = candidates
            captured["start"] = start
            self.action_requested = type("S", (), {"connect": staticmethod(lambda *a: None)})()
        def exec(self):
            return 0

    monkeypatch.setattr(M, "SideBySideViewer", _FakeViewer, raising=False)
    # _open_compare 가 지연 import 하므로 모듈 경로도 패치.
    import aoi_verification.app.ui.widgets.side_by_side_viewer as SBS
    monkeypatch.setattr(SBS, "SideBySideViewer", _FakeViewer)

    slots = ["A1"]
    unmatched = [MissEntry(slot="A1", side="ref", path=Path("/tmp/ref_A1.jpg"))]
    val_pool = {"A1": [ImageItem(slot="A1", path=Path(f"/tmp/v{i}.jpg"), side="val")
                       for i in range(3)]}
    dlg = M.UnmatchedReviewDialog(unmatched, val_pool)
    # 점수: v2 > v1 > v0 (내림차순 정렬 확인용)
    score = {"/tmp/v0.jpg": 0.5, "/tmp/v1.jpg": 0.7, "/tmp/v2.jpg": 0.9}
    dlg._lookup_or_compute_score = lambda r, v, allow_compute=True: score[str(v.path)]
    dlg.resize(900, 700); dlg.show(); qapp.processEvents()
    dlg._idx = 0; dlg._render_current()

    dlg._open_compare(0)
    assert captured["start"] == 0
    caps = [cap for _item, cap in captured["candidates"]]
    # 첫 후보가 가장 높은 유사도여야(내림차순).
    assert "90" in caps[0]


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
