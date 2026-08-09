"""로딩 오버레이가 부모를 **끝까지** 덮는지 못 박는다.

배경(실제 사고, 라운드 5): 오버레이의 잠금에는 두 가지 책임이 섞여 있다 —
**입력을 삼키는 것**과 **부모 크기를 따라가는 것**.  '보이지 않으면 아무것도 막지
않는다' 는 가드를 `eventFilter` 맨 앞에 두었더니, 그 아래에 있던 부모 Resize 추종
분기까지 함께 막혔다.

앱에서 이 조합이 실제로 성립한다: 페이지들은 만들어져 스택에 담겨만 있고
(QStackedLayout 은 **현재 페이지에만** 기하를 준다) 첫 전환 전까지 640x480 이다.
그런데 검토 진입은 `load_state`(→ 오버레이 표시) 를 부른 **뒤** 페이지를 전환한다.
그래서 오버레이는 640x480 으로 무장하고, 전환 때 오는 Resize(1280x800)는 가드가
삼켜 다시 덮지 못했다.

결과가 나빴다 — 화면의 좌상단 1/4 만 어두워지고 **[검토 완료] 가 밝게 노출돼**,
아직 만들어지지도 않은 행까지 전부 '유지' 로 확정됐다(사용자는 120행만 봤는데
300건이 확정). `match_review_page` 주석이 막으려던 바로 그 사고다.

여기서 지키는 두 계약(둘 다 필요하다 — 하나를 고치다 다른 하나를 깨뜨렸다):
  1. **덮기**: 숨어 있는 동안 부모가 커져도, 뜰 때는 부모를 전부 덮는다.
  2. **삼키기**: 보이지 않는 동안에는 입력을 막지 않는다(앱 키보드 사망 방지).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, Qt                                # noqa: E402
from PyQt6.QtGui import QKeyEvent                                  # noqa: E402
from PyQt6.QtWidgets import QStackedWidget, QWidget                # noqa: E402

from aoi_verification.app.ui.widgets.loading_overlay import (      # noqa: E402
    LoadingOverlay)


def test_overlay_follows_a_parent_that_grew_while_hidden(styled_qapp):
    """스택에 담겨만 있던 페이지 위에서 무장 → 전환 → 전부 덮여야 한다."""
    stack = QStackedWidget()
    first = QWidget()
    page = QWidget()                    # 아직 현재 페이지가 아니라 작다
    stack.addWidget(first)
    stack.addWidget(page)
    stack.resize(1280, 800)
    stack.show()
    styled_qapp.processEvents()

    assert page.width() < 1280, "전제가 깨졌다 — 숨은 페이지가 이미 크다"
    ov = LoadingOverlay(page)
    ov.show_overlay("행 만드는 중", cancelable=False)   # 작을 때 무장
    styled_qapp.processEvents()

    stack.setCurrentWidget(page)        # 이제 전환 — 페이지가 커진다
    styled_qapp.processEvents()

    assert ov.geometry().size() == page.size(), (
        f"오버레이가 부모를 다 덮지 못한다 — 오버레이 {ov.geometry()} / "
        f"페이지 {page.size()} (가려지지 않은 곳의 버튼이 눌린다)")
    stack.deleteLater()


def test_overlay_covers_after_a_plain_resize(styled_qapp):
    """평범한 창 크기 변경에도 계속 따라간다(기존 동작 — 함께 지킨다)."""
    host = QWidget()
    host.resize(600, 400)
    host.show()
    ov = LoadingOverlay(host)
    ov.show_overlay("계산 중")
    styled_qapp.processEvents()

    host.resize(1400, 900)
    styled_qapp.processEvents()
    assert ov.geometry().size() == host.size(), \
        f"창을 키웠는데 오버레이가 따라오지 않는다 ({ov.geometry()} vs {host.size()})"
    host.deleteLater()


def test_hidden_overlay_still_does_not_swallow_input(styled_qapp):
    """덮기를 되살리면서 **삼키기 불변식**을 깨뜨리면 안 된다(라운드 4 계약)."""
    host = QWidget()
    host.resize(600, 400)
    ov = LoadingOverlay(host)           # host 를 show() 하지 않는다 = 숨은 부모
    ov.show_overlay("되살아난 오버레이", cancelable=True)
    styled_qapp.processEvents()

    victim = QWidget()
    victim.show()
    styled_qapp.processEvents()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N,
                    Qt.KeyboardModifier.NoModifier)
    assert ov.eventFilter(victim, key) is False, \
        "보이지 않는 오버레이가 다시 앱 키보드를 잠갔다"

    victim.deleteLater()
    host.deleteLater()


def test_visible_overlay_still_swallows_input(styled_qapp):
    """대조군 — 떠 있는 동안에는 여전히 막아야 한다(그게 이 잠금의 목적이다)."""
    host = QWidget()
    host.resize(600, 400)
    host.show()
    ov = LoadingOverlay(host)
    ov.show_overlay("계산 중", cancelable=True)
    styled_qapp.processEvents()

    other = QWidget()
    other.show()
    styled_qapp.processEvents()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_N,
                    Qt.KeyboardModifier.NoModifier)
    assert ov.eventFilter(other, key) is True, \
        "떠 있는 오버레이가 뒤 화면 키를 통과시킨다"

    other.deleteLater()
    host.deleteLater()


def test_review_page_overlay_shields_the_done_button(styled_qapp, tmp_path):
    """앱의 실제 순서 — `load_state`(오버레이) → 전환. [검토 완료] 가 가려져야 한다.

    가려지지 않으면 **사용자가 보지 못한 행까지 전부 '유지' 로 확정**된다.
    """
    from aoi_verification.app.models.result import MatchResult
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage

    ms = []
    for i in range(200):                # 배치 생성이 걸리도록 충분히 많이
        r = tmp_path / f"r{i}.jpg"
        v = tmp_path / f"v{i}.jpg"
        r.write_bytes(b"")
        v.write_bytes(b"")
        ms.append(MatchResult(slot="S1", ref_path=r, val_path=v, score=0.9))

    stack = QStackedWidget()
    stack.addWidget(QWidget())
    page = MatchReviewPage()
    stack.addWidget(page)
    stack.resize(1280, 800)
    stack.show()
    styled_qapp.processEvents()

    page.load_state(ms)                 # 아직 전환 전 — 페이지가 작다
    stack.setCurrentWidget(page)        # 그 다음에 전환(앱과 같은 순서)
    styled_qapp.processEvents()

    assert page._pending_rows, "전제가 깨졌다 — 행이 이미 다 만들어졌다"
    assert page._loading.isVisible()
    btn = page.btn_done
    centre = btn.mapTo(page, btn.rect().center())
    assert page._loading.geometry().contains(centre), (
        "행 생성 중인데 [검토 완료] 가 오버레이 밖에 있다 — "
        f"오버레이 {page._loading.geometry()} / 버튼 중심 {centre}")
    stack.deleteLater()
