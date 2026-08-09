"""매칭 [중지] → [아니오] 가 앱 키보드를 잠그지 않는지 못 박는다 (U-19 경쟁 조건).

배경(실제 사고): 확인창을 띄우려고 오버레이를 잠시 내렸다가 [아니오] 면 되돌리는데,
그 확인창은 **중첩 이벤트 루프**라 그동안 매칭 워커의 시그널이 계속 배달된다.
사용자가 문구를 읽는 몇 초 사이에 매칭이 끝나 검토 화면으로 넘어가 버릴 수 있고,
그 뒤 [아니오] 가 돌아오면 오버레이를 되살린다 — 그런데 부모(매칭 페이지)가 이미
숨겨져 있어 **화면에는 나타나지 못하면서 앱 전역 입력 잠금만 걸린다.**
보상 해제인 `hideEvent` 도 영영 오지 않으므로, 어두운 화면도 진행 표시도 [중지]
버튼도 없는 상태에서 **앱 전체가 키보드와 단축키를 못 받는다.** 마우스만 살아 있어
사용자는 원인을 알 수 없다.

`loading_overlay._set_input_lock` 주석이 못 박아 둔 바로 그 사고 유형이다.
두 겹으로 막는다 — 되돌리는 쪽이 페이지 가시성을 확인하고(출처),
필터 자신이 보이지 않을 때는 아무것도 막지 않는다(불변식).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, Qt                                # noqa: E402
from PyQt6.QtGui import QKeyEvent                                  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget                  # noqa: E402

from aoi_verification.app.ui.widgets.loading_overlay import (      # noqa: E402
    LoadingOverlay)


# ---------------------------------------------------------------------------
# 불변식 — 보이지 않는 오버레이는 아무것도 막지 않는다
# ---------------------------------------------------------------------------
def test_hidden_overlay_never_swallows_keys(styled_qapp):
    """잠금은 '가려서 못 누르게 한다' 는 뜻이다 — 가리고 있지 않으면 권한도 없다."""
    host = QWidget()
    host.resize(600, 400)
    host.show()
    ov = LoadingOverlay(host)
    ov.show_overlay("계산 중", cancelable=True)
    styled_qapp.processEvents()
    assert ov.isVisible() and ov._input_locked

    other = QWidget()
    other.show()
    styled_qapp.processEvents()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier)
    assert ov.eventFilter(other, key) is True, "떠 있는 동안에는 막아야 한다"

    # 부모가 숨으면(페이지 전환) 오버레이도 보이지 않게 된다.
    host.hide()
    styled_qapp.processEvents()
    assert not ov.isVisible()
    assert ov.eventFilter(other, key) is False, \
        "보이지 않는 오버레이가 키를 삼킨다 — 앱 전체가 키보드를 잃는다"

    other.deleteLater()
    host.deleteLater()


def test_lock_armed_while_parent_hidden_does_not_freeze_the_app(styled_qapp):
    """경쟁의 핵심 — 숨은 부모 위에서 show_overlay 가 불려도 앱은 멀쩡해야 한다."""
    host = QWidget()
    host.resize(600, 400)          # show() 하지 않는다 = 페이지가 이미 전환된 상태
    ov = LoadingOverlay(host)
    ov.show_overlay("되살아난 오버레이", cancelable=True)
    styled_qapp.processEvents()

    victim = QWidget()
    victim.show()
    styled_qapp.processEvents()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N, Qt.KeyboardModifier.NoModifier)
    assert ov.eventFilter(victim, key) is False, \
        "보이지도 않는 오버레이가 앱 키보드를 잠갔다"

    victim.deleteLater()
    host.deleteLater()


# ---------------------------------------------------------------------------
# 출처 — 되돌리는 쪽이 페이지 가시성을 확인한다
# ---------------------------------------------------------------------------
def _match_page(styled_qapp):
    from aoi_verification.app.ui.pages.match_page import MatchPage
    page = MatchPage()
    page.resize(1000, 700)
    page._precompute_done = 3
    page._precompute_total = 28
    return page


def test_no_restore_when_the_page_already_moved_on(styled_qapp, monkeypatch):
    """확인창을 읽는 사이 매칭이 끝나 화면이 넘어갔다면 되돌릴 것이 없다."""
    from PyQt6.QtWidgets import QMessageBox
    import aoi_verification.app.ui.pages.match_page as MP

    page = _match_page(styled_qapp)
    page.show()
    styled_qapp.processEvents()
    page._loading.show_overlay("유사도 계산", cancelable=True)
    styled_qapp.processEvents()

    def _ask_and_switch(*a, **k):
        # 중첩 루프 안에서 매칭이 끝나 페이지가 넘어간 상황을 재현한다.
        page.hide()
        styled_qapp.processEvents()
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(MP.sheets, "ask", _ask_and_switch)
    assert page._confirm_cancel() is False          # [아니오] → 취소하지 않는다
    styled_qapp.processEvents()

    assert not page._loading._input_locked, \
        "화면이 넘어갔는데 오버레이를 되살려 앱 입력을 잠갔다"
    page.deleteLater()


def test_restore_still_happens_when_the_page_is_still_there(styled_qapp,
                                                            monkeypatch):
    """대조군 — 경쟁이 없으면 [아니오] 는 예전처럼 오버레이를 되돌려야 한다."""
    from PyQt6.QtWidgets import QMessageBox
    import aoi_verification.app.ui.pages.match_page as MP

    page = _match_page(styled_qapp)
    page.show()
    styled_qapp.processEvents()
    page._loading.show_overlay("유사도 계산", cancelable=True)
    styled_qapp.processEvents()

    monkeypatch.setattr(MP.sheets, "ask",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    assert page._confirm_cancel() is False
    styled_qapp.processEvents()

    assert page._loading.isVisible(), "작업이 계속 도는데 진행 표시가 사라졌다"
    assert page._loading._input_locked, "되돌렸는데 입력 잠금이 걸리지 않았다"
    page.deleteLater()
