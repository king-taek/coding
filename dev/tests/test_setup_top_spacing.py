"""메인(Setup) 화면 상단 여백 — 제목이 맨 위에 붙고, 안내 문구는 없다.

사용자 요청: "상단에 빈 공간이 많아서 좀 줄일게요 — 'AOI 레시피 검증' 글자를 위로,
'기준 장비와 검증 장비는…' 문구 삭제".

되돌아가기 쉬운 두 가지를 못 박는다.
1. 제목 줄의 항목(모드 배지)이 제목 **위 별도 줄**로 갈라지면 제목이 그 높이만큼
   내려간다 → 제목과 배지가 같은 줄(같은 y)에 있어야 한다.
2. 그렇게 한 줄로 합쳐도 800px 에서 가로가 넘치면 안 된다(이 앱은 가로 넘침 금지).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel, QScrollArea                 # noqa: E402

from aoi_verification.app import i18n                            # noqa: E402
from aoi_verification.app.ui import theme                        # noqa: E402
from aoi_verification.app.ui.pages import setup_page as sp       # noqa: E402


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    """테마 적용된 앱 — conftest 가 `setStyleSheet` 을 세션당 1회만 부른다."""
    return styled_qapp


def _page(qapp, w: int = 1280, h: int = 900) -> sp.SetupPage:
    page = sp.SetupPage()
    page.resize(w, h)
    page.show()
    for _ in range(8):
        qapp.processEvents()
    return page


def test_mode_badge_sits_bottom_left(qapp):
    """판정 기준 배지는 하단 바 왼쪽 끝, 시작 버튼과 같은 줄이다(사용자 요청 2026-10-28)."""
    page = _page(qapp)
    try:
        page.resize(1280, 800)
        page.show()
        qapp.processEvents()
        badge = page._mode_badge_card
        start = page.start_btn
        b = badge.mapTo(page, badge.rect().center())
        s = start.mapTo(page, start.rect().center())
        assert abs(b.y() - s.y()) <= start.height(), "배지가 시작 버튼 줄이 아니다"
        assert b.x() < s.x() and b.x() < page.width() // 2, "배지가 왼쪽이 아니다"
    finally:
        page.close()


def _logo_label(page: sp.SetupPage) -> QLabel:
    for lbl in page.findChildren(QLabel):
        if lbl.property("role") == "appLogo":
            return lbl
    raise AssertionError("로고 라벨을 찾지 못했다")


def test_logo_and_title_sit_atop_the_tool_rail(qapp):
    """로고 + 제목은 왼쪽 도구 목록 맨 위 한 줄이다(사용자 선택 2026-10-29, B안).

    Defect 추출 모드에서도 같은 자리에서 제목 글자만 바뀐다."""
    page = _page(qapp)
    try:
        logos = [l for l in page.findChildren(QLabel)
                 if l.property("role") == "appLogo" and l.isVisible()]
        assert logos == [page._rail_logo], "보이는 로고가 도구 목록 위 하나가 아니다"
        logo, title = page._rail_logo, page._title_label
        assert page._tool_rail.isAncestorOf(logo) and page._tool_rail.isAncestorOf(title)
        l_mid = logo.mapTo(page, logo.rect().center())
        t_mid = title.mapTo(page, title.rect().center())
        assert abs(l_mid.y() - t_mid.y()) <= logo.height() // 2, "로고와 제목이 다른 줄이다"
        assert logo.mapTo(page, logo.rect().topRight()).x() <= \
            title.mapTo(page, title.rect().topLeft()).x(), "로고가 제목 왼쪽이 아니다"
        btn_y = page.image_info_btn.mapTo(page, page.image_info_btn.rect().topLeft()).y()
        assert logo.mapTo(page, logo.rect().bottomLeft()).y() <= btn_y, \
            "로고가 첫 도구 버튼 아래에 있다"
        assert title.text() == i18n.KO.SETUP_TITLE
        page.set_extract_mode(True)
        for _ in range(4):
            qapp.processEvents()
        assert title.text() == i18n.KO.EXTRACT_TITLE and title.isVisible()
        assert logo.isVisible()
    finally:
        page.close()


def test_no_setup_hint_paragraph(qapp):
    """'기준 장비와 검증 장비는…' 안내 문단은 화면에서 제거됐다."""
    assert not hasattr(i18n.KO, "SETUP_HINT")
    page = _page(qapp)
    try:
        texts = [lbl.text() for lbl in page.findChildren(QLabel)]
        assert not any("서로 다른 호기의 폴더입니다" in t for t in texts)
    finally:
        page.close()


@pytest.mark.parametrize("width", [800, 1024, 1512])
def test_no_horizontal_overflow(qapp, width):
    """로고·제목·배지를 한 줄에 둬도 좁은 창에서 가로 스크롤이 없어야 한다."""
    page = _page(qapp, width)
    try:
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0, f"{width}px 가로 넘침"
    finally:
        page.close()
