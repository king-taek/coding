"""로딩 덮개 — 다시 불러도 깜빡이지 않고(M1), 짧은 일은 그리지 않는다(M2)."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QWidget  # noqa: E402

from aoi_verification.app.ui import motion  # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay  # noqa: E402


def _wait(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def overlay(styled_qapp, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host = QWidget()
    host.resize(600, 400)
    host.show()
    ov = LoadingOverlay(host)
    yield ov
    ov.hide_overlay()
    host.close()


def test_second_show_does_not_restart_entrance(overlay) -> None:
    overlay.show_overlay("처음")
    _wait(motion.LOADING_REVEAL_DELAY_MS + overlay.RISE_IN_MS + 150)
    assert overlay._fade == 1.0
    token = overlay._show_token
    overlay.show_overlay("다음 문구")
    assert overlay._fade == 1.0                      # 투명도 1 유지(페이드 되감기 없음)
    assert overlay._show_token == token              # 래치·모션 재시작 없음
    assert overlay._fade_anim.state() == overlay._fade_anim.State.Stopped


def test_short_job_never_paints_but_blocks_input(overlay) -> None:
    overlay.show_overlay("짧은 일")
    assert overlay._veiled and overlay._input_locked  # 그림은 지연, 입력 차단은 즉시
    assert overlay._fade == 0.0 and not overlay._panel.isVisible()
    overlay.hide_overlay()
    assert not overlay.isVisible() and not overlay._input_locked
    _wait(motion.LOADING_REVEAL_DELAY_MS + 80)
    assert not overlay.isVisible() and overlay._fade == 0.0   # 늦게 발화해도 안 뜬다


def test_long_job_reveals_after_delay(overlay) -> None:
    overlay.show_overlay("긴 일")
    _wait(motion.LOADING_REVEAL_DELAY_MS + 80)
    assert not overlay._veiled and overlay._panel.isVisible()
    assert overlay._fade > 0.0
