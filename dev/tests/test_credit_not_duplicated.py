"""개발자 크레딧은 한 화면에 **한 번만** 보인다.

실측 버그: Setup 화면에 'Developed by 임현택' 이 **두 번** 떠 있었다 —
가운데(페이지가 직접 넣은 라벨)와 좌하단(상태바).  두 자리가 서로를 모른 채
각자 넣고 있었고, 화면을 캡처해 보기 전까지 아무도 몰랐다.

크레딧의 단일 출처는 `main_window._credit_label`(상태바) 다 — **모든 화면 공통**
이므로 페이지는 자기 몫을 따로 넣지 않는다.  페이지가 다시 넣으면 이 테스트가
깨진다.
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


def test_setup_page_does_not_add_its_own_credit(styled_qapp):
    """페이지 안에는 크레딧이 없다 — 상태바가 담당한다."""
    from aoi_verification.app.ui.pages import setup_page as sp

    page = sp.SetupPage()
    try:
        assert _credit_labels(page) == [], (
            "SetupPage 가 크레딧을 또 넣었다 — 상태바와 겹쳐 두 번 보인다")
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


def test_status_bar_still_shows_the_credit(styled_qapp):
    """★ 위 두 테스트만 있으면 '크레딧을 전부 지운다' 로도 통과한다.

    단일 출처가 **살아 있는지**를 함께 못박아, 지우는 방향의 회귀도 잡는다.
    """
    from aoi_verification.app.ui.main_window import MainWindow

    win = MainWindow()
    try:
        found = _credit_labels(win)
        assert len(found) == 1, f"창 전체에 크레딧이 {len(found)}개 — 1개여야 한다"
        # 그 하나는 상태바의 것이다
        assert found[0] is win._credit_label
    finally:
        win.close()
