"""좌표 매칭 — 검증 사진 썸네일을 시작 때 기다리지 않는다(사용자 결정 2026-10-26).

썸네일은 화면 전용이다.  좌표 매칭은 파일명·INI·.001 의 좌표만 읽으므로 썸네일을 언제
만들든 매칭 결과는 같다 — 그 사실도 여기서 못 박는다(`coord_matcher` 는 썸네일러를 모른다).
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from aoi_verification.app.models.result import MatchResult
from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers import thumb_plan

ROOT = Path(__file__).resolve().parents[2]


def _it(slot, p, side):
    return ImageItem(slot=slot, path=Path(p), side=side)


def _slots():
    return {
        "S1": SimpleNamespace(
            ref_images=[_it("S1", "/r/1.jpg", "ref")],
            val_images=[_it("S1", f"/v/{n}.jpg", "val") for n in (1, 2, 3)]),
        "S2": SimpleNamespace(
            ref_images=[_it("S2", "/r/9.jpg", "ref")],
            val_images=[_it("S2", "/v/9.jpg", "val")]),
    }


# ---------------------------------------------------------------------------
# 순수 로직
# ---------------------------------------------------------------------------
def test_startup_waits_for_ref_only_when_lazy():
    got = thumb_plan.startup_items(_slots(), ["S1", "S2"], lazy_val=True)
    assert [it.side for it in got] == ["ref", "ref"]


def test_startup_keeps_everything_when_not_lazy():
    """이미지 매칭은 지금 그대로 — 기준·검증 전부(사용자 결정)."""
    got = thumb_plan.startup_items(_slots(), ["S1", "S2"], lazy_val=False)
    assert len(got) == 2 + 4
    assert [it.path.name for it in got[:4]] == ["1.jpg", "1.jpg", "2.jpg", "3.jpg"]


def test_candidate_items_are_that_slot_only_in_pool_order_without_dupes():
    results = {
        ("S1", Path("/r/1.jpg")): [(Path("/v/3.jpg"), 0.9), (Path("/v/1.jpg"), -1.5)],
        ("S1", Path("/r/2.jpg")): [(Path("/v/3.jpg"), 0.8)],
        ("S2", Path("/r/9.jpg")): [(Path("/v/9.jpg"), 0.9)],
        ("S1", Path("/r/4.jpg")): [],                       # 매치 실패 — 빈 목록
    }
    got = thumb_plan.candidate_items(results, "S1", _slots()["S1"].val_images)
    assert [it.path.name for it in got] == ["1.jpg", "3.jpg"]


def test_cross_b_prefetch_skips_a_matched_vals():
    val_by_slot = {n: s.val_images for n, s in _slots().items()}
    a = [MatchResult(slot="S1", ref_path=Path("/r/1.jpg"),
                     val_path=Path("/v/2.jpg"), score=0.9)]
    got = thumb_plan.cross_b_prefetch_items(val_by_slot, a)
    assert [(it.slot, it.path.name) for it in got] == [
        ("S1", "1.jpg"), ("S1", "3.jpg"), ("S2", "9.jpg")]


def test_coord_matcher_does_not_touch_thumbnails():
    """좌표 매칭 결과가 썸네일 시점과 무관하다는 근거 — 매처가 썸네일을 부르지 않는다."""
    src = (ROOT / "aoi_verification/app/workers/coord_matcher.py").read_text(encoding="utf-8")
    for name in ("thumbnailer", "get_thumb_path", "get_mid_path", "thumb_plan"):
        assert name not in src, f"coord_matcher 가 {name} 을 쓴다 — 썸네일 시점이 결과에 섞인다"


# ---------------------------------------------------------------------------
# ThumbnailPool — 재시작 · 저부하
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_io(monkeypatch):
    pytest.importorskip("PyQt6.QtCore")
    from aoi_verification.app.utils import image_io
    made: list[str] = []
    lock = threading.Lock()
    gate = threading.Event()
    gate.set()

    def _thumb(path, tier=None):
        gate.wait(5)
        with lock:
            made.append(Path(path).name)
        return Path(path)

    monkeypatch.setattr(image_io, "get_thumb_path", _thumb)
    monkeypatch.setattr(image_io, "get_mid_path", lambda p, tier=None: Path(p))
    return SimpleNamespace(made=made, gate=gate)


def test_pool_restarts_for_work_enqueued_after_it_went_idle(fake_io):
    from aoi_verification.app.workers.thumbnailer import ThumbnailPool
    pool = ThumbnailPool(num_workers=2)
    pool.enqueue([_it("S1", "/v/a.jpg", "val"), _it("S1", "/v/b.jpg", "val")])
    pool.start()
    pool.wait(5000)
    assert pool.is_done() and pool._alive == 0
    # 후보 사진은 슬롯마다 나중에 들어온다 — 끝난 풀이 그 일을 놓치면 안 된다.
    pool.enqueue([_it("S2", "/v/c.jpg", "val")])
    pool.start()
    pool.wait(5000)
    assert sorted(fake_io.made) == ["a.jpg", "b.jpg", "c.jpg"]
    assert pool.is_done()


def test_low_priority_pool_runs_one_lowest_priority_worker(fake_io):
    """교차검증 2차 대비 — '컴퓨터 부하가 심하지 않게'(사용자 결정): 일꾼 1명·최저 우선순위."""
    from PyQt6.QtCore import QThread
    from aoi_verification.app.workers.thumbnailer import ThumbnailPool
    fake_io.gate.clear()
    pool = ThumbnailPool(num_workers=1, low_priority=True)
    pool.enqueue([_it("S1", f"/v/{n}.jpg", "val") for n in range(3)])
    pool.start()
    pool.start()                       # 두 번 불러도 일꾼이 늘지 않는다
    try:
        assert len(pool._workers) == 1
        assert pool._workers[0].priority() == QThread.Priority.LowestPriority
    finally:
        fake_io.gate.set()
        pool.wait(5000)
    assert len(fake_io.made) == 3


def test_stopped_pool_does_not_restart(fake_io):
    from aoi_verification.app.workers.thumbnailer import ThumbnailPool
    pool = ThumbnailPool(num_workers=1)
    pool.stop()
    pool.enqueue([_it("S1", "/v/a.jpg", "val")])
    pool.start()
    assert pool._workers == [] and fake_io.made == []


# ---------------------------------------------------------------------------
# 메인 창 배선 (Qt 대역)
# ---------------------------------------------------------------------------
class _PoolStub:
    def __init__(self, **kw):
        self.kw = kw
        self.items: list = []
        self.started = 0
        self.stopped = False
        self.signals = SimpleNamespace(
            progress=SimpleNamespace(connect=lambda f: None),
            finished=SimpleNamespace(connect=lambda f: setattr(self, "on_finished", f)))

    def enqueue(self, items, priority=3):
        self.items.extend(items)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped = True


def _window(lazy):
    import aoi_verification.app.ui.main_window as MW
    from aoi_verification.app.utils.prefs import AutomationLevel
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._reset_cross_state()
    win._cross_stage = "A"
    win._lazy_val_thumbs = lazy
    win._input = SimpleNamespace(mode="cross", extract=False,
                                 automation_level=AutomationLevel.USER_SELECT)
    slots = _slots()
    win._scan = SimpleNamespace(slots=slots, common_slot_names=["S1", "S2"])
    win._matches_a = [MatchResult(slot="S1", ref_path=Path("/r/1.jpg"),
                                  val_path=Path("/v/2.jpg"), score=0.9)]
    win._reviewed_matches = list(win._matches_a)
    win._a_reviewed = True
    win._pools = []

    def _new_pool(**kw):
        p = _PoolStub(**kw)
        win._pools.append(p)
        return p

    win._new_thumb_pool = _new_pool
    win._loading = SimpleNamespace(show_overlay=lambda *a, **k: None,
                                   set_progress=lambda *a, **k: None,
                                   hide_overlay=lambda: win._hidden.append(True))
    win._hidden = []
    win._match_page = SimpleNamespace(review_context=lambda: SimpleNamespace(
        fast_results={}, coord_classical_refs=frozenset()))
    win._select_loaded = []
    win._select_page = SimpleNamespace(
        set_extract_mode=lambda on: None, set_reverse=lambda on: None,
        load_state=lambda **kw: win._select_loaded.append(kw))
    win._show_page = lambda w, **kw: None
    return MW, win


def test_cross_b_prefetch_is_one_low_priority_worker(styled_qapp):
    MW, win = _window(lazy=True)
    MW.MainWindow._start_cross_b_prefetch(win)
    MW.MainWindow._start_cross_b_prefetch(win)      # finished 가 두 번 와도 한 번만
    (pool,) = win._pools
    assert pool.kw == {"num_workers": 1, "low_priority": True}
    assert [it.path.name for it in pool.items] == ["1.jpg", "3.jpg", "9.jpg"]


def test_no_cross_b_prefetch_for_image_matching(styled_qapp):
    MW, win = _window(lazy=False)
    MW.MainWindow._start_cross_b_prefetch(win)
    assert win._pools == []


def test_entering_b_finishes_remaining_thumbs_at_full_speed_then_selects(styled_qapp):
    """사용자 결정 '가' — 남은 것만 전속력으로 마저 만든 뒤 2차 선별에 들어간다."""
    MW, win = _window(lazy=True)
    MW.MainWindow._start_cross_b_prefetch(win)
    MW.MainWindow._enter_cross_b(win)
    low, full = win._pools
    assert low.stopped, "저부하 풀이 2차 진입 뒤에도 계속 돌면 같은 사진을 두 풀이 만든다"
    assert full.kw == {} and full.started == 1
    assert [it.path.name for it in full.items] == ["1.jpg", "3.jpg", "9.jpg"]
    assert win._select_loaded == [], "썸네일이 끝나기 전에 2차 선별로 들어갔다"
    MW.MainWindow._show_cross_b_select(win)          # finished → 한 틱 뒤 호출되는 그것
    assert [it.path.name for it in win._select_loaded[0]["queue"]] == ["1.jpg", "3.jpg", "9.jpg"]
    assert win._phase == MW.PHASE_B_SELECT and win._hidden == [True]
    MW.MainWindow._show_cross_b_select(win)          # 늦은 신호 — 두 번 들어가지 않는다
    assert len(win._select_loaded) == 1


def test_cancel_during_b_wait_goes_straight_to_select(styled_qapp):
    MW, win = _window(lazy=True)
    win._stage = ""
    MW.MainWindow._enter_cross_b(win)
    (full,) = win._pools
    MW.MainWindow._on_loading_cancel(win)
    assert full.stopped and len(win._select_loaded) == 1


def test_image_matching_enters_b_without_waiting(styled_qapp):
    MW, win = _window(lazy=False)
    MW.MainWindow._enter_cross_b(win)
    assert win._pools == [] and len(win._select_loaded) == 1


def test_candidate_pool_dedupes_and_restarts(styled_qapp):
    MW, win = _window(lazy=True)
    a, b = _it("S1", "/v/1.jpg", "val"), _it("S1", "/v/3.jpg", "val")
    MW.MainWindow._on_coord_candidates(win, "S1", [a, b])
    MW.MainWindow._on_coord_candidates(win, "S1", [b])
    MW.MainWindow._on_coord_candidates(win, "S2", [_it("S2", "/v/9.jpg", "val")])
    (pool,) = win._pools
    assert pool.kw["low_priority"] is True
    assert [it.path.name for it in pool.items] == ["1.jpg", "3.jpg", "9.jpg"]
    assert pool.started == 2


def test_continue_start_uses_thumb_plan():
    """시작 썸네일 목록은 `thumb_plan.startup_items` 한 곳에서 정한다(두 벌 금지)."""
    src = (ROOT / "aoi_verification/app/ui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_continue_start_after_scan")
    body = ast.get_source_segment(src, fn)
    assert "thumb_plan.startup_items" in body
    assert "val_images)" not in body.split("per_side_total")[0], \
        "시작 때 검증 사진을 다시 직접 넣고 있다"


def test_auto_all_b_also_finishes_thumbs_before_matching(styled_qapp):
    """올인원 2차도 기다린다 — 2차 검토 화면이 검증 사진을 곧바로 그리기 때문이다.
    안 기다리면 저부하 일꾼 1명이 만드는 동안 검토 화면이 사진마다 멈춘다."""
    from aoi_verification.app.utils.prefs import AutomationLevel
    MW, win = _window(lazy=True)
    win._input.automation_level = AutomationLevel.AUTO_ALL
    started = []
    win._start_cross_b_match = lambda q: started.append(q)
    MW.MainWindow._start_cross_b_prefetch(win)
    MW.MainWindow._enter_cross_b(win)
    low, full = win._pools
    assert low.stopped and full.started == 1 and started == []
    MW.MainWindow._show_cross_b_select(win)
    assert [it.path.name for it in started[0]] == ["1.jpg", "3.jpg", "9.jpg"]
    assert win._select_loaded == [] and win._hidden == [True]
