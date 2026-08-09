"""확대창의 열 수가 **창 폭을 실제로 따라가는지** 못 박는다 (U-09).

배경(실측 사고): 열 수를 3 으로 고정하던 것을 뷰포트 폭에 맞춰 계산하도록 바꿨는데,
그 계산이 **첫 레이아웃에서 1열로 굳었다.**  생성자에서 부르는 재배치는 스크롤
뷰포트가 아직 기본값(98px)이라 1열을 내고, show 때 배달되는 (밀린) 첫 resize 도
레이아웃 활성화 **전**이라 뷰포트가 여전히 낡은 값이다 — 그래서 '열 수가 바뀔 때만
재배치' 가드에 걸려 건너뛴다.  그 뒤로는 resizeEvent 가 오지 않으므로 사용자가 창
크기를 직접 바꾸기 전까지 1열로 남았다.  **고정 3열이던 예전보다 나빠진 것**이다.

이 창에는 열 수를 다루는 테스트가 하나도 없어서 아무도 몰랐다.  그래서 여기 만든다.

⚠ 반드시 **시트로 띄운 상태**(`sheets.run(..., full_bleed=True)`)에서 재야 한다 —
그게 앱이 실제로 여는 방식이고, 위젯을 단독으로 show() 하면 창이 화면 크기에 묶여
문제가 재현되지 않는다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QTimer                                    # noqa: E402
from PyQt6.QtWidgets import QMainWindow, QScrollArea, QWidget      # noqa: E402

from aoi_verification.app.models.slot import ImageItem             # noqa: E402
from aoi_verification.app.ui.widgets import sheet_host as sheets   # noqa: E402
from aoi_verification.app.ui.widgets.sheet_host import SheetHost   # noqa: E402
from aoi_verification.app.ui.widgets import zoom_window as ZW      # noqa: E402


def _columns_at(styled_qapp, tmp_path, w_px: int, h_px: int) -> tuple:
    """창 크기 ``w_px x h_px`` 에서 실제로 그려진 (뷰포트폭, 열 수, 가로스크롤여부)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(9):
        p = tmp_path / f"z{i}.jpg"
        p.write_bytes(b"")
        items.append(ImageItem(slot="S", path=p, side="val"))

    host = QMainWindow()
    host.setCentralWidget(QWidget())
    host._sheets = SheetHost(host)
    host.resize(w_px, h_px)
    host.show()
    for _ in range(8):
        styled_qapp.processEvents()

    win = ZW.ZoomWindow("S", items, ZW.SOURCE_CANDIDATES, parent=host)
    out: dict = {}

    def _probe():
        for _ in range(25):
            styled_qapp.processEvents()
        sc = win.findChild(QScrollArea)
        xs = {t.mapTo(win, t.rect().topLeft()).x() for t in win._tiles}
        out["vp"] = sc.viewport().width()
        out["cols"] = len(xs)
        out["hscroll"] = sc.horizontalScrollBar().maximum() > 0
        win.close()

    QTimer.singleShot(0, _probe)
    sheets.run(win, full_bleed=True)
    host.close()
    for _ in range(5):
        styled_qapp.processEvents()
    return out["vp"], out["cols"], out["hscroll"]


def test_wide_window_uses_all_three_columns(styled_qapp, tmp_path):
    """넓은 창에서 1열로 뜨면 사진 한 장 폭만 쓰고 세로 스크롤이 3배가 된다."""
    vp, cols, _ = _columns_at(styled_qapp, tmp_path, 1280, 800)
    assert cols == 3, f"뷰포트 {vp}px 인데 {cols}열 — 열 계산이 첫 레이아웃에서 굳었다"


def test_very_wide_window_stops_at_three(styled_qapp, tmp_path):
    """상한 3 — 더 넓어져도 타일이 흩어지지 않는다."""
    vp, cols, _ = _columns_at(styled_qapp, tmp_path / "b", 1600, 950)
    assert cols == 3, f"뷰포트 {vp}px 에서 {cols}열"


def test_narrow_window_drops_a_column_instead_of_clipping(styled_qapp, tmp_path):
    """U-09 의 본래 목적 — 좁으면 열을 줄여 가로 스크롤을 만들지 않는다."""
    vp, cols, hscroll = _columns_at(styled_qapp, tmp_path / "c", 1000, 700)
    assert cols == 2, f"뷰포트 {vp}px 인데 {cols}열 (3열이면 세 번째가 잘린다)"
    assert not hscroll, "열을 줄였는데도 가로 스크롤이 생겼다"
