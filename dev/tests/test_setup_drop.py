"""설정 화면(매칭) 끌어다 놓기 · 붙여넣기 — 놓을 줄 판정 · 한 폴더만 · 엑셀 · 안내."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl          # noqa: E402
from PyQt6.QtGui import QDropEvent, QShortcut                 # noqa: E402
from PyQt6.QtWidgets import QApplication                      # noqa: E402

from aoi_verification.app import i18n                         # noqa: E402
from aoi_verification.app.ui.pages import setup_page as sp    # noqa: E402


@pytest.fixture
def page(styled_qapp, monkeypatch):
    p = sp.SetupPage()
    p.resize(1400, 900)
    p.show()
    p.notices = []
    monkeypatch.setattr(p, "_drop_notice", p.notices.append)
    QApplication.processEvents()
    yield p
    p.deleteLater()


def _tree(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    for d in (a, b):
        d.mkdir()
        (d / "x.jpg").write_bytes(b"")
    return a, b


def _mime(paths):
    m = QMimeData()
    m.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return m


def _drop(page, paths, pos=QPointF(1, 1)):
    mime = _mime(paths)                  # QDropEvent 는 mime 을 소유하지 않는다 — 살려 둔다
    ev = QDropEvent(pos, Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    page.dropEvent(ev)
    return ev


def _center(page, w):
    return QPointF(page.mapFromGlobal(w.mapToGlobal(w.rect().center())))


def test_edits_do_not_take_drops(page):
    assert page.acceptDrops()
    assert not page.ref_path_edit.acceptDrops() and not page.val_path_edit.acceptDrops()


def test_row_detection_and_target(page, tmp_path):
    a, _ = _tree(tmp_path)
    ref_hit = page._drop_row_at(_center(page, page.ref_group).toPoint())
    val_hit = page._drop_row_at(_center(page, page.val_group).toPoint())
    assert ref_hit[1] is page.ref_path_edit and val_hit[1] is page.val_path_edit
    _drop(page, [a], _center(page, page.val_group))      # 커서 아래 줄이 우선
    assert page.val_path_edit.text() == str(a) and page.ref_path_edit.text() == ""


def test_extra_val_row_is_target(page, tmp_path):
    a, _ = _tree(tmp_path)
    page._add_val_row()
    QApplication.processEvents()
    rec = page._val_rows[0]
    _drop(page, [a], _center(page, rec["host"]))
    assert rec["edit"].text() == str(a)


def test_no_cursor_row_fills_first_empty_then_full(page, tmp_path):
    a, b = _tree(tmp_path)
    page._apply_match_paths([str(a)], None)
    assert page.ref_path_edit.text() == str(a)
    page._apply_match_paths([str(b / "x.jpg")], None)    # 사진 → 든 폴더, 다음 빈 칸
    assert page.val_path_edit.text() == str(b)
    page._apply_match_paths([str(a)], None)
    assert page.notices == [i18n.KO.DROP_SETUP_FULL]


def test_two_folders_rejected_and_wrong_kind(page, tmp_path):
    a, b = _tree(tmp_path)
    page._apply_match_paths([str(a), str(b)], None)
    (tmp_path / "n.txt").write_text("x")
    page._apply_match_paths([str(tmp_path / "n.txt")], None)
    assert page.notices == [i18n.KO.DROP_ONE_AT_A_TIME, i18n.KO.DROP_WRONG_KIND_FOLDER]
    assert page.ref_path_edit.text() == ""
    (a / "y.jpg").write_bytes(b"")                       # 같은 폴더의 사진 둘은 한 폴더
    page._apply_match_paths([str(a / "x.jpg"), str(a / "y.jpg")], None)
    assert page.ref_path_edit.text() == str(a)


def test_single_xlsx_requests_excel_ref(page, tmp_path):
    got = []
    page.excel_ref_requested.connect(got.append)
    x = tmp_path / "r.xlsx"
    x.write_bytes(b"")
    page._apply_match_paths([str(x)], None)
    assert got == [str(x)] and page.ref_path_edit.text() == ""


def test_drop_resets_slot_selection_like_browse(page, tmp_path, monkeypatch):
    a, _ = _tree(tmp_path)
    calls = []
    monkeypatch.setattr(page, "_reset_slot_selection", lambda: calls.append(1))
    page._apply_match_paths([str(a)], page.ref_path_edit)
    assert calls == [1]


def test_highlight_set_and_cleared(page):
    page._set_drop_hl(page.ref_group)
    assert page.ref_group.property("drop") == "on"
    page._set_drop_hl(page.val_group)
    assert page.ref_group.property("drop") == "" and page.val_group.property("drop") == "on"
    page._set_drop_hl(None)
    assert page.val_group.property("drop") == ""


def test_tool_screen_ignores_and_extract_unchanged(page, tmp_path):
    a, _ = _tree(tmp_path)
    page._page_key = "gl"
    ev = _drop(page, [a])
    assert not ev.isAccepted() and page.ref_path_edit.text() == ""
    page._page_key = "match"
    page.set_extract_mode(True)
    _drop(page, [a])
    assert page._lot_paths() == [str(a)]


def test_paste_paths_and_no_path(page, tmp_path):
    a, _ = _tree(tmp_path)
    QApplication.clipboard().setMimeData(_mime([a]))
    QApplication.focusWidget() and QApplication.focusWidget().clearFocus()   # 입력란이 포커스면 거기에 붙는다
    sc = next(s for s in page._match_host.findChildren(QShortcut))
    sc.activated.emit()
    assert page.ref_path_edit.text() == str(a)
    QApplication.clipboard().setMimeData(QMimeData())
    QApplication.focusWidget() and QApplication.focusWidget().clearFocus()
    sc.activated.emit()
    assert page.notices[-1] == i18n.KO.DROP_NOTHING_TO_PASTE


def test_normalize_quoted_and_file_url(page):
    e = page.ref_path_edit
    e.setText('"/tmp/x y"')
    page._normalize_pasted(e, e.text())
    assert e.text() == "/tmp/x y"
    e.setText("file:///tmp/x%20y")
    page._normalize_pasted(e, e.text())
    assert e.text() == "/tmp/x y"
    e.setText("/tmp/typing ")
    page._normalize_pasted(e, e.text())
    assert e.text() == "/tmp/typing "


def test_missing_photo_path_notice_and_no_browse_target_change(page, tmp_path):
    page._browse_target = page.val_path_edit
    _drop(page, [tmp_path / "nope" / "x.jpg"])
    assert page.notices[-1] == i18n.KO.DROP_NOT_FOUND and page.ref_path_edit.text() == ""
    a, _ = _tree(tmp_path)
    _drop(page, [a])
    assert page.ref_path_edit.text() == str(a) and page._browse_target is page.val_path_edit


def test_extract_nothing_usable_notice(page, tmp_path):
    page.set_extract_mode(True)
    _drop(page, [tmp_path / "nope.txt"])
    assert page.notices[-1] == i18n.KO.DROP_WRONG_KIND_FOLDER


def test_paste_targets_focused_edit(page, tmp_path):
    a, _ = _tree(tmp_path)
    page.val_path_edit.setFocus()
    QApplication.processEvents()
    if QApplication.focusWidget() is not page.val_path_edit:
        pytest.skip("offscreen 포커스 없음")
    page._on_paste_paths([str(a)])
    assert page.val_path_edit.text() == str(a) and page.ref_path_edit.text() == ""
