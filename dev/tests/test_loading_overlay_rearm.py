"""페이지 전환이 오버레이를 **꺼 놓고 가는** 것을 막는다 (라운드 6 수리).

배경(실제 사고): `main_window._show_page` 는 무플래시 전환을 위해 새 페이지를
한 번 띄워 스냅샷을 찍고 **다시 옛 페이지로 되돌린 뒤** 240ms 후에 진짜로
전환한다 — 즉 새 페이지는 `show → hide → show` 를 겪는다.  그 중간의 `hide` 가
`LoadingOverlay.hideEvent` 를 발화시켜 페이드·스피너·입력잠금을 전부 끈다.

그런데 그 숨김은 '작업이 끝났다' 는 뜻이 아니다.  되살리지 않으면 다시 떴을 때

  · `_fade` 가 0 근처에서 얼어 **완전히 투명한 막**이 화면을 가득 덮고
    (스크림도 스피너도 안 보이는데 마우스는 전부 삼켜진다 — '앱이 죽은 듯'),
  · 입력잠금은 풀려 있어 **키보드로 [검토 완료] 가 눌린다**.

실측: 600행 검토 진입에서 약 4초 동안 그 상태였고, Tab+Space 로 아직 만들어지지도
않은 320행까지 전부 '유지' 로 확정됐다(사용자는 아무것도 못 본 채).

여기서 세우는 계약: **`hideEvent` 는 '끝' 이 아니다.**  끝은 `_finish_hide` 하나뿐이고,
그 전에 다시 보이면 오버레이는 스스로 무장 상태를 되찾는다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEvent, Qt                                # noqa: E402
from PyQt6.QtGui import QKeyEvent                                  # noqa: E402
from PyQt6.QtWidgets import QStackedWidget, QWidget                # noqa: E402

from aoi_verification.app.ui import motion                         # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (      # noqa: E402
    LoadingOverlay)


@pytest.fixture()
def motion_on(monkeypatch):
    """모션을 켠다 — 페이드가 도는 중에 숨어야 `_fade` 가 0 근처에서 언다.

    offscreen 기본값(False)에서는 `_fade` 가 처음부터 1.0 이라 '투명한 막' 증상이
    재현되지 않는다(잠금 해제 쪽만 재현된다)."""
    monkeypatch.setattr(motion, "enabled", lambda: True)


def _swap_like_show_page(app, stack, old: QWidget, new: QWidget) -> None:
    """`main_window._show_page` 의 스냅샷 스왑을 그대로 재현한다.

    setCurrentWidget(new) → (스냅샷) → setCurrentWidget(old) → … → setCurrentWidget(new)
    """
    stack.setCurrentWidget(new)
    app.processEvents()
    stack.setCurrentWidget(old)      # 스냅샷 뒤 원복 — 여기서 hideEvent 가 온다
    app.processEvents()
    stack.setCurrentWidget(new)      # 전환 커밋
    app.processEvents()


def test_overlay_rearms_itself_after_a_transition_swap(styled_qapp, motion_on):
    """스왑을 겪고도 **보이고·돌고·잠긴** 상태여야 한다."""
    stack = QStackedWidget()
    old = QWidget()
    page = QWidget()
    stack.addWidget(old)
    stack.addWidget(page)
    stack.resize(1280, 800)
    stack.show()
    styled_qapp.processEvents()

    ov = LoadingOverlay(page)
    ov.show_overlay("행 만드는 중", cancelable=False)
    styled_qapp.processEvents()

    _swap_like_show_page(styled_qapp, stack, old, page)

    assert ov.isVisible(), "스왑 뒤 오버레이가 사라졌다"
    assert ov._fade == pytest.approx(1.0), (
        f"오버레이가 **투명한 막**으로 남았다(_fade={ov._fade:.3f}) — 화면은 멀쩡해 "
        "보이는데 마우스가 전부 삼켜진다")
    assert ov._spinner._timer.isActive(), "스피너가 멈춘 채 남았다 — 작업 중 신호가 없다"
    assert ov._input_locked, (
        "입력잠금이 풀린 채 남았다 — 키보드로 [검토 완료] 가 눌려 못 본 행까지 확정된다")

    ov.hide_overlay()
    stack.deleteLater()


def test_transition_swap_keeps_swallowing_keys(styled_qapp, motion_on):
    """계약의 알맹이 — 스왑 뒤에도 뒤 화면 키를 실제로 막는다."""
    stack = QStackedWidget()
    old = QWidget()
    page = QWidget()
    stack.addWidget(old)
    stack.addWidget(page)
    stack.resize(1280, 800)
    stack.show()
    styled_qapp.processEvents()

    ov = LoadingOverlay(page)
    ov.show_overlay("계산 중")
    styled_qapp.processEvents()
    _swap_like_show_page(styled_qapp, stack, old, page)

    victim = QWidget()
    victim.show()
    styled_qapp.processEvents()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                    Qt.KeyboardModifier.NoModifier)
    assert ov.eventFilter(victim, key) is True, (
        "스왑 뒤 오버레이가 뒤 화면 키를 통과시킨다 — Space 하나로 [검토 완료] 가 눌린다")

    victim.deleteLater()
    ov.hide_overlay()
    stack.deleteLater()


def test_a_finished_overlay_does_not_come_back(styled_qapp, motion_on):
    """되살리기는 **끝나지 않은** 작업에만 — 끝난 오버레이는 다시 떠도 무장하지 않는다.

    이 반대 방향이 없으면 `_rearm` 이 '한번 뜬 오버레이는 영원히 잠근다' 가 된다.
    """
    host = QWidget()
    host.resize(600, 400)
    host.show()
    ov = LoadingOverlay(host)
    ov.show_overlay("계산 중")
    styled_qapp.processEvents()

    ov.hide_overlay()
    for _ in range(60):                     # 최소표시 래치 + 페이드아웃을 다 소진
        styled_qapp.processEvents()
    ov._finish_hide()                       # 타이밍에 의존하지 않게 종료를 확정
    assert ov._active is False

    ov.show()                               # 누군가 위젯만 다시 띄우면
    styled_qapp.processEvents()
    assert ov._input_locked is False, "끝난 오버레이가 앱 키보드를 다시 잠갔다"
    assert ov._spinner._timer.isActive() is False, "끝난 오버레이의 스피너가 되살아났다"

    ov.hide()
    host.deleteLater()


def test_review_page_survives_the_real_transition(styled_qapp, motion_on, tmp_path):
    """앱의 실제 조합 — 검토 페이지 행 생성 중에 전환 스왑이 지나간다."""
    from aoi_verification.app.models.result import MatchResult
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage

    ms = []
    for i in range(200):                    # 배치 생성(_ROW_BATCH=40)이 걸리도록
        r = tmp_path / f"r{i}.jpg"
        v = tmp_path / f"v{i}.jpg"
        r.write_bytes(b"")
        v.write_bytes(b"")
        ms.append(MatchResult(slot="S1", ref_path=r, val_path=v, score=0.9))

    stack = QStackedWidget()
    old = QWidget()
    page = MatchReviewPage()
    stack.addWidget(old)
    stack.addWidget(page)
    stack.resize(1280, 800)
    stack.show()
    styled_qapp.processEvents()

    page.load_state(ms)                     # 오버레이 무장(앱과 같은 순서)
    _swap_like_show_page(styled_qapp, stack, old, page)

    assert page._pending_rows, "전제가 깨졌다 — 행이 이미 다 만들어졌다"
    ov = page._loading
    assert ov.isVisible()
    assert ov._fade == pytest.approx(1.0), (
        f"행 생성 중인데 화면이 밝다(_fade={ov._fade:.3f}) — 사용자는 조작해도 되는 줄 안다")
    assert ov._input_locked, (
        "행 생성 중인데 키가 통한다 — 아직 만들어지지 않은 행까지 '유지' 로 확정된다")
    # 커버 계약(라운드 5)도 함께 지켜지는지 — 두 수리가 서로를 깨지 않았다는 증명.
    btn = page.btn_done
    centre = btn.mapTo(page, btn.rect().center())
    assert ov.geometry().contains(centre)

    stack.deleteLater()
