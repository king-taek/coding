"""사진 마우스 규칙 한 벌 — 현택님 2026-11-24 결정(D1b·D1c·D1d·D1e).

- **우클릭**: 사진이 있는 모든 화면이 같은 메뉴 3개 — [크게 보기] / [Defect 정보 조회] / [GL 히스토그램].
  화면마다 메뉴를 만들지 않고 ``widgets/photo_menu.ask`` 하나를 부른다.
- **더블클릭**: 어느 사진이든 크게 본다(예전 '타일 더블클릭 없음' 규칙을 현택님 2026-11-24 결정으로 바꿈 —
  이 파일은 그 옛 규칙을 지키던 ``test_no_double_click_zoom.py`` 를 대신한다).  한 번 누르기가 선택 ·
  교체인 타일은 첫 누름이 먼저 고른 뒤 크게 본다(그래도 된다는 결정).
- **Defect Map 은 그대로**: 점 더블클릭 = Defect 정보.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, QPointF, Qt                  # noqa: E402
from PyQt6.QtGui import QColor, QMouseEvent, QPixmap          # noqa: E402

from aoi_verification.app import i18n                         # noqa: E402
from aoi_verification.app.models.slot import ImageItem        # noqa: E402

_APP = Path(__file__).resolve().parents[2] / "aoi_verification" / "app"


def _item(path="/tmp/a.jpg", side="val"):
    return ImageItem(slot="A1", path=Path(path), side=side)


def _dbl() -> QMouseEvent:
    return QMouseEvent(QMouseEvent.Type.MouseButtonDblClick, QPointF(4, 4),
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


@pytest.fixture
def menu_calls(monkeypatch):
    """``photo_menu.ask`` 를 가로챈다 — 띄우지 않고 [크게 보기] 를 고른 것으로 하고 (경로) 를 적는다."""
    from aoi_verification.app.ui.widgets import photo_menu
    calls: list = []

    def fake_ask(owner, gpos, path=None, extras=()):
        calls.append(None if path is None else Path(path))
        return True

    monkeypatch.setattr(photo_menu, "ask", fake_ask)
    return calls


@pytest.fixture
def opened_large(monkeypatch):
    from aoi_verification.app.ui.widgets import photo_menu
    seen: list = []
    monkeypatch.setattr(photo_menu, "open_large", lambda owner, path: seen.append(Path(path)))
    return seen


# ── 메뉴 한 벌 ──────────────────────────────────────────────────────────────
def test_menu_has_the_three_items_in_order(styled_qapp):
    from PyQt6.QtWidgets import QWidget
    from aoi_verification.app.ui.widgets import photo_menu
    w = QWidget()
    menu, acts = photo_menu.build(w, Path("/tmp/a.jpg"))
    assert [a.text() for a in menu.actions()] == ["크게 보기", "Defect 정보 조회", "GL 히스토그램"]
    assert all(a.isEnabled() for a in menu.actions())
    # 경로를 모르는 그림(Scan Crop)은 두 도구가 꺼진다 — 크게 보기는 남는다.
    menu2, acts2 = photo_menu.build(w, None)
    assert acts2["view"].isEnabled() and not acts2["info"].isEnabled() and not acts2["gl"].isEnabled()
    # 화면에만 있는 항목은 구분선 뒤
    menu3, _ = photo_menu.build(w, Path("/tmp/a.jpg"), extras=[("X", lambda: None)])
    assert menu3.actions()[3].isSeparator() and menu3.actions()[4].text() == "X"
    w.deleteLater()


def test_menu_opens_the_tools_with_that_photo(styled_qapp, monkeypatch):
    from PyQt6.QtWidgets import QMenu, QWidget
    from aoi_verification.app.ui.widgets import photo_menu
    from aoi_verification.app.ui.widgets import gl_viewer_dialog as G
    from aoi_verification.app.ui.widgets import image_info_dialog as I
    ran: list = []
    monkeypatch.setattr(photo_menu.sheets, "run", lambda dlg, **k: ran.append(type(dlg).__name__))
    monkeypatch.setattr(I.ImageInfoDialog, "show_image", lambda self, p: ran.append(("info", p)))
    monkeypatch.setattr(G.GlViewerDialog, "open_path", lambda self, p, **k: ran.append(("gl", p)))
    w = QWidget()
    pick = {"i": 1}
    monkeypatch.setattr(QMenu, "exec", lambda self, *a: self.actions()[pick["i"]])
    assert photo_menu.ask(w, QPoint(0, 0), Path("/tmp/a.jpg")) is False
    pick["i"] = 2
    assert photo_menu.ask(w, QPoint(0, 0), Path("/tmp/a.jpg")) is False
    assert ran == [("info", Path("/tmp/a.jpg")), "ImageInfoDialog", ("gl", Path("/tmp/a.jpg")), "GlViewerDialog"]
    pick["i"] = 0
    assert photo_menu.ask(w, QPoint(0, 0), Path("/tmp/a.jpg")) is True        # 크게 보기는 부른 쪽이 연다
    w.deleteLater()


def test_no_screen_builds_its_own_view_larger_menu():
    """[크게 보기] 메뉴 항목을 만드는 곳은 ``photo_menu`` 하나뿐이다."""
    hits = []
    for f in (_APP / "ui").rglob("*.py"):
        if f.name == "photo_menu.py":
            continue
        if "CTX_VIEW_LARGER" in f.read_text(encoding="utf-8"):
            hits.append(f.name)
    assert not hits, f"자기 메뉴를 만드는 화면: {hits}"


_PHOTO_SCREENS = [
    "pages/match_review_page.py", "pages/select_page.py", "widgets/zoom_window.py",
    "widgets/unmatched_review_dialog.py", "widgets/threshold_preview_dialog.py",
    "widgets/overkill_pick_dialog.py", "widgets/zone_origin_dialog.py", "widgets/bulk_select_dialog.py",
    "widgets/thumb_grid.py", "widgets/scalable_image.py", "widgets/coord_calibration_dialog.py",
    "widgets/image_info_dialog.py",
]


@pytest.mark.parametrize("rel", _PHOTO_SCREENS)
def test_every_photo_screen_uses_the_one_menu(rel):
    src = (_APP / "ui" / rel).read_text(encoding="utf-8")
    assert "photo_menu.ask(" in src, rel
    assert not re.search(r"\bQMenu\(", src), f"{rel} 가 자기 QMenu 를 만든다"


# ── 각 사진: 우클릭 = 같은 메뉴(그 사진 경로) · 더블클릭 = 크게 ─────────────────────────
def test_review_main_photo(styled_qapp, tmp_path, menu_calls):
    from aoi_verification.app.ui.pages.match_review_page import _LazyThumb
    p = tmp_path / "a.jpg"
    views = []
    t = _LazyThumb(p, size=80, on_view=lambda: views.append(1))
    t.customContextMenuRequested.emit(QPoint(3, 3))
    assert menu_calls == [p] and views == [1]
    t.mouseDoubleClickEvent(_dbl())
    assert views == [1, 1]
    t.deleteLater()


def test_review_runner_up_tile(styled_qapp, menu_calls):
    from aoi_verification.app.ui.pages.match_review_page import _RunnerUpTile
    it = _item()
    tile = _RunnerUpTile(it, 0.5)
    views = []
    tile.view_requested.connect(views.append)
    tile.customContextMenuRequested.emit(QPoint(3, 3))
    assert menu_calls == [it.path] and views == [it]
    tile.mouseDoubleClickEvent(_dbl())
    assert views == [it, it]
    tile.deleteLater()


def test_zoom_window_tile(styled_qapp, menu_calls):
    from aoi_verification.app.ui.widgets.zoom_window import _MidTile
    it = _item()
    tile = _MidTile(it)
    views = []
    tile.view_requested.connect(views.append)
    tile.customContextMenuRequested.emit(QPoint(3, 3))
    tile.set_selected(True)
    tile.mouseDoubleClickEvent(_dbl())
    assert menu_calls == [it.path] and views == [it, it]
    assert tile._selected is True                     # 더블클릭이 선택을 되돌리지 않는다
    tile.deleteLater()


def test_unmatched_candidate_tile(styled_qapp, menu_calls):
    from aoi_verification.app.ui.widgets.unmatched_review_dialog import _CandidateTile
    it = _item()
    tile = _CandidateTile(it, 0.5)
    views = []
    tile.view_requested.connect(views.append)
    tile.customContextMenuRequested.emit(QPoint(3, 3))
    tile.mouseDoubleClickEvent(_dbl())
    assert menu_calls == [it.path] and views == [it, it]
    tile.deleteLater()


def test_unmatched_ref_photo_double_click_and_menu_are_wired():
    from aoi_verification.app.ui.widgets import unmatched_review_dialog as urd
    assert "mouseDoubleClickEvent" in urd._RefPhoto.__dict__
    src = inspect.getsource(urd.UnmatchedReviewDialog)
    assert "self.ref_img.double_clicked.connect" in src and "_on_ref_context_menu" in src
    assert "self.ref_img.mouseDoubleClickEvent" not in src     # 인스턴스 몽키패치는 여전히 쓰지 않는다


def test_bulk_select_tile(styled_qapp, tmp_path, menu_calls):
    from aoi_verification.app.ui.widgets.bulk_select_dialog import _SelectTile
    it = _item(tmp_path / "b.jpg")
    tile = _SelectTile(it)
    zooms = []
    tile.zoom_requested.connect(zooms.append)
    right = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(4, 4), QPointF(40, 40),
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
                        Qt.KeyboardModifier.NoModifier)
    tile.mousePressEvent(right)
    assert menu_calls == [it.path] and zooms == [it]
    tile.mouseDoubleClickEvent(_dbl())
    assert zooms == [it, it]
    tile.deleteLater()


def test_thumb_grid_tile(styled_qapp, menu_calls, opened_large):
    from aoi_verification.app.ui.widgets.thumb_grid import ThumbEntry, _ThumbTile
    it = _item()
    tile = _ThumbTile(ThumbEntry(item=it), inline_select=True)
    tile.customContextMenuRequested.emit(QPoint(3, 3))
    tile.mouseDoubleClickEvent(_dbl())
    assert menu_calls == [it.path] and opened_large == [it.path, it.path]
    tile.deleteLater()


def test_scalable_image_when_enabled(styled_qapp, tmp_path, menu_calls, opened_large):
    from aoi_verification.app.ui.widgets.scalable_image import ScalableImage
    p = tmp_path / "c.jpg"
    pm = QPixmap(20, 20)
    pm.fill(QColor("#888"))
    pm.save(str(p))
    img = ScalableImage()
    img.set_image(p)
    img.mouseDoubleClickEvent(_dbl())
    assert opened_large == []                         # 켜지 않은 자리(후보 선별 가운데)는 그 화면이 처리한다
    img.enable_photo_mouse()
    img.customContextMenuRequested.emit(QPoint(3, 3))
    img.mouseDoubleClickEvent(_dbl())
    assert menu_calls == [p] and opened_large == [p, p]
    img.deleteLater()


def test_match_page_photos_turn_it_on():
    from aoi_verification.app.ui.pages import match_page
    from aoi_verification.app.ui.widgets import match_expand_view
    assert "self.center_img.enable_photo_mouse()" in inspect.getsource(match_page)
    assert "self._img.enable_photo_mouse()" in inspect.getsource(match_expand_view)


def test_coord_calibration_tile(styled_qapp, tmp_path, menu_calls, opened_large):
    from aoi_verification.app.ui.widgets.coord_calibration_dialog import _PhotoTile
    p = tmp_path / "d.jpg"
    tile = _PhotoTile("k", p, 80)
    picked = []
    tile.clicked.connect(picked.append)
    tile.customContextMenuRequested.emit(QPoint(3, 3))
    tile.mouseDoubleClickEvent(_dbl())
    assert menu_calls == [p] and opened_large == [p, p] and picked == []
    tile.deleteLater()


def test_overkill_and_zone_tile(styled_qapp, tmp_path, menu_calls):
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import _Tile
    p = tmp_path / "e.jpg"
    tile = _Tile(p, 80)
    hits = []
    tile.enlarge.connect(lambda: hits.append(1))
    tile.customContextMenuRequested.emit(QPoint(3, 3))
    tile.mouseDoubleClickEvent(_dbl())
    assert menu_calls == [p] and hits == [1, 1]
    tile.deleteLater()


def test_overkill_pick_tile(styled_qapp):
    from aoi_verification.app.ui.widgets import overkill_pick_dialog as O
    assert "mouseDoubleClickEvent" in O._Tile.__dict__
    assert "t.double_clicked.connect(self._enlarge)" in inspect.getsource(O.OverkillPickDialog)


def test_zone_detail_photo_menu(styled_qapp, tmp_path, menu_calls):
    from aoi_verification.app.ui.widgets.zone_origin_dialog import _Pic
    p = tmp_path / "f.jpg"
    pic = _Pic(path_of=lambda: p)
    hits = []
    pic.clicked.connect(lambda: hits.append(1))
    pic.setText("…")
    pic.customContextMenuRequested.emit(QPoint(3, 3))
    assert menu_calls == []                           # 자리 글뿐이면 메뉴가 없다
    pm = QPixmap(20, 20)
    pm.fill(QColor("#888"))
    pic.setPixmap(pm)
    pic.customContextMenuRequested.emit(QPoint(3, 3))
    assert menu_calls == [p] and hits == [1]
    pic.deleteLater()


def test_defect_info_photo_menu(styled_qapp, tmp_path, menu_calls):
    from aoi_verification.app.ui.widgets.image_info_dialog import _ClickLabel
    p = tmp_path / "g.jpg"
    lab = _ClickLabel(path_of=lambda: p)
    hits = []
    lab.clicked.connect(lambda: hits.append(1))
    pm = QPixmap(20, 20)
    pm.fill(QColor("#888"))
    lab.setPixmap(pm)
    lab.customContextMenuRequested.emit(QPoint(3, 3))
    assert menu_calls == [p] and hits == [1]
    lab.deleteLater()


def test_select_page_center_photo_menu_and_double_click(styled_qapp, tmp_path, monkeypatch, menu_calls):
    from aoi_verification.app.ui.pages import select_page as S
    shown = []
    monkeypatch.setattr(S.sheets, "run", lambda w, **kw: shown.append(w) or 0)
    page = S.SelectPage()
    try:
        p = tmp_path / "S1_000.jpg"
        pm = QPixmap(40, 40)
        pm.fill(QColor("#888"))
        pm.save(str(p))
        page.load_state([ImageItem(slot="S1", path=p, side="ref")])
        page.show()
        styled_qapp.processEvents()
        page.center_img.customContextMenuRequested.emit(QPoint(3, 3))
        assert menu_calls == [p] and len(shown) == 1
        from PyQt6.QtWidgets import QApplication
        QApplication.sendEvent(page.center_img, _dbl())
        assert len(shown) == 2
    finally:
        page.deleteLater()


# ── Defect Map 은 그대로 — 점 더블클릭 = Defect 정보 ───────────────────────────
def test_defect_map_keeps_its_own_double_click():
    from aoi_verification.app.ui.widgets import wafer_map_dialog, wafer_map_view
    src = inspect.getsource(wafer_map_view)
    assert "photo_menu" not in src
    assert "self.point_activated.emit(hit.path)" in src
    dsrc = inspect.getsource(wafer_map_dialog.WaferMapDialog._on_point)
    assert "ImageInfoDialog" in dsrc


# ── 확대가 아닌 더블클릭은 건드리지 않는다 ──────────────────────────────────
def test_slot_mapping_double_click_is_kept():
    """묶은 쌍을 더블클릭해 푸는 동작은 사진이 아니다 — 유지."""
    from aoi_verification.app.ui.widgets import slot_mapping_dialog as smd
    assert "itemDoubleClicked" in inspect.getsource(smd)


def test_no_user_facing_text_tells_you_to_double_click_to_deselect():
    texts = {k: v for k, v in vars(i18n.KO).items() if isinstance(v, str)}
    assert not {k: v for k, v in texts.items() if "더블클릭" in v and "해제" in v}
