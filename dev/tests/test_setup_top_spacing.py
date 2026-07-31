"""메인(Setup) 화면 상단 여백 — 제목이 맨 위에 붙고, 안내 문구는 없다.

사용자 요청: "상단에 빈 공간이 많아서 좀 줄일게요 — 'AOI 레시피 검증' 글자를 위로,
'기준 장비와 검증 장비는…' 문구 삭제".

되돌아가기 쉬운 두 가지를 못 박는다.
1. 보기 옵션(다크 모드)이 제목 **위 별도 줄**로 다시 갈라지면 제목이 그 높이만큼
   내려간다 → 제목 줄과 스위치가 같은 줄(같은 y)에 있어야 한다.
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


def _title_label(page: sp.SetupPage) -> QLabel:
    for lbl in page.findChildren(QLabel):
        if lbl.text() == i18n.KO.SETUP_TITLE:
            return lbl
    raise AssertionError("제목 라벨을 찾지 못했다")


def test_dark_switch_shares_the_title_row(qapp):
    """보기 옵션이 제목 위 별도 줄로 돌아가면(= 제목이 내려가면) 실패."""
    page = _page(qapp)
    try:
        title = _title_label(page)
        switch = page._dark_switch
        t_mid = title.mapTo(page, title.rect().center()).y()
        s_mid = switch.mapTo(page, switch.rect().center()).y()
        # 허용치는 스위치 행 높이 — 제목 라벨 높이를 쓰면 줄이 갈라져도 통과한다
        # (라벨이 줄 높이만큼 늘어나 85px 이 되므로 별도 줄 간격 70px 을 삼킨다).
        assert abs(t_mid - s_mid) <= switch.height(), \
            f"제목({t_mid}px)과 다크 모드 스위치({s_mid}px)가 다른 줄에 있다"
    finally:
        page.close()


def test_title_sits_near_the_top(qapp):
    """제목 위에는 페이지 마진 말고 다른 띠가 없어야 한다."""
    page = _page(qapp)
    try:
        title = _title_label(page)
        top = title.mapTo(page, title.rect().topLeft()).y()
        assert top <= theme.PROFILE.page_margin + 8, f"제목 위 여백 {top}px"
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
    """제목·배지·스위치를 한 줄에 둬도 좁은 창에서 가로 스크롤이 없어야 한다."""
    page = _page(qapp, width)
    try:
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0, f"{width}px 가로 넘침"
    finally:
        page.close()
