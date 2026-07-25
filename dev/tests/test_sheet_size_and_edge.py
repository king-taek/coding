"""시트가 **내용만큼 열리고**, 뒷화면과 **경계가 보인다**.

두 가지 회귀:
1. '일부 슬롯만' 창이 세로로 짧아 슬롯 리스트가 거의 안 보였다.  ``_place`` 는
   root(=`_SheetFrame`)의 ``sizeHint`` 만 보는데, 프레임이 내용 위젯의 요구 높이를
   반영하지 못했다(실측: 내용 624px 요구 → 프레임 392px 보고).
2. 시트 면($panel)이 뒷화면($bg)과 대비 1.09:1 이라 '팝업이 어디서 시작하는지'가
   안 보였다.  면을 $elev 로 올리고 테두리를 2px 로 세운다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import (QApplication, QLabel, QMainWindow,  # noqa: E402
                             QVBoxLayout, QWidget)

from aoi_verification.app.ui import theme                        # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sh     # noqa: E402
from aoi_verification.app.ui.widgets.slot_select_dialog import (  # noqa: E402
    SlotSelectDialog)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply_to_app(app)
    return app


@pytest.fixture
def win(qapp):
    w = QMainWindow()
    w.resize(1280, 800)
    w.setCentralWidget(QWidget(w))
    w.show()
    qapp.processEvents()
    yield w
    w.close()


def _relative_luminance(hexv: str) -> float:
    hexv = hexv.lstrip("#")
    parts = [int(hexv[i:i + 2], 16) for i in (0, 2, 4)]

    def lin(v: int) -> float:
        x = v / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(p) for p in parts)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# 1) 시트가 내용이 원하는 높이로 열린다
# ---------------------------------------------------------------------------
def test_frame_hint_carries_content_height(qapp, win):
    """프레임의 sizeHint 가 내용의 요구 높이를 담는다(제목줄만큼만 더해서)."""
    content = QWidget()
    lay = QVBoxLayout(content)
    lay.addWidget(QLabel("본문"))
    content.setMinimumHeight(500)

    frame = sh._SheetFrame(win, "제목")
    frame.set_content(content)
    frame.show()
    qapp.processEvents()
    assert frame.sizeHint().height() >= content.sizeHint().height()


def test_slot_dialog_sheet_is_tall_enough(qapp, win):
    """슬롯 선택 창이 요구한 높이대로 배치된다 — 392px 로 쪼그라들지 않는다."""
    names = [f"슬롯{i:02d}" for i in range(30)]
    dlg = SlotSelectDialog(names, preselected=set(names), parent=win)
    host = sh.SheetHost(win)
    host.resize(1280, 800)
    frame = sh._SheetFrame(host, dlg.windowTitle())
    frame.set_content(dlg)
    frame.show()
    qapp.processEvents()
    host._place(frame, False)
    qapp.processEvents()

    want = dlg.sizeHint().height()
    assert want > 500, "다이얼로그가 애초에 큰 높이를 요구해야 한다"
    # 가용(800-48=752) 안에서 요구 높이를 거의 그대로 받는다.
    assert frame.height() >= want, f"시트가 내용보다 짧다: {frame.height()} < {want}"


# ---------------------------------------------------------------------------
# 2) 시트 경계가 보인다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_sheet_surface_differs_from_page(qapp, mode):
    """시트 면이 뒷화면 면보다 한 단 위 — 같은 색이면 경계가 사라진다."""
    theme.set_color_mode(mode)
    try:
        assert theme.ELEV != theme.BG
        # 면만으로는 미미하므로 테두리가 실제 경계를 만든다(아래 테스트).
        assert _contrast(theme.BG, theme.ELEV) > _contrast(theme.BG, theme.PANEL)
    finally:
        theme.set_color_mode("light")


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_sheet_border_is_visible(qapp, mode):
    """시트 테두리가 뒷화면과 3:1 이상으로 구분되고, 굵기가 2px 이다."""
    theme.set_color_mode(mode)
    theme.apply_to_app(qapp)
    try:
        assert _contrast(theme.BG, theme.LINE_STRONG) >= 3.0
        qss = qapp.styleSheet()
        i = qss.find('QDialog[role="sheet"]')
        assert i >= 0
        block = qss[i:i + 220]
        assert "border: 2px solid" in block, block
        assert f"background-color: {theme.ELEV};" in block, block
    finally:
        theme.set_color_mode("light")
        theme.apply_to_app(qapp)
