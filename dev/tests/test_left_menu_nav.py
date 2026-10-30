"""왼쪽 메뉴 — [매칭] 버튼 · 접기 · 도구 화면 이동(사용자 요청 2026-10-30).

1. 메뉴 맨 위에 기본 화면(매칭 설정)으로 가는 [매칭] — 아이콘은 프로그램 아이콘.
2. [«] 를 누르면 아이콘만 남는다(이름은 툴팁으로).
3. 사진 정보 · Wafer Map · GL 히스토그램은 팝업이 아니라 오른쪽 화면이 바뀌고,
   그때 메뉴가 저절로 접힌다.  [매칭] 으로 돌아오면 사용자가 고른 상태로 되돌린다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402

from aoi_verification.app import i18n  # noqa: E402
from aoi_verification.app.ui.pages import setup_page as sp  # noqa: E402


def _page(qapp, w: int = 1400, h: int = 900) -> sp.SetupPage:
    page = sp.SetupPage()
    page.resize(w, h)
    page.show()
    for _ in range(8):
        qapp.processEvents()
    return page


@pytest.fixture
def no_sheets(monkeypatch):
    seen = []
    monkeypatch.setattr(sp.sheets, "run", lambda dlg, **kw: seen.append(dlg) or 0)
    return seen


def test_match_button_is_first_with_program_icon(qapp):
    page = _page(qapp)
    try:
        assert page._tool_buttons[0] is page.match_btn
        assert page.match_btn.text() == i18n.KO.MATCH_NAV_BUTTON
        assert not page.match_btn.icon().isNull()
        assert page.match_btn.isChecked()
        assert page.current_page() == "match"
    finally:
        page.close()


def test_fold_button_leaves_only_icons(qapp):
    page = _page(qapp)
    try:
        wide = page._tool_rail.width()
        assert not page.is_rail_folded()
        page.rail_fold_btn.click()
        assert page.is_rail_folded()
        assert page._tool_rail.width() < wide
        for b in (*page._tool_buttons, page.update_btn):
            assert b.text() == ""
            assert not b.icon().isNull()
            assert b.property("_railText") in b.toolTip()      # 이름은 툴팁으로
        assert not page._title_label.isVisible()
        page.rail_fold_btn.click()
        assert not page.is_rail_folded()
        assert page._tool_rail.width() == wide
        assert page.image_info_btn.text() == i18n.KO.IMAGE_INFO_BUTTON
    finally:
        page.close()


@pytest.mark.parametrize("key,btn", [("image_info", "image_info_btn"),
                                     ("wafer_map", "wafer_map_btn"),
                                     ("gl", "gl_btn")])
def test_tool_opens_as_page_and_folds_menu(qapp, no_sheets, key, btn):
    page = _page(qapp)
    try:
        getattr(page, btn).click()
        qapp.processEvents()
        assert no_sheets == [], "도구가 아직 팝업(시트)으로 뜬다"
        assert page.current_page() == key
        dlg = page.tool_dialog(key)
        assert dlg is not None and dlg.isVisible()
        assert page._pages.currentWidget().isAncestorOf(dlg)
        assert page.is_rail_folded(), "도구 화면으로 가면 메뉴가 저절로 접혀야 한다"
        assert getattr(page, btn).isChecked() and not page.match_btn.isChecked()
        assert not page.start_btn.isVisible()                # 시작 바는 매칭 화면 것
        page.match_btn.click()
        assert page.current_page() == "match"
        assert not page.is_rail_folded(), "매칭으로 돌아오면 펼친 상태로 되돌린다"
        assert page.start_btn.isVisible()
    finally:
        page.close()


def test_returning_keeps_user_fold_choice(qapp, no_sheets):
    page = _page(qapp)
    try:
        page.toggle_rail()                       # 사용자가 접어 둔 채
        page.gl_btn.click()
        page.match_btn.click()
        assert page.is_rail_folded()             # 돌아와도 접힌 그대로
    finally:
        page.close()


def test_tool_page_is_kept_between_visits(qapp, no_sheets):
    page = _page(qapp)
    try:
        page.wafer_map_btn.click()
        first = page.tool_dialog("wafer_map")
        page.match_btn.click()
        page.wafer_map_btn.click()
        assert page.tool_dialog("wafer_map") is first     # 보던 맵이 그대로
    finally:
        page.close()


def test_escape_does_not_close_embedded_tool(qapp, no_sheets):
    page = _page(qapp)
    try:
        page.image_info_btn.click()
        dlg = page.tool_dialog("image_info")
        qapp.sendEvent(dlg, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                      Qt.KeyboardModifier.NoModifier))
        qapp.processEvents()
        assert dlg.isVisible(), "Esc 로 화면이 빈 칸이 됐다"
        assert page.current_page() == "image_info"
    finally:
        page.close()


def test_extract_and_match_are_menu_pages(qapp):
    page = _page(qapp)
    try:
        page.extract_btn.click()
        assert page.is_extract_mode() and page.current_page() == "extract"
        assert page.extract_btn.isChecked() and not page.match_btn.isChecked()
        page.extract_btn.click()                       # 다시 눌러도 추출 그대로(토글 아님)
        assert page.is_extract_mode() and page.extract_btn.isChecked()
        page.match_btn.click()
        assert not page.is_extract_mode() and page.match_btn.isChecked()
    finally:
        page.close()


def test_wafer_map_fullscreen_hides_menu(qapp, no_sheets):
    page = _page(qapp)
    try:
        seen = []
        page.tool_fullscreen_changed.connect(seen.append)
        page.wafer_map_btn.click()
        dlg = page.tool_dialog("wafer_map")
        dlg.set_fullscreen(True)
        assert not page._tool_rail.isVisible() and seen == [True]
        # 전체화면의 Esc 는 맵이 받아 빠져나온다(삼키지 않는다).
        qapp.sendEvent(dlg, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                      Qt.KeyboardModifier.NoModifier))
        assert not dlg.is_fullscreen()
        assert page._tool_rail.isVisible() and seen == [True, False]
    finally:
        page.close()


def test_fold_animation_settles_and_survives_teardown(qapp, monkeypatch):
    """모션을 켠 경로 — 폭 이동이 끝나면 최종 폭에 닿고, 도중에 화면을 지워도 죽지 않는다."""
    from PyQt6.QtTest import QTest
    from aoi_verification.app.ui import motion
    monkeypatch.setattr(motion, "enabled", lambda: True)
    page = _page(qapp)
    try:
        page.toggle_rail()
        assert page._rail_anim is not None and page._rail_anim.state() != 0
        QTest.qWait(motion.DUR_RAIL_FOLD + 250)
        assert page._tool_rail.width() == page._rail_width(True)
        page.toggle_rail()
        QTest.qWait(motion.DUR_RAIL_FOLD + 250)
        assert page._tool_rail.width() == page._rail_width(False)
        assert page.image_info_btn.text() == i18n.KO.IMAGE_INFO_BUTTON
        page.toggle_rail()                    # 도중에 지운다
    finally:
        page.close()
        page.deleteLater()
    QTest.qWait(motion.DUR_RAIL_FOLD + 100)


def test_tool_page_stays_folded_when_window_widens(qapp, no_sheets):
    """좁은 창에서 도구를 연 뒤 창을 넓혀도 도구 화면에서는 메뉴가 저절로 펴지지 않는다."""
    page = _page(qapp, 800, 600)
    try:
        page.wafer_map_btn.click()
        page.resize(1400, 900)
        for _ in range(8):
            qapp.processEvents()
        assert page._wide and page.is_rail_folded()
        page.match_btn.click()
        assert not page.is_rail_folded()                # 매칭은 넓은 창의 사용자 선택(펼침)
    finally:
        page.close()


def test_peeking_on_tool_page_keeps_match_choice(qapp, no_sheets):
    """도구 화면에서 [»] 로 잠깐 펼쳐도, 매칭 화면에서 고른 '접힘' 은 그대로다."""
    page = _page(qapp)
    try:
        page.toggle_rail()                       # 매칭에서 접음
        page.gl_btn.click()
        page.toggle_rail()                       # 도구에서 펼쳐 봄
        assert not page.is_rail_folded()
        page.match_btn.click()
        assert page.is_rail_folded()
        page.gl_btn.click()                      # 다시 들어가면 또 저절로 접힌다
        assert page.is_rail_folded()
    finally:
        page.close()


def test_extract_drop_ignored_while_tool_page_shown(qapp, no_sheets, tmp_path):
    """추출 모드인 채 도구 화면에 폴더를 놓아도 안 보이는 추출 목록에 쌓이지 않는다."""
    from PyQt6.QtCore import QMimeData, QPointF, QUrl
    from PyQt6.QtGui import QDropEvent
    page = _page(qapp)
    try:
        page.extract_btn.click()
        page.image_info_btn.click()
        before = page.ref_path_edit.text()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        ev = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        page.dropEvent(ev)
        assert page.ref_path_edit.text() == before
        assert len(page._lot_paths()) == 0 or page._lot_paths() == [before]
    finally:
        page.close()


def test_visited_tool_does_not_widen_matching_page(qapp, no_sheets):
    """한 번 연 도구 화면(숨은 페이지)이 매칭 화면의 최소 폭을 넓히지 않는다.

    QStackedWidget 은 숨은 페이지까지 합쳐 최소 크기를 잡는다 — 넓은 도구가 남아 있으면
    좁은 창의 매칭 화면에 가로 스크롤이 생긴다(PR #91 리뷰)."""
    from PyQt6.QtWidgets import QWidget
    page = _page(qapp, 800, 600)
    try:
        before = page._pages.minimumSizeHint().width()
        page.gl_btn.click()
        wide = QWidget()
        wide.setMinimumWidth(3000)                     # 아주 긴 파일명 같은 것
        page.tool_dialog("gl").layout().addWidget(wide)
        page.match_btn.click()
        for _ in range(8):
            qapp.processEvents()
        assert page._pages.minimumSizeHint().width() <= max(before, 200)
        assert page._scroll.horizontalScrollBar().maximum() == 0
    finally:
        page.close()
