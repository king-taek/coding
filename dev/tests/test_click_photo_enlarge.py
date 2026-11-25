"""사진을 누르면 크게 (현택님: "사진 클릭하면 크게 보게해줘").

**지금 클릭이 아무 일도 안 하던 사진**에만 붙였다 — 후보 선별 가운데 사진, 매치 검토의
기준·검증 사진, Overkill 고르기의 가운데 큰 사진.  Zone 화면의 큰 사진은 이미 눌러서
크게 볼 수 있었는데 손가락 커서가 없어 알 수 없었다.  클릭이 이미 '고르기/교체' 인
타일(일괄 선택·선별 패널·차순위 후보·Overkill/Zone 타일 격자)은 그대로 둔다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, Qt                                 # noqa: E402
from PyQt6.QtGui import QColor, QPixmap                             # noqa: E402
from PyQt6.QtTest import QTest                                      # noqa: E402

_HAND = Qt.CursorShape.PointingHandCursor


def _pm() -> QPixmap:
    pm = QPixmap(40, 40)
    pm.fill(QColor("#888888"))
    return pm


def test_select_page_center_photo_opens_large(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages import select_page as S
    from aoi_verification.app.ui.widgets.zoom_window import FullscreenViewer
    shown = []
    monkeypatch.setattr(S.sheets, "run", lambda w, **kw: shown.append(w) or 0)
    page = S.SelectPage()
    try:
        p = tmp_path / "S1_000.jpg"
        _pm().save(str(p))
        page.load_state([ImageItem(slot="S1", path=p, side="ref")])
        page.show()
        styled_qapp.processEvents()
        assert page.center_img.cursor().shape() == _HAND
        before = (dict(page._state.targets), dict(page._state.excluded))
        QTest.mouseClick(page.center_img, Qt.MouseButton.LeftButton)
        assert len(shown) == 1 and isinstance(shown[0], FullscreenViewer)
        assert shown[0]._path == p
        # 크게 보기만 한다 — 결정(검증/제외)은 그대로
        assert (dict(page._state.targets), dict(page._state.excluded)) == before
        assert page._current is not None and page._current.path == p
    finally:
        page.deleteLater()


def test_review_ref_and_val_photos_open_large_on_click(styled_qapp, tmp_path):
    from aoi_verification.app.ui.pages.match_review_page import _LazyThumb
    p = tmp_path / "a.jpg"
    _pm().save(str(p))
    opened = []
    t = _LazyThumb(p, size=80, on_view=lambda: opened.append(1))
    assert t.cursor().shape() == _HAND
    QTest.mouseClick(t, Qt.MouseButton.LeftButton)
    assert opened == [1]
    t.deleteLater()


def test_runner_up_click_still_swaps_not_enlarges(styled_qapp, tmp_path):
    """차순위 후보는 클릭 = 교체(원래 동작) — 안의 썸네일이 클릭을 가로채면 안 된다."""
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages.match_review_page import _RunnerUpTile
    p = tmp_path / "b.jpg"
    _pm().save(str(p))
    tile = _RunnerUpTile(ImageItem(slot="S", path=p, side="val"), 0.5)
    swaps, views = [], []
    tile.swap_requested.connect(lambda it, s: swaps.append(it))
    tile.view_requested.connect(views.append)
    tile.show()
    styled_qapp.processEvents()
    QTest.mouseClick(tile._img, Qt.MouseButton.LeftButton)
    assert len(swaps) == 1 and views == []
    tile.deleteLater()


def test_overkill_pick_big_photo_opens_large(styled_qapp, monkeypatch):
    from aoi_verification.app.ui import motion
    from aoi_verification.app.ui.widgets.overkill_pick_dialog import OverkillPickDialog
    monkeypatch.setattr(motion, "enabled", lambda: False)

    class D:
        def __init__(self, n):
            self.zone, self.n, self.zone_dir = "a", n, "/z/a"

    items = [D(i) for i in range(3)]
    enlarged = []
    dlg = OverkillPickDialog(
        zones=[("a", "구역 A")], zone="a", items_of=lambda z: items,
        key_of=lambda d: (d.zone, d.n), pixmap_of=lambda d, s: _pm(),
        request_pics=lambda ds, s: None, caption_of=lambda d: "c",
        picked={}, overkill={}, preview=lambda p, o: (0, 0, 0),
        enlarge=enlarged.append)
    try:
        dlg.resize(1200, 800)
        dlg.show()
        styled_qapp.processEvents()
        assert dlg.big.has_photo() and dlg.big.cursor().shape() == _HAND
        QTest.mouseClick(dlg.big, Qt.MouseButton.LeftButton)
        assert enlarged == [items[0]]
        assert dlg.picked() == {}                       # 크게 보기는 결정이 아니다
        dlg.big.set_photo(None, "…")
        assert dlg.big.cursor().shape() != _HAND
        QTest.mouseClick(dlg.big, Qt.MouseButton.LeftButton)
        assert enlarged == [items[0]]                   # 사진이 없으면 열지 않는다
    finally:
        dlg.deleteLater()


def test_zone_detail_photo_shows_hand_only_with_a_photo(styled_qapp):
    from aoi_verification.app.ui.widgets.zone_origin_dialog import _Pic
    pic = _Pic()
    hits = []
    pic.clicked.connect(lambda: hits.append(1))
    pic.setText("없음")
    assert pic.cursor().shape() != _HAND
    pic.setPixmap(_pm())
    assert pic.cursor().shape() == _HAND
    QTest.mouseClick(pic, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert hits == [1]
    pic.deleteLater()
