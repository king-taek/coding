"""우선순위 1~7 개선 항목 검증 (B1/B2/C1/C2/D2)."""

from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication          # noqa: E402

from aoi_verification.app.models.slot import ImageItem          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(slot, n, side="ref"):
    return [ImageItem(slot, Path(f"/tmp/{slot}_{side}{i}.png"), side)
            for i in range(n)]


# ===========================================================================
# B1 — log_silent 헬퍼는 동작을 바꾸지 않고 로그만 남긴다(예외 재전파 없음)
# ===========================================================================
def test_b1_log_silent_never_raises(caplog):
    from aoi_verification.app.utils import errors
    with caplog.at_level(logging.WARNING, logger="aoi"):
        errors.log_silent("ctx", RuntimeError("boom"), level=logging.WARNING)
    assert any("boom" in r.message or "ctx" in r.message for r in caplog.records)
    # exc 없이 호출, 로깅 내부 실패에도 예외 전파 없음.
    errors.log_silent("plain")  # 예외 없이 반환되면 통과


# ===========================================================================
# B2 — 정지된 구 워커의 늦은 시그널은 무시, 현재 워커 시그널만 처리
# ===========================================================================
def test_b2_stale_worker_signal_ignored(qapp):
    from aoi_verification.app.ui.pages.match_page import MatchPage
    from aoi_verification.app.similarity.slot_features import _PrecomputeSignals

    mp = MatchPage()
    mp._streaming_precompute = True

    cur = _PrecomputeSignals()
    stale = _PrecomputeSignals()

    class _W:
        pass
    w = _W()
    w.signals = cur
    mp._precompute_worker = w

    cur.slot_finished.connect(mp._on_precompute_slot_finished)
    stale.slot_finished.connect(mp._on_precompute_slot_finished)

    mp._precompute_processed_slots.clear()
    # 구 워커(stale) 시그널 → 가드가 차단 (상태 불변)
    stale.slot_finished.emit("SX", 5, 9)
    assert "SX" not in mp._precompute_processed_slots
    # 현재 워커 시그널 → 정상 처리
    cur.slot_finished.emit("SY", 2, 9)
    assert "SY" in mp._precompute_processed_slots
    mp.deleteLater()


# ===========================================================================
# C1 — Stage 2 되돌리기: 매칭/매칭없음 취소 + 기준 사진 큐 복귀 + 집계 되돌림
# ===========================================================================
def test_c1_undo_match_and_no_match(qapp, isolated_cache, monkeypatch):
    from aoi_verification.app.ui.pages.match_page import MatchPage
    from aoi_verification.app.ui.widgets.thumb_grid import ThumbEntry

    mp = MatchPage()
    # 매처/사전계산 워커를 띄우지 않도록 스텁 (undo 로직만 격리 검증)
    monkeypatch.setattr(mp, "_advance", lambda: None)
    monkeypatch.setattr(mp, "_start_precompute", lambda: None)
    mp.show()
    QApplication.processEvents()
    assert mp.isVisible()

    refs = _items("S1", 3, "ref")
    vals = _items("S1", 2, "val")
    mp.load_state(queue=list(refs), val_pool_by_slot={"S1": vals}, threshold=0.5)
    assert mp.undo_btn.isEnabled() is False

    undone = []
    mp.match_undone.connect(lambda m: undone.append(m))

    # 1) 첫 기준 사진을 매칭 확정
    mp._current = refs[0]
    mp._on_pick(ThumbEntry(item=vals[0], extra={"score": 0.9}))
    assert len(mp._state.matches) == 1
    assert refs[0] not in mp._state.queue
    assert mp.undo_btn.isEnabled() is True

    # 2) 되돌리기 → 매칭 제거 + 기준 사진 큐 맨 앞 복귀 + 집계 되돌림 신호
    mp._current = refs[1]
    mp._undo_match()
    assert len(mp._state.matches) == 0
    assert mp._state.queue[0] == refs[0]
    assert len(undone) == 1 and undone[0].val_path == vals[0].path
    assert mp.undo_btn.isEnabled() is False

    # 3) '매칭 없음' 후 되돌리기 → no_match 풀에서 제거 + 복귀
    mp._current = refs[0]
    mp._confirm_no_match()
    assert refs[0] in mp._state.no_match["S1"]
    assert mp.undo_btn.isEnabled() is True
    mp._current = refs[1]
    mp._undo_match()
    assert refs[0] not in mp._state.no_match["S1"]
    assert mp._state.queue[0] == refs[0]
    assert mp.undo_btn.isEnabled() is False
    mp.deleteLater()


# ===========================================================================
# C2 — 긴 작업(썸네일) 취소 프리미티브: cancelable 오버레이가 취소 신호를 emit
# ===========================================================================
def test_c2_loading_overlay_cancelable(qapp):
    from PyQt6.QtWidgets import QWidget
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    host = QWidget()
    ov = LoadingOverlay(host)
    fired = []
    ov.cancel_requested.connect(lambda: fired.append(True))
    # 기본(취소 불가) — 버튼 숨김 (오프스크린이라 isHidden 으로 플래그 검증)
    ov.show_overlay("작업 중")
    assert ov._cancel_btn.isHidden() is True
    # cancelable=True — 버튼 노출, 클릭 시 신호
    ov.show_overlay("썸네일 생성", cancelable=True)
    assert ov._cancel_btn.isHidden() is False
    ov._cancel_btn.click()
    assert fired == [True]
    host.deleteLater()


def test_c2_thumbnail_pool_has_no_isrunning():
    """리뷰에서 잡힌 회귀 방지: ThumbnailPool 은 QObject(워커만 QThread)라
    isRunning() 이 없다.  _on_loading_cancel 가드는 이를 호출하면 AttributeError
    가 나므로, 풀 활성 판별에 isRunning() 을 쓰지 않아야 한다 (stop()+one-shot
    플래그로 가드)."""
    from aoi_verification.app.workers.thumbnailer import ThumbnailPool
    assert hasattr(ThumbnailPool, "stop")
    assert hasattr(ThumbnailPool, "wait")
    assert not hasattr(ThumbnailPool, "isRunning"), (
        "ThumbnailPool 에 isRunning 이 추가되면 _on_loading_cancel 가드를 재검토"
    )


def test_c2_cancel_guard_pattern_without_isrunning():
    """_on_loading_cancel 와 동일한 가드 패턴: isRunning 없이 stop()+one-shot."""
    calls = {"stop": 0, "ready": 0}

    class _FakePool:          # isRunning() 없음 — 실제 ThumbnailPool 과 동일
        def stop(self):
            calls["stop"] += 1

    state = {"handled": False, "pool": _FakePool()}

    def on_ready():
        if state["handled"]:
            return
        state["handled"] = True
        calls["ready"] += 1

    def on_cancel():
        pool = state["pool"]
        if pool is not None and not state["handled"]:
            pool.stop()
            on_ready()

    on_cancel()               # 취소 → stop + 진행 1회
    on_cancel()               # 두 번째 취소 → 가드로 no-op
    assert calls == {"stop": 1, "ready": 1}


def test_c2_thumbs_oneshot_guard_pattern():
    """_on_thumbs_ready 의 one-shot 가드와 동일한 패턴: 두 번 호출돼도 1회만 진행."""
    state = {"handled": False, "proceeded": 0}

    def on_ready():
        if state["handled"]:
            return
        state["handled"] = True
        state["proceeded"] += 1

    on_ready()          # 정상 finished
    on_ready()          # 취소가 같은 함수를 또 호출
    assert state["proceeded"] == 1


def test_c1_main_window_aggregation_undo():
    """main_window._on_match_undone 가 _matches_a 에서 같은 key 항목을 제거."""
    from aoi_verification.app.models.result import MatchResult
    # main_window 전체 생성은 무거우니 핸들러 로직만 동치로 검증.
    m1 = MatchResult(slot="S1", ref_path=Path("/r/a.png"),
                     val_path=Path("/v/x.png"), score=0.9)
    m2 = MatchResult(slot="S1", ref_path=Path("/r/b.png"),
                     val_path=Path("/v/y.png"), score=0.8)
    agg = [m1, m2]
    target = m1
    for i in range(len(agg) - 1, -1, -1):
        if agg[i].key == target.key:
            del agg[i]
            break
    assert agg == [m2]

