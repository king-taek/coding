"""Scan 범위 박스: 굵은 노란 선으로 그려지고, 버튼으로 끄고 켤 수 있다."""
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QImage

from aoi_verification.app import i18n
from aoi_verification.app.ui import theme
from aoi_verification.app.ui.widgets.zoom_window import FullscreenViewer
from pathlib import Path


def _viewer(styled_qapp, overlay):
    img = QImage(400, 300, QImage.Format.Format_Grayscale8)
    img.fill(QColor(120, 120, 120))
    v = FullscreenViewer(Path("scan.jpg"), image=img, overlay=overlay)
    v.resize(800, 600)
    v.show()
    v._redraw()
    return v


def _count_box_pixels(v):
    im = v._label.pixmap().toImage()
    want = QColor(theme.VIEWER_BOX).rgb()
    return sum(1 for x in range(im.width()) for y in range(im.height())
               if im.pixelColor(x, y).rgb() == want)


def test_box_drawn_and_toggled(styled_qapp):
    v = _viewer(styled_qapp, (100, 100, 100, 100))
    assert v._btn_box.text() == i18n.KO.VIEWER_BOX_OFF
    on = _count_box_pixels(v)
    assert on > 0
    v._btn_box.click()
    assert v._btn_box.text() == i18n.KO.VIEWER_BOX_ON
    assert _count_box_pixels(v) == 0
    v._btn_box.click()
    assert _count_box_pixels(v) == on


def test_no_button_without_overlay(styled_qapp):
    v = _viewer(styled_qapp, None)
    assert v._btn_box is None
