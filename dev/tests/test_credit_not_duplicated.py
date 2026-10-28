"""개발자 크레딧은 한 화면에 **한 번만** 보인다.

실측 버그: Setup 화면에 'Developed by 임현택' 이 **두 번** 떠 있었다 —
가운데(페이지가 직접 넣은 라벨)와 좌하단(상태바).  두 자리가 서로를 모른 채
각자 넣고 있었고, 화면을 캡처해 보기 전까지 아무도 몰랐다.

크레딧은 기본적으로 `main_window._credit_label`(상태바) 하나다 — **모든 화면 공통**.
예외: 설정 화면은 자기 바닥 줄(레일 칸)에 크레딧을 두고, 그동안 상태바 쪽은 숨긴다
(`MainWindow._sync_statusbar`).  어느 화면에서도 **보이는 크레딧은 하나**다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel                          # noqa: E402

from aoi_verification.app import i18n                       # noqa: E402


def _credit_labels(widget) -> list[QLabel]:
    return [w for w in widget.findChildren(QLabel)
            if (w.text() or "").strip() == i18n.KO.CREDIT]


def test_setup_page_has_exactly_one_credit(styled_qapp):
    """설정 화면은 자기 바닥 줄에 크레딧을 **하나** 둔다(상태바는 그동안 숨긴다)."""
    from aoi_verification.app.ui.pages import setup_page as sp

    page = sp.SetupPage()
    try:
        assert len(_credit_labels(page)) == 1
    finally:
        page.close()


def test_result_page_does_not_add_its_own_credit(styled_qapp):
    from aoi_verification.app.ui.pages import result_page as rp

    page = rp.ResultPage()
    try:
        assert _credit_labels(page) == [], (
            "ResultPage 가 크레딧을 또 넣었다 — 상태바와 겹쳐 두 번 보인다")
    finally:
        page.close()


def test_one_visible_credit_on_every_page(styled_qapp):
    """★ 창 전체에서 **보이는** 크레딧은 늘 하나 — 설정 화면은 페이지 것, 나머지는 상태바 것.

    '전부 지운다' 로도 '두 번 보인다' 로도 깨지게 못박는다."""
    from aoi_verification.app.ui.main_window import MainWindow

    win = MainWindow()
    try:
        win.show()
        styled_qapp.processEvents()

        def visible() -> int:
            return sum(1 for w in _credit_labels(win) if w.isVisibleTo(win)
                       and (w is not win._credit_label or win._status_bar.isVisibleTo(win)))

        win._show_page(win._setup_page, animate=False)
        assert visible() == 1, "설정 화면: 보이는 크레딧이 1개가 아니다"
        win._show_page(win._result_page, animate=False)
        assert visible() == 1, "결과 화면: 보이는 크레딧이 1개가 아니다"
        assert win._credit_label.isVisibleTo(win)
    finally:
        win.close()
