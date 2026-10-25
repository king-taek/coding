"""L8 — 걷힘이 진행 중일 때 `hide_overlay` 가 다시 불려도 먼저 건 콜백이 살아 있어야 한다.

``match_review_page._arm_entrance`` 는 ``hide_overlay(then=...)`` 로 걸린다.  최소표시 래치 중에
다른 곳이 인자 없는 ``hide_overlay()`` 를 부르면, 예전엔 ``self._hide_then = None`` 으로
덮어써 입장 연출이 통째로 사라졌다.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget  # noqa: E402

from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay  # noqa: E402


def _shown_overlay(styled_qapp):
    host = QWidget()
    host.resize(600, 400)
    host.show()
    ov = LoadingOverlay(host)
    ov.show_overlay("계산 중")
    styled_qapp.processEvents()
    return host, ov


def test_later_plain_hide_does_not_drop_pending_callback(styled_qapp):
    host, ov = _shown_overlay(styled_qapp)
    calls: list = []
    ov.hide_overlay(then=lambda: calls.append("arm"))
    assert ov.is_retiring()                            # 래치 중 — 아직 안 걷혔다
    ov.hide_overlay()                                  # 인자 없음 — 덮어쓰면 안 된다
    ov._finish_hide()                                  # 타이밍에 의존하지 않고 종료 확정
    assert calls == ["arm"]
    host.deleteLater()


def test_two_callbacks_both_run_in_order(styled_qapp):
    host, ov = _shown_overlay(styled_qapp)
    calls: list = []
    ov.hide_overlay(then=lambda: calls.append("first"))
    ov.hide_overlay(then=lambda: calls.append("second"))
    ov._finish_hide()
    assert calls == ["first", "second"]
    ov._finish_hide()                                  # 한 번 쓰면 비워진다
    assert calls == ["first", "second"]
    host.deleteLater()


def test_new_show_discards_callback_of_the_cancelled_hide(styled_qapp):
    host, ov = _shown_overlay(styled_qapp)
    calls: list = []
    ov.hide_overlay(then=lambda: calls.append("stale"))
    ov.show_overlay("다시 계산 중")                      # 걷힘 취소
    ov.hide_overlay()
    ov._finish_hide()
    assert calls == [], "취소된 걷힘의 콜백이 다음 걷힘에 새어 나왔다"
    host.deleteLater()
