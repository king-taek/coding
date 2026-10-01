"""Defect 추출 모드 — 사진·폴더를 끌어다 놓으면 LOT 입력란에 경로가 들어간다(사용자 요청).

계약:
- 폴더는 그 폴더 그대로, 사진은 그 사진이 든 폴더.  그 밖의 파일은 버린다.
- 빈 줄부터 채우고 모자라면 줄을 늘린다.  이미 있는 폴더는 건너뛴다.
- 최대 줄 수를 넘는 것은 넣지 않고 알린다.
- 매칭 모드에서는 받지 않는다(기존 동작 그대로).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl          # noqa: E402
from PyQt6.QtGui import QDropEvent                            # noqa: E402

from aoi_verification.app import i18n                         # noqa: E402
from aoi_verification.app.ui.pages import setup_page as sp    # noqa: E402


def _tree(tmp_path):
    lot = tmp_path / "LOT1"
    w1, w2 = lot / "W1", lot / "W2"
    for d in (w1, w2):
        d.mkdir(parents=True)
    for p in (w1 / "a.jpg", w1 / "b.jpeg", w2 / "c.jpg"):
        p.write_bytes(b"")
    (w1 / "note.txt").write_text("x")
    return lot, w1, w2


def _folders(paths):
    from aoi_verification.app.config import CONFIG
    return sp.extract_drop_folders([str(p) for p in paths],
                                   is_dir=lambda p: p.is_dir(), is_image=CONFIG.is_image)


def test_folder_as_is_photo_to_its_folder_others_dropped(tmp_path):
    lot, w1, w2 = _tree(tmp_path)
    got = _folders([w1 / "a.jpg", w1 / "b.jpeg", lot, w1 / "note.txt", w2 / "c.jpg"])
    assert got == [str(w1), str(lot), str(w2)]        # 같은 폴더 사진 둘은 한 번


def _drop(page, paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    ev = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    page.dropEvent(ev)
    return ev


def test_drop_fills_empty_rows_then_adds_rows(qapp, tmp_path):
    lot, w1, w2 = _tree(tmp_path)
    page = sp.SetupPage()
    try:
        page.set_extract_mode(True)
        page.ref_path_edit.setText("")
        assert page.acceptDrops() and not page.ref_path_edit.acceptDrops()
        _drop(page, [w1 / "a.jpg", w2])
        assert page._lot_paths() == [str(w1), str(w2)]
        assert page.ref_path_edit.text() == str(w1)
        assert not page._lot_rows[1]["edit"].acceptDrops()
        _drop(page, [w1 / "b.jpeg", lot])               # w1 은 이미 있다
        assert page._lot_paths() == [str(w1), str(w2), str(lot)]
        page.set_extract_mode(False)
        assert not page.acceptDrops() and page.ref_path_edit.acceptDrops()
    finally:
        page.deleteLater()


def test_matching_mode_ignores_drops(qapp, tmp_path):
    _lot, w1, _w2 = _tree(tmp_path)
    page = sp.SetupPage()
    try:
        page.ref_path_edit.setText("")
        _drop(page, [w1])
        assert page.ref_path_edit.text() == ""
    finally:
        page.deleteLater()


def test_too_many_folders_are_reported(qapp, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(sp.sheets, "info", lambda _p, _t, msg: shown.append(msg))
    dirs = []
    for k in range(sp.SetupPage.MAX_LOTS + 2):
        d = tmp_path / f"W{k:02d}"
        d.mkdir()
        dirs.append(d)
    page = sp.SetupPage()
    try:
        page.set_extract_mode(True)
        page.ref_path_edit.setText("")
        assert page.add_extract_folders([str(d) for d in dirs]) == sp.SetupPage.MAX_LOTS
        assert len(page._lot_paths()) == sp.SetupPage.MAX_LOTS
        assert shown == [i18n.KO.EXTRACT_DROP_TOO_MANY_FMT.format(
            max=sp.SetupPage.MAX_LOTS, n=2)]
    finally:
        page.deleteLater()
