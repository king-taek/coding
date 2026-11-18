"""끌어놓기 표시(`ui/drop_overlay`) — 진짜 drag 이벤트를 보내 표시 · 글자 · 받음/거절을 본다.

없는 경로만 쓴다: dragEnter/dragMove 는 디스크를 만지면 안 된다(NAS)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl          # noqa: E402
from PyQt6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication                      # noqa: E402

from aoi_verification.app import i18n                         # noqa: E402

K = i18n.KO


def _mime(paths):
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return m


def _send(widget, kind, paths, pos=QPointF(5, 5)):
    mime = _mime(paths)
    if kind == "move" and not getattr(widget, "_t_entered", False):
        widget._t_entered = True            # 실제 OS 순서: enter 가 먼저 온다
        _send(widget, "enter", paths, pos)
    if kind == "leave":
        widget._t_entered = False
        ev = QDragLeaveEvent()
    else:
        cls = QDragEnterEvent if kind == "enter" else QDragMoveEvent
        ev = cls(pos.toPoint(), Qt.DropAction.CopyAction, mime,
                 Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)
    return ev, mime


def _ov(widget):
    return widget._drop_overlay


@pytest.fixture
def page(styled_qapp):
    from aoi_verification.app.ui.pages import setup_page as sp
    p = sp.SetupPage()
    p.resize(1400, 900)
    p.show()
    QApplication.processEvents()
    yield p
    p.deleteLater()


def _center(page, w):
    return QPointF(page.mapFromGlobal(w.mapToGlobal(w.rect().center())))


def test_setup_hover_val_card_says_val_folder(page):
    ev, _m = _send(page, "move", ["/nope/LOT_A"], _center(page, page.val_group))
    ov = _ov(page)
    assert ov.isVisible() and ov.text == K.DROP_PILL_VAL == "검증 폴더로" and ov.ok
    assert ev.isAccepted()
    _send(page, "move", ["/nope/LOT_A"], _center(page, page.ref_group))
    assert ov.text == K.DROP_PILL_REF
    _send(page, "leave", [])
    assert not ov.isVisible()


def test_setup_xlsx_labels_ref_card(page):
    _send(page, "enter", ["/nope/r.xlsx"], _center(page, page.val_group))
    ov = _ov(page)
    assert ov.text == K.DROP_PILL_EXCEL_REF and ov.ok and ov._target is page.ref_group


def test_setup_other_only_rejects_but_enter_is_accepted(page):
    ev, _m = _send(page, "enter", ["/nope/a.txt"], _center(page, page.val_group))
    ov = _ov(page)
    assert not ov.ok and ov.text == K.DROP_PILL_ONLY_FOLDER_PHOTO
    assert ev.isAccepted()                  # enter 는 받아야 dragLeave 가 온다
    ev, _m = _send(page, "move", ["/nope/a.txt"], _center(page, page.val_group))
    assert not ev.isAccepted()              # move 에서 거절 → 금지 커서
    _send(page, "leave", [])
    assert not ov.isVisible()


def test_setup_empty_target_goes_to_first_empty_card(page):
    page.ref_path_edit.setText("/nope/ref")
    _send(page, "move", ["/nope/LOT_A"], QPointF(1, 1))       # 카드 밖
    ov = _ov(page)
    assert ov.text == K.DROP_PILL_VAL and ov._target is page.val_group
    page.val_path_edit.setText("/nope/val")
    _send(page, "move", ["/nope/LOT_A"], QPointF(1, 1))
    assert not ov.ok and ov.text == K.DROP_PILL_SETUP_FULL


def test_setup_extract_label(page):
    page.set_extract_mode(True)
    QApplication.processEvents()
    _send(page, "enter", ["/nope/LOT_A"])
    ov = _ov(page)
    assert ov.text == K.DROP_PILL_ADD_FOLDER and ov._target is page.ref_group and ov.ok


def test_image_info_rejects_folder_accepts_image(styled_qapp):
    from aoi_verification.app.ui.widgets.image_info_dialog import ImageInfoDialog
    d = ImageInfoDialog()
    d.resize(900, 600)
    d.show()
    QApplication.processEvents()
    ev, _m = _send(d, "move", ["/nope/LOT_A"])
    ov = _ov(d)
    assert ov.isVisible() and not ov.ok and ov.text == K.DROP_PILL_ONLY_IMAGE
    assert not ev.isAccepted()
    ev, _m = _send(d, "move", ["/nope/a.jpg"])
    assert ov.ok and ov.text == K.DROP_PILL_OPEN_IMAGE and ev.isAccepted()
    _send(d, "leave", [])
    assert not ov.isVisible()
    d.close()


def test_gl_accepts_jpg(styled_qapp):
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    d = GlViewerDialog()
    d.resize(900, 600)
    d.show()
    QApplication.processEvents()
    ev, _m = _send(d, "enter", ["/nope/a.jpg"])
    ov = _ov(d)
    assert ov.isVisible() and ov.ok and ov.text == K.DROP_PILL_OPEN_IMAGE and ev.isAccepted()
    _send(d, "enter", ["/nope/a.txt"])
    assert not ov.ok and ov.text == K.DROP_PILL_ONLY_IMAGE
    _send(d, "leave", [])
    assert not ov.isVisible()
    d.close()


def test_card_border_is_outside_card_and_overlay_follows_geometry(page):
    _send(page, "move", ["/nope/LOT_A"], _center(page, page.val_group))
    ov = _ov(page)
    card = page.val_group.rect().translated(page.val_group.mapTo(page, page.rect().topLeft()))
    assert ov.border_rect().top() < card.top() and ov.border_rect().left() < card.left()
    page.resize(1200, 800)                      # 크기가 바뀐 뒤 다시 부르면 따라간다
    QApplication.processEvents()
    _send(page, "move", ["/nope/LOT_A"], _center(page, page.val_group))
    assert ov.geometry() == page.rect()


def test_overlay_hidden_after_drop(page, tmp_path):
    from PyQt6.QtGui import QDropEvent
    _send(page, "move", ["/nope/LOT_A"], _center(page, page.val_group))
    assert _ov(page).isVisible()
    mime = _mime(["/nope/LOT_A"])
    ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    page._drop_notice = lambda *_a: None
    page.dropEvent(ev)
    assert not _ov(page).isVisible()


def test_overkill_accept_and_reject(styled_qapp):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    d = T.ThresholdPreviewDialog()
    d.resize(900, 600)
    d.show()
    QApplication.processEvents()
    ev, _m = _send(d, "move", ["/nope/LOT_A"])
    ov = _ov(d)
    assert ov.ok and ov.text == K.DROP_PILL_ADD_FOLDER and ev.isAccepted()
    ev, _m = _send(d, "move", ["/nope/a.txt"])
    assert not ov.ok and ov.text == K.DROP_PILL_ONLY_FOLDER_PHOTO and not ev.isAccepted()
    d.hide()                                    # 끌던 중 숨겨져도 표시가 남지 않는다
    assert not ov.isVisible()
    d.close()


def test_wafer_map_accept_and_reject(styled_qapp):
    from aoi_verification.app.ui.widgets.wafer_map_dialog import WaferMapDialog
    d = WaferMapDialog()
    d.resize(900, 600)
    d.show()
    QApplication.processEvents()
    _send(d, "enter", ["/nope/a.jpg"])
    assert _ov(d).ok and _ov(d).text == K.DROP_PILL_SHOW_ON_MAP
    _send(d, "enter", ["/nope/LOT_A"])
    assert _ov(d).text == K.DROP_PILL_FOLDER_MAP
    ev, _m = _send(d, "move", ["/nope/a.txt"])
    assert not _ov(d).ok and not ev.isAccepted()
    d.close()
