"""왼쪽 메뉴 접힘 이름(V4) · 줄 스타일(V3) · 폴더 칸 오류 모양(V9) · [폴더 선택…] 높이(V10)."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QPushButton  # noqa: E402

from aoi_verification.app import i18n  # noqa: E402
from aoi_verification.app.ui import theme  # noqa: E402
from aoi_verification.app.ui.pages.setup_page import SetupPage  # noqa: E402


def _qss() -> str:
    from pathlib import Path
    src = Path(theme.__file__).with_name("style.qss").read_text(encoding="utf-8")
    return theme.render_qss(src)


def _page(qapp):
    page = SetupPage()
    page.resize(1400, 900)
    page.show()
    for _ in range(8):
        qapp.processEvents()
    return page


def _icon_y(page, b) -> float:
    """아이콘(버튼 세로 가운데)의 화면 y."""
    return b.mapTo(page, QPoint(0, 0)).y() + b.height() / 2


def test_folded_icons_keep_vertical_position_and_names_fit(styled_qapp):
    page = _page(styled_qapp)
    try:
        btns = [*page._nav_buttons.values(), page.update_btn]
        page._set_rail_folded(False, animate=False)
        for _ in range(5):
            styled_qapp.processEvents()
        open_y = [_icon_y(page, b) for b in btns]
        page._set_rail_folded(True, animate=False)
        for _ in range(5):
            styled_qapp.processEvents()
        assert [_icon_y(page, b) for b in btns] == open_y      # 세로 위치 불변
        assert page._tool_rail.width() == 76
        for key, b in zip([*page._nav_buttons, "update"], btns):
            lb = b.property("_railShort")
            assert lb.isVisible() and lb.text() == i18n.KO.RAIL_SHORT_NAMES[key]
            assert lb.sizeHint().width() <= b.width()          # 76px 칸 안에서 안 잘림
        page._set_rail_folded(False, animate=False)
        assert not any(b.property("_railShort").isVisible() for b in btns)
    finally:
        page.deleteLater()


def test_rail_buttons_are_borderless_lines(styled_qapp):
    qss = _qss()
    block = qss.split('QPushButton[grade="rail"] {')[1].split("}")[0]
    assert "border: 1px solid transparent" in block and "background-color: transparent" in block
    assert 'QPushButton[grade="rail"]:checked' in qss


def test_invalid_and_nophoto_look_the_same(styled_qapp, tmp_path):
    page = _page(styled_qapp)
    try:
        page._set_field_state(page.ref_path_edit, "missing")
        page._set_field_state(page.val_path_edit, "nophoto")
        assert page.ref_path_edit.property("state") == page.val_path_edit.property("state") == "invalid"
        assert page.val_path_edit.property("_errLabel").text() == i18n.KO.SETUP_NO_PHOTOS
        block = _qss().split('QLineEdit[state="invalid"] {')[1].split("}")[0]
        assert "background" not in block                      # 분홍 채움 없음
    finally:
        page.deleteLater()


def test_browse_button_height_equals_input(styled_qapp):
    page = _page(styled_qapp)
    try:
        browse = [b for b in page.findChildren(QPushButton) if b.text() == i18n.KO.BTN_BROWSE]
        assert browse
        assert all(b.height() == page.ref_path_edit.height() for b in browse)
    finally:
        page.deleteLater()
