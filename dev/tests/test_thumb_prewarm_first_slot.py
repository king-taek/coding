"""썸네일 사전 생성은 **첫 슬롯 몫만 기다린다**.

배경(사용자 신고): 슬롯 25개·사진 1만 장 넘는 자재에서 폴더를 고르고 [검증 시작]
을 누르면 로딩이 아주 길고 "화면이 멈춘 것 같다".  원인은 대기 범위였다 —
공통 슬롯 **전부**(기준+검증)의 썸네일과 중간 이미지를 다 만든 뒤에야 화면을
내줬다.  사진 1장당 2개(썸네일·중간)라 1만 장이면 2만 번의 디코드·인코드고,
진행 수치의 분모도 1만이라 바가 한 칸씩만 움직여 멈춘 것처럼 보였다.

Stage 1 은 슬롯을 하나씩 보여 주므로(`select_page._is_single_slot_mode`) 지금
필요한 것은 첫 슬롯뿐이다.  나머지는 **풀을 멈추지 않고** 뒤에서 계속 데운다.

⚠ 사전 생성 자체를 없애면 안 된다 — 타일은 GUI 스레드에서 `cached_tile_pixmap`
→ `get_thumb_path` 로 **없으면 그 자리에서 만든다**.  기다리는 몫을 줄이는 것이지
일을 없애는 게 아니다.  그래서 '풀을 멈추지 않는다' 를 함께 못 박는다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")
pytest.importorskip("numpy")     # thumbnailer → image_io

from aoi_verification.app import i18n                              # noqa: E402
from aoi_verification.app.models.slot import (ImageItem, ScanResult,  # noqa: E402
                                              Slot)
import aoi_verification.app.ui.main_window as MW                   # noqa: E402
import aoi_verification.app.workers.thumbnailer as TN              # noqa: E402


def _slot(name: str, n_ref: int, n_val: int) -> Slot:
    return Slot(
        name=name,
        ref_images=[ImageItem(slot=name, path=Path(f"/x/{name}/r{i}.jpg"),
                              side="ref") for i in range(n_ref)],
        val_images=[ImageItem(slot=name, path=Path(f"/x/{name}/v{i}.jpg"),
                              side="val") for i in range(n_val)],
    )


class _FakeOverlay:
    def __init__(self) -> None:
        self.progress: list[tuple[int, int, str]] = []
        self.shown: list[tuple] = []

    def show_overlay(self, message="", **kw):
        self.shown.append((message, kw))

    def set_progress(self, done, total, message=""):
        self.progress.append((done, total, message))

    def hide_overlay(self, then=None):
        pass


class _FakePool:
    """`ThumbnailPool` 대역 — 우선순위별로 무엇이 들어갔는지만 기록한다."""

    instances: list["_FakePool"] = []

    def __init__(self, **kw) -> None:
        self.kw = kw
        self.enqueued: list[tuple[int, list[ImageItem]]] = []
        self.started = 0
        self.stopped = 0
        self.signals = type("S", (), {})()
        self.signals.progress = type("Sig", (), {"connect": lambda s, f: None})()
        self.signals.finished = type("Sig", (), {"connect": lambda s, f: None})()
        _FakePool.instances.append(self)

    def enqueue(self, items, *, priority=0):
        self.enqueued.append((priority, list(items)))

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _win(sr: ScanResult) -> MW.MainWindow:
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._scan = sr
    win._input = object()
    win._loading = _FakeOverlay()
    win._thumb_pool = None
    win._thumbs_handled = False
    win._thumb_wait_n = 0
    win._thumb_wait_slot = ""
    win._thumb_wait_msg = ""
    win._sizing_tier = None
    win._ready: list = []
    win._on_thumbs_ready = lambda: win._ready.append(True)
    return win


@pytest.fixture()
def fake_pool(monkeypatch):
    _FakePool.instances = []
    monkeypatch.setattr(TN, "ThumbnailPool", _FakePool)
    return _FakePool


def _run(win, sr):
    MW.MainWindow._continue_start_after_scan(win, sr.common_slot_names)
    return _FakePool.instances[-1]


def test_overlay_waits_for_the_first_slot_only(styled_qapp, fake_pool):
    """분모가 **첫 슬롯의 장수**여야 한다 — 전체 장수면 바가 멈춘 것처럼 보인다."""
    sr = ScanResult(slots={"A": _slot("A", 3, 2),
                           "B": _slot("B", 100, 100),
                           "C": _slot("C", 100, 100)},
                    ref_only=[], val_only=[])
    win = _win(sr)
    _run(win, sr)

    assert win._thumb_wait_slot == "A"
    assert win._thumb_wait_n == 5, "첫 슬롯의 기준+검증 장수여야 한다"
    last_done, last_total, _msg = win._loading.progress[-1]
    assert (last_done, last_total) == (0, 5), (
        f"오버레이 분모가 첫 슬롯 몫이 아니다: {win._loading.progress[-1]}")


def test_every_slot_is_still_enqueued_in_slot_order(styled_qapp, fake_pool):
    """기다리는 몫만 줄인다 — 뒤 슬롯도 **전부** 큐에 들어가야 한다(계속 데운다)."""
    sr = ScanResult(slots={"A": _slot("A", 2, 1),
                           "B": _slot("B", 1, 1),
                           "C": _slot("C", 1, 1),
                           "D": _slot("D", 1, 1)},
                    ref_only=[], val_only=[])
    win = _win(sr)
    pool = _run(win, sr)

    slots_in_order = [items[0].slot for _prio, items in pool.enqueued]
    assert slots_in_order == ["A", "B", "C", "D"], (
        "슬롯 순서대로 넣어야 사전 생성이 사용자보다 앞서 달린다")
    assert sum(len(items) for _p, items in pool.enqueued) == 9   # 3+2+2+2
    assert pool.started == 1


def test_first_two_slots_get_priority(styled_qapp, fake_pool):
    """첫 슬롯 = 지금 보여 줄 것, 둘째 = look-ahead, 나머지 = 배경."""
    sr = ScanResult(slots={"A": _slot("A", 1, 1),
                           "B": _slot("B", 1, 1),
                           "C": _slot("C", 1, 1)},
                    ref_only=[], val_only=[])
    win = _win(sr)
    pool = _run(win, sr)

    assert [p for p, _items in pool.enqueued] == [
        TN.PRIORITY_ACTIVE_SLOT, TN.PRIORITY_NEXT_SLOT, TN.PRIORITY_BACKGROUND,
    ]


def test_proceeds_when_the_first_slot_is_done_and_pool_keeps_running(
        styled_qapp, fake_pool):
    """첫 슬롯 몫을 채우면 곧바로 다음 단계 — 그리고 풀은 **멈추지 않는다**."""
    sr = ScanResult(slots={"A": _slot("A", 2, 1),
                           "B": _slot("B", 50, 50)},
                    ref_only=[], val_only=[])
    win = _win(sr)
    pool = _run(win, sr)

    MW.MainWindow._on_thumb_progress(win, 2, 103, "/x/A/r1.jpg")
    assert win._ready == [], "첫 슬롯이 아직 안 끝났는데 넘어갔다"

    MW.MainWindow._on_thumb_progress(win, 3, 103, "/x/A/v0.jpg")
    assert win._ready == [True], "첫 슬롯이 끝났는데 넘어가지 않았다"
    assert pool.stopped == 0, (
        "뒤 슬롯을 데우던 풀을 멈췄다 — Stage 1 이 GUI 스레드에서 썸네일을 만든다")


def test_progress_after_handoff_does_not_touch_the_overlay(styled_qapp,
                                                           fake_pool):
    """넘어간 뒤에도 신호는 계속 온다 — 그때는 아무것도 하지 않아야 한다."""
    sr = ScanResult(slots={"A": _slot("A", 1, 1), "B": _slot("B", 50, 50)},
                    ref_only=[], val_only=[])
    win = _win(sr)
    _run(win, sr)
    win._thumbs_handled = True
    before = len(win._loading.progress)

    MW.MainWindow._on_thumb_progress(win, 40, 102, "/x/B/r7.jpg")

    assert len(win._loading.progress) == before, (
        "오버레이가 내려간 뒤에도 진행 갱신이 돌았다")


def test_message_names_the_slot_being_prepared(styled_qapp, fake_pool):
    """'무슨 로딩이 도는 중인지' 가 문구에 있어야 한다 — 슬롯명 + 남은 슬롯 수."""
    sr = ScanResult(slots={"A": _slot("A", 1, 1),
                           "B": _slot("B", 1, 1),
                           "C": _slot("C", 1, 1)},
                    ref_only=[], val_only=[])
    win = _win(sr)
    _run(win, sr)

    assert "A" in win._thumb_wait_msg
    assert "2" in win._thumb_wait_msg, "남은 슬롯 수(2)가 문구에 없다"
    assert win._thumb_wait_msg == i18n.KO.LOAD_THUMBNAIL_SLOT_REST_FMT.format(
        slot="A", rest=2)


def test_single_slot_message_has_no_rest_clause(styled_qapp, fake_pool):
    """슬롯이 하나면 '나머지 0개' 같은 말이 나오면 안 된다."""
    sr = ScanResult(slots={"A": _slot("A", 1, 1)}, ref_only=[], val_only=[])
    win = _win(sr)
    _run(win, sr)

    assert win._thumb_wait_msg == i18n.KO.LOAD_THUMBNAIL_SLOT_FMT.format(slot="A")


# ---------------------------------------------------------------------------
# 진행 신호 솎아내기 — 풀이 사용자와 **동시에** 돌게 됐으므로 새로 필요해졌다.
# ---------------------------------------------------------------------------
def test_progress_signals_are_throttled_but_the_last_one_always_lands():
    """1만 장이면 신호 1만 개가 GUI 큐로 들어간다 — 눈이 읽는 건 초당 몇 개뿐이다."""
    pool = TN.ThumbnailPool()
    seen: list[tuple[int, int]] = []
    pool.signals.progress.connect(lambda d, t, _p: seen.append((d, t)))
    items = [ImageItem(slot="A", path=Path(f"/x/A/{i}.jpg"), side="ref")
             for i in range(200)]
    pool.enqueue(items)

    for it in items:
        pool._on_worker_progress(it, True, "")

    assert len(seen) < 20, f"솎아내지 않았다: {len(seen)}회"
    assert seen[-1] == (200, 200), "마지막 완료 보고가 빠졌다"
