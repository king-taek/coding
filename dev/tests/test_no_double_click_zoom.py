"""사진 **더블클릭 확대**를 없앤다 — 우클릭 '크게 보기' 는 남긴다.

왜: 사진을 연달아 고르는 화면에서 두 번째 클릭이 더블클릭으로 붙어 원하지 않는 확대
창이 떴다(사용자 지적).  선택/교체는 프레스마다 일어나는 **잦은** 동작이고 확대는
**드문** 동작인데, 같은 버튼의 연타에 둘을 겹쳐 두면 잦은 쪽이 드문 쪽을 오발한다.

제스처를 없애는 것과 기능을 없애는 것은 다르다 — 그래서 우클릭 경로가 남아 있는지도
함께 고정한다.  ``_MidTile`` 은 원래 더블클릭 하나뿐이었으므로 컨텍스트 메뉴를 새로
붙였다(그대로 지웠으면 ZoomWindow 에서 확대가 사라졌을 것이다).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                   # noqa: E402
from PyQt6.QtWidgets import QApplication                      # noqa: E402

from aoi_verification.app.models.slot import ImageItem        # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _item(name="a.jpg"):
    return ImageItem(slot="A1", path=Path("/tmp") / name, side="val")


# ── 사진 확대 타일들: 더블클릭 핸들러가 없어야 한다 ──────────────────────────
def _defines_own_double_click(cls) -> bool:
    """자기 클래스(또는 상속 체인의 자기 모듈)에서 핸들러를 정의했는지."""
    return "mouseDoubleClickEvent" in cls.__dict__


def test_zoom_window_tile_has_no_double_click():
    from aoi_verification.app.ui.widgets import zoom_window as zw
    assert not _defines_own_double_click(zw._MidTile)
    # 확대 경로는 우클릭으로 남아 있어야 한다.
    assert hasattr(zw._MidTile, "_on_context_menu")
    assert hasattr(zw._MidTile, "view_requested")
    assert not hasattr(zw._MidTile, "double_clicked"), "죽은 시그널 잔존"


def test_match_review_runner_up_tile_has_no_double_click():
    from aoi_verification.app.ui.pages import match_review_page as mrp
    assert not _defines_own_double_click(mrp._RunnerUpTile)
    assert hasattr(mrp._RunnerUpTile, "_on_context_menu")


def test_unmatched_candidate_tile_has_no_double_click():
    from aoi_verification.app.ui.widgets import unmatched_review_dialog as urd
    assert not _defines_own_double_click(urd._CandidateTile)
    assert hasattr(urd._CandidateTile, "_on_context_menu")


def test_unmatched_ref_image_is_not_monkeypatched(qapp):
    """기준 사진에 걸려 있던 **인스턴스 메서드 몽키패치**도 사라져야 한다.

    ``self.ref_img.mouseDoubleClickEvent = lambda …`` 는 위젯이 자기 클래스 계약 밖에서
    동작하게 만들어 다음 사람이 찾을 수 없다 — 제스처보다 이 방식이 더 나빴다."""
    import inspect
    from aoi_verification.app.ui.widgets import unmatched_review_dialog as urd
    src = inspect.getsource(urd.UnmatchedReviewDialog)
    assert "mouseDoubleClickEvent" not in src, "기준 사진 더블클릭 몽키패치 잔존"
    # 우클릭 경로는 유지.
    assert "_on_ref_context_menu" in src


# ── 확대가 아닌 더블클릭은 건드리지 않는다 ──────────────────────────────────
def test_inline_select_deselect_double_click_is_kept(qapp):
    """썸네일의 더블클릭 = **선택 해제**다.  확대가 아니므로 그대로 둔다."""
    from aoi_verification.app.ui.widgets.thumb_grid import _ThumbTile, ThumbEntry
    assert _defines_own_double_click(_ThumbTile)
    tile = _ThumbTile(ThumbEntry(item=_item()), inline_select=True)
    try:
        tile.set_inline_selected(True)
        seen: list[bool] = []
        tile.sel_toggled.connect(lambda _e, sel: seen.append(sel))
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(4, 4),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        tile.mouseDoubleClickEvent(ev)
        assert tile.is_inline_selected() is False and seen[-1] is False
    finally:
        tile.deleteLater()


def test_slot_mapping_double_click_is_kept():
    """묶은 쌍을 더블클릭해 푸는 동작도 확대가 아니다 — 유지."""
    import inspect
    from aoi_verification.app.ui.widgets import slot_mapping_dialog as smd
    assert "itemDoubleClicked" in inspect.getsource(smd)


# ── 사용자에게 더블클릭을 안내하지 않는다 ───────────────────────────────────
def test_no_user_facing_text_tells_you_to_double_click_for_zoom():
    from aoi_verification.app import i18n
    hint = i18n.KO.UNMATCHED_REVIEW_HINT
    assert "더블클릭" not in hint, f"안내 문구가 없는 제스처를 가르친다: {hint}"
    assert "우클릭" in hint
