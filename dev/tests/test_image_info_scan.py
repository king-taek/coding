"""단일 사진 정보 창의 Scan image 칸 — 헤드리스 검증.

지키는 계약(사용자 결정):
- Scan 이 있을 때만 Color 아래에 칸이 생긴다.  없으면 칸도 문구도 없다.
- 판정되면 자리를 먼저 잡고 '불러오는 중', 그다음 그림.
- 사진을 바꾸면 이전 사진의 늦은 결과가 새 사진 아래에 붙지 않는다(세대 번호).
- 창 크기를 바꿔도 Scan 을 다시 읽지 않는다(메모리의 Crop 만 재스케일).
- 누르면 전체 Scan 을 기존 전체화면 뷰어로, 잘라낸 범위를 덧그려 연다.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("PIL")

from PIL import Image                                         # noqa: E402
from PyQt6.QtGui import QImage                                # noqa: E402
from PyQt6.QtWidgets import QApplication                      # noqa: E402

from aoi_verification.app import i18n                         # noqa: E402
from aoi_verification.app.coords import scan_image            # noqa: E402
from aoi_verification.app.ui.widgets import image_info_dialog as iid  # noqa: E402
from aoi_verification.app.ui.widgets.zoom_window import FullscreenViewer  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh():
    scan_image.clear_caches()
    yield
    scan_image.clear_caches()


def _wafer(tmp_path, name="W", *, manifest=True):
    folder = tmp_path / name
    folder.mkdir()
    color = folder / "1000.2000.c.1.jpeg"
    Image.new("RGB", (138, 104), (60, 90, 60)).save(color)
    Image.new("L", (320, 100), 90).save(folder / "-1.2.t.jpeg")
    (folder / "Params_WaferInfo.ini").write_text(
        "[I]\nRefPixelSizeX=1.0\nRefPixelSizeY=1.0\n")
    if manifest:
        (folder / scan_image.SCAN_LIST_NAME).write_text(
            "Version=1\n-1.2.t.jpeg,1000,2000\n")
    return color


def _wait(pred, ms=5000):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def _idle():
    assert _wait(lambda: not iid._LIVE_SCAN_LOADS)


def test_scan_shown_under_color_when_found(qapp, isolated_cache, tmp_path):
    dlg = iid.ImageInfoDialog(image_path=str(_wafer(tmp_path)))
    dlg.resize(1100, 760)
    dlg.show()
    assert _wait(lambda: dlg._scan_crop is not None)
    assert not dlg.scan_view.isHidden() and not dlg.scan_title.isHidden()
    assert dlg.scan_title.text() == i18n.KO.SCAN_IMAGE_TITLE
    assert not dlg.scan_view.pixmap().isNull()
    assert dlg._scan_box == (10, -100, 300, 300)      # 결함이 Scan 중앙, 범위가 위아래로 넘침
    assert dlg.scan_view.width() <= dlg.preview.width()
    dlg.close()
    _idle()


def test_no_scan_means_no_box_at_all(qapp, isolated_cache, tmp_path):
    dlg = iid.ImageInfoDialog(image_path=str(_wafer(tmp_path, manifest=False)))
    dlg.show()
    _idle()
    assert dlg.scan_view.isHidden() and dlg.scan_title.isHidden()
    dlg.close()


def test_loading_state_reserves_the_box(qapp, isolated_cache):
    dlg = iid.ImageInfoDialog()
    dlg._on_scan_found(dlg._scan_gen)
    assert not dlg.scan_view.isHidden()
    assert dlg.scan_view.text() == i18n.KO.SCAN_IMAGE_LOADING


def test_stale_result_is_ignored(qapp, isolated_cache):
    dlg = iid.ImageInfoDialog()
    dlg._scan_gen = 5
    img = QImage(10, 10, QImage.Format.Format_Grayscale8)
    dlg._on_scan_done(4, scan_image.OK, img, img, (0, 0, 10, 10), "x.t.jpeg")
    dlg._on_scan_found(4)
    assert dlg._scan_crop is None and dlg.scan_view.isHidden()


def test_switching_photo_clears_previous_scan(qapp, isolated_cache, tmp_path):
    with_scan = _wafer(tmp_path, "A")
    without = _wafer(tmp_path, "B", manifest=False)
    dlg = iid.ImageInfoDialog(image_path=str(with_scan))
    dlg.show()
    assert _wait(lambda: dlg._scan_crop is not None)
    dlg.show_image(without)
    assert dlg._scan_crop is None and dlg.scan_view.isHidden()   # 즉시 치운다
    _idle()
    assert dlg.scan_view.isHidden()
    dlg.close()


def test_resize_does_not_reread_scan(qapp, isolated_cache, tmp_path, monkeypatch):
    dlg = iid.ImageInfoDialog(image_path=str(_wafer(tmp_path)))
    dlg.show()
    assert _wait(lambda: dlg._scan_crop is not None)
    _idle()
    calls = []
    monkeypatch.setattr(scan_image, "resolve", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(scan_image, "load_crop", lambda *a, **k: calls.append(a))
    for w in (700, 1300, 900):
        dlg.resize(w, 700)
        QApplication.processEvents()
    assert calls == [] and not dlg.scan_view.pixmap().isNull()
    dlg.close()


def test_closing_before_result_is_safe(qapp, isolated_cache, tmp_path):
    dlg = iid.ImageInfoDialog(image_path=str(_wafer(tmp_path)))
    dlg.close()
    dlg.deleteLater()
    QApplication.processEvents()
    _idle()                       # 늦은 결과가 지워진 위젯에 닿아도 예외 없이 끝난다


def test_unreadable_scan_shows_message(qapp, isolated_cache):
    dlg = iid.ImageInfoDialog()
    dlg._on_scan_done(dlg._scan_gen, scan_image.UNREADABLE, None, None, None, "")
    assert dlg.scan_view.text() == i18n.KO.SCAN_IMAGE_UNREADABLE


def test_click_opens_full_scan_with_overlay(qapp, isolated_cache, tmp_path, monkeypatch):
    dlg = iid.ImageInfoDialog(image_path=str(_wafer(tmp_path)))
    dlg.show()
    assert _wait(lambda: dlg._scan_full is not None)
    opened = []
    monkeypatch.setattr(iid.sheets, "run", lambda w, **k: opened.append(w))
    dlg.scan_view.clicked.emit()
    (viewer,) = opened
    assert isinstance(viewer, FullscreenViewer)
    assert viewer._overlay == dlg._scan_box
    assert viewer._pix.width() == 320 and viewer._loader is None   # 원본 그대로, 재로딩 없음
    viewer.resize(800, 500)
    viewer.show()
    QApplication.processEvents()
    assert not viewer._label.pixmap().isNull()
    viewer.close()
    dlg.close()
    _idle()


def test_pil_to_qimage_keeps_pixels(qapp):
    im = Image.new("L", (3, 2))
    im.putdata([0, 50, 100, 150, 200, 250])
    q = iid.pil_to_qimage(im)
    assert (q.width(), q.height()) == (3, 2)
    assert q.pixelColor(2, 1).red() == 250
