"""Wafer map(폴더 선택 모드) 끌어놓기·붙여넣기 — 폴더 1개는 폴더 맵, 사진은 그 사진들만."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("numpy")

from pathlib import Path                                    # noqa: E402

from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl       # noqa: E402
from PyQt6.QtGui import QDragEnterEvent                     # noqa: E402

from aoi_verification.app import i18n                       # noqa: E402
from aoi_verification.app.ui.widgets.wafer_map_dialog import WaferMapDialog  # noqa: E402


@pytest.fixture
def dlg(qapp, monkeypatch):
    d = WaferMapDialog()
    calls = {"folder": [], "images": [], "notice": []}
    monkeypatch.setattr(d, "show_folder", lambda p: calls["folder"].append(p))
    monkeypatch.setattr(d, "show_images", lambda p: calls["images"].append(p))
    monkeypatch.setattr(d, "_notice", calls["notice"].append)
    d.calls = calls
    yield d
    d.deleteLater()


def test_drag_enter_accepts_local_paths_without_touching_disk(dlg, tmp_path, monkeypatch):
    def boom(*_a):
        raise AssertionError("dragEnter 가 디스크를 만졌다")
    monkeypatch.setattr(os.path, "isdir", boom)
    monkeypatch.setattr(os.path, "exists", boom)
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(tmp_path / "SomeFolder"))])
    ev = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.DropAction.CopyAction, md,
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    ev.ignore()
    dlg.dragEnterEvent(ev)
    assert ev.isAccepted()


def test_one_folder_opens_folder_map(dlg, tmp_path):
    f = tmp_path / "W1"
    f.mkdir()
    dlg._on_paths([str(f)])
    assert dlg.calls["folder"] == [Path(str(f))]


def test_images_win_and_missing_photo_is_dropped(dlg, tmp_path):
    a = tmp_path / "a.jpeg"
    a.write_bytes(b"x")
    dlg._on_paths([str(a), str(tmp_path / "gone.jpeg")])
    assert dlg.calls["images"] == [[a]]


def test_notices(dlg, tmp_path):
    f1, f2 = tmp_path / "A", tmp_path / "B"
    f1.mkdir()
    f2.mkdir()
    txt = tmp_path / "a.txt"
    txt.write_text("x", encoding="utf-8")
    dlg._on_paths([str(f1), str(f2)])
    dlg._on_paths([str(tmp_path / "nope")])
    dlg._on_paths([str(txt)])
    dlg._on_no_path(True)
    dlg._on_no_path(False)
    assert dlg.calls["notice"] == [
        i18n.KO.DROP_ONE_AT_A_TIME, i18n.KO.DROP_NOT_FOUND,
        i18n.KO.DROP_WRONG_KIND_FOLDER, i18n.KO.DROP_NO_PATH_IMAGE,
        i18n.KO.DROP_NOTHING_TO_PASTE]
    assert not dlg.calls["folder"] and not dlg.calls["images"]
