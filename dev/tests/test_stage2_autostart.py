"""Stage 2 자동 매칭이 **시작·지속**되는지 — '가끔 매칭이 안 도는' 버그의 회귀 테스트.

실제 증상: Stage 2 에 들어갔는데 첫 사진만 뜨고 진행이 멈춘다(가끔).

원인은 **페이지 전환 애니메이션(≈160ms) 동안 `MatchPage.isVisible()` 이 False** 라는
사실이었다.  `main_window._show_page` 는 새 화면의 스냅샷만 띄우고 실제 위젯은
비-current 로 되돌리며, 두 진입점은 `load_state`(→ 워커 시작)를 `_show_page` 보다
**먼저** 부른다.  그 창 안에서 후보 0개인 ref 를 만나면 `_confirm_no_match` 의
`if not self.isVisible(): return` 이 자동 사슬을 조용히 끊었다.

그래서 이 파일의 테스트는 **일부러 `show()` 하지 않는다** — 그게 함정의 조건이다.
(기존 `test_improvements.test_c1_undo_match_and_no_match` 는 `mp.show()` 를 부르고
`_advance` 를 스텁해 이 함정을 정확히 비껴간다.)
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication              # noqa: E402

from aoi_verification.app.models.slot import ImageItem   # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(slot, n, side="ref"):
    return [ImageItem(slot, Path(f"/tmp/{slot}_{side}{i}.png"), side)
            for i in range(n)]


def _page(monkeypatch, *, auto=True):
    """워커·이미지 로딩 없이 진행 사슬만 돌릴 수 있는 MatchPage.

    ``_launch_matcher`` 는 호출 기록만 남긴다 — '다음 ref 로 넘어갔는가' 를
    관찰하는 지점이다."""
    from aoi_verification.app.ui.pages.match_page import MatchPage

    mp = MatchPage()
    monkeypatch.setattr(mp, "_start_precompute", lambda: None)
    monkeypatch.setattr(mp, "_show_center", lambda item: None)
    launched: list = []
    monkeypatch.setattr(mp, "_launch_matcher",
                        lambda ref, vals: launched.append(ref))
    refs = _items("S1", 3, "ref")
    vals = _items("S1", 2, "val")
    mp.load_state(queue=list(refs), val_pool_by_slot={"S1": vals},
                  threshold=0.5, auto_mode=auto)
    return mp, refs, vals, launched


# ===========================================================================
# 핵심 회귀 — 화면이 아직 안 보이는 동안에도 자동 사슬이 이어져야 한다
# ===========================================================================
def test_auto_chain_survives_while_page_is_not_visible(qapp, monkeypatch):
    mp, refs, _vals, launched = _page(monkeypatch)
    # ★ show() 하지 않는다 — 전환 애니메이션 중과 같은 상태.
    assert mp.isVisible() is False

    mp._current = refs[0]
    mp._on_matcher_done([])                 # 후보 0개인 ref
    QApplication.processEvents()            # singleShot(0) 소화

    # 수정 전: 여기서 전부 실패했다(큐 그대로, no_match 비어 있음, 다음 호출 없음).
    assert refs[0] in mp._state.no_match["S1"]
    assert refs[0] not in mp._state.queue
    assert mp._state.queue[0] is refs[1]
    assert launched == [refs[1]]            # 다음 ref 로 실제로 넘어갔다
    mp.deleteLater()


def test_user_shortcut_is_still_ignored_when_page_hidden(qapp, monkeypatch):
    """가드를 지운 게 아니라 **경로를 나눈** 것 — 사람이 누른 건 여전히 무시한다.

    'N' 단축키는 WindowShortcut 이라 다른 화면이 보일 때도 이 핸들러로 온다."""
    mp, refs, _vals, launched = _page(monkeypatch)
    assert mp.isVisible() is False
    mp._current = refs[0]

    mp._confirm_no_match()                  # user=True (기본값) = 사람이 누름
    assert mp._state.no_match["S1"] == []
    assert mp._state.queue[0] is refs[0]
    assert launched == []

    mp._skip_current()                      # 같은 규칙
    assert mp._state.skipped["S1"] == []
    mp.deleteLater()


def test_ref_without_val_pool_advances_while_hidden(qapp, monkeypatch):
    """val 이 하나도 없는 슬롯도 자동으로 '매칭 없음' 처리되고 진행해야 한다."""
    from aoi_verification.app.ui.pages.match_page import MatchPage

    mp = MatchPage()
    monkeypatch.setattr(mp, "_start_precompute", lambda: None)
    monkeypatch.setattr(mp, "_show_center", lambda item: None)
    refs = _items("S1", 2, "ref")
    mp.load_state(queue=list(refs), val_pool_by_slot={}, threshold=0.5,
                  auto_mode=True)
    assert mp.isVisible() is False

    mp._advance()
    QApplication.processEvents()

    # 두 ref 모두 val 이 없어 no_match 로 흘러가고 큐가 비어야 한다.
    assert mp._state.queue == []
    assert set(mp._state.no_match["S1"]) == set(refs)
    mp.deleteLater()


# ===========================================================================
# 매처 워커 실패도 '결과' 다 — 오버레이를 풀고 다음 ref 로
# ===========================================================================
def test_matcher_failure_releases_overlay_and_advances(qapp, monkeypatch):
    mp, refs, _vals, launched = _page(monkeypatch)
    hidden: list = []
    monkeypatch.setattr(mp._loading, "hide_overlay",
                        lambda *a, **k: hidden.append(True))
    mp._current = refs[0]

    mp._on_matcher_failed("특징 추출 실패")
    QApplication.processEvents()

    assert hidden, "실패 시 차단 오버레이가 풀려야 한다"
    assert refs[0] in mp._state.no_match["S1"]
    assert launched == [refs[1]]
    mp.deleteLater()


# ===========================================================================
# 사전계산이 '아무 일도 안 하고' 끝나는 경로에도 안전망이 있어야 한다
# ===========================================================================
def test_precompute_finished_without_first_slot_still_advances(qapp,
                                                               monkeypatch):
    """스트리밍인데 첫 slot_finished 가 끝내 오지 않은 경우(중단·예외).

    예전엔 `_waiting_for_slot` 이 None 이라 아무 것도 하지 않고 반환했다 —
    '잠시만 기다려주세요' 오버레이가 영구히 남는 경로."""
    mp, refs, _vals, launched = _page(monkeypatch)
    mp._streaming_precompute = True
    mp._waiting_for_slot = None
    mp._current = None
    mp._precompute_worker = None

    mp._on_precompute_finished()
    QApplication.processEvents()

    assert launched == [refs[0]]
    mp.deleteLater()


def test_precompute_failed_with_empty_queue_emits_finished(qapp, monkeypatch):
    """큐도 비고 현재 ref 도 없으면 상위(main_window)에 '끝'을 알려야 한다.

    예전엔 조용히 반환해 Stage 2 에 갇혔다."""
    from aoi_verification.app.ui.pages.match_page import MatchPage

    mp = MatchPage()
    monkeypatch.setattr(mp, "_start_precompute", lambda: None)
    monkeypatch.setattr(mp, "_show_center", lambda item: None)
    mp.load_state(queue=[], val_pool_by_slot={}, threshold=0.5, auto_mode=True)
    done: list = []
    mp.finished.connect(lambda: done.append(True))
    mp._current = None

    mp._on_precompute_failed("네트워크 오류")
    QApplication.processEvents()

    assert done, "빈 큐로 실패하면 finished 를 내야 한다"
    mp.deleteLater()


# ===========================================================================
# 중단 후 재실행 — 계산 결과를 물려받지 않는다(정확도)
# ===========================================================================
def test_cancel_clears_caches_so_rerun_recomputes(qapp, monkeypatch):
    mp, refs, vals, _launched = _page(monkeypatch)
    mp._score_cache.put("S1", refs[0].path, vals[0].path, 0.9)
    assert mp._score_cache.has_pair("S1", refs[0].path, vals[0].path)

    mp._on_cancel_requested()

    assert mp._score_cache.size() == 0
    assert mp._slot_cache.size() == 0
    assert mp._candidates == []
    assert mp._precompute_worker is None
    assert mp._worker is None
    mp.deleteLater()


# ===========================================================================
# CoordScheduler 신호 계약 — 어떤 경로로 끝나도 finished/failed 중 정확히 하나
# ===========================================================================
def test_coord_scheduler_always_terminates_with_one_signal(qapp):
    _coord = pytest.importorskip(
        "aoi_verification.app.workers.coord_matcher")

    for tasks in ([], [("S1", [], [])]):
        sch = _coord.CoordScheduler(tasks)
        seen: list = []
        sch.signals.finished.connect(lambda: seen.append("finished"))
        sch.signals.failed.connect(lambda m: seen.append("failed"))
        sch.run()                       # 스레드 없이 동기 실행
        assert len(seen) == 1, f"tasks={tasks!r} → {seen!r}"


def test_coord_scheduler_reports_failure_instead_of_hanging(qapp,
                                                            monkeypatch):
    """_run 안에서 예외가 나도 반드시 failed 로 빠져나온다."""
    _coord = pytest.importorskip(
        "aoi_verification.app.workers.coord_matcher")

    sch = _coord.CoordScheduler([("S1", _items("S1", 1), _items("S1", 1, "val"))])
    monkeypatch.setattr(sch, "_run",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    seen: list = []
    sch.signals.finished.connect(lambda: seen.append("finished"))
    sch.signals.failed.connect(lambda m: seen.append(("failed", m)))
    sch.run()
    assert len(seen) == 1 and seen[0][0] == "failed"
