"""매칭 흐름 보강 — 좌표 모드 실패 멈춤 · 도는 매칭 스레드 수명 · 검토 덮개 · 첫 슬롯 덮개."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import i18n  # noqa: E402


def _page(monkeypatch):
    from aoi_verification.app.ui.pages.match_page import MatchPage
    mp = MatchPage()
    monkeypatch.setattr(mp, "_start_precompute", lambda: None)
    mp.load_state(queue=[], val_pool_by_slot={}, threshold=0.5, auto_mode=True)
    return mp


@pytest.mark.parametrize("msg", [
    i18n.KO.COORD_MISSING + " (x)",
    "Notch 방향을 맞추지 못했습니다",
    "예기치 못한 예외",
])
def test_coord_mode_any_failure_stops_and_returns(styled_qapp, monkeypatch, msg):
    from aoi_verification.app.ui.pages import match_page as mod
    mp = _page(monkeypatch)
    mp._coord_mode = True
    warned: list = []
    monkeypatch.setattr(mod.sheets, "warn", lambda *a, **k: warned.append(a))
    cancelled: list = []
    mp.cancelled.connect(lambda: cancelled.append(True))
    advanced: list = []
    monkeypatch.setattr(mp, "_advance", lambda: advanced.append(True))

    mp._on_precompute_failed(msg)

    assert warned and cancelled and not advanced
    mp.deleteLater()


def test_detach_keeps_running_worker_alive_until_finished(styled_qapp, monkeypatch):
    from aoi_verification.app.ui.pages import match_page as mod
    mp = _page(monkeypatch)

    class _Sig:
        def disconnect(self): pass

    class _Fin:
        def __init__(self): self.cb = None
        def connect(self, f): self.cb = f

    class _W:
        signals = type("S", (), {"progress": _Sig(), "done": _Sig(), "failed": _Sig()})()
        finished = _Fin()
        stopped = False
        def isRunning(self): return True
        def stop(self): self.stopped = True
        def wait(self, ms): return False

    w = _W()
    mp._worker = w
    mp._detach_worker()
    assert w in mod._LIVE_MATCHERS and w.stopped
    w.finished.cb()
    assert w not in mod._LIVE_MATCHERS
    mp.deleteLater()


def test_review_load_state_clears_overlay_of_unfinished_batch(styled_qapp):
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    calls: list = []
    page._loading.hide_overlay = lambda *a, **k: calls.append(True)
    page._pending_rows = [object()]          # 만들던 중
    page.load_state([])
    assert calls
    page.deleteLater()


def test_first_slot_overlay_is_busy_not_whole_run_progress(styled_qapp, monkeypatch):
    mp = _page(monkeypatch)
    mp._streaming_precompute = True
    mp._current = None
    got: list = []
    monkeypatch.setattr(mp._loading, "set_progress", lambda *a: got.append(a))
    monkeypatch.setattr(mp, "_precompute_signal_is_current", lambda: True)
    mp._on_precompute_progress(3, 40)
    assert got and got[-1][0] == 0 and got[-1][1] == 0
    mp.deleteLater()
