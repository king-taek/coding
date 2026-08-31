"""썸네일 + 중간 이미지 사전 생성 워커.

원본 폴더 스캔이 끝난 직후 호출되어 모든 이미지의 캐시를 미리 만든다.

설계: ``ThumbnailPool(QObject)`` — 다중 worker QThread 가 공유 heapq 에서 작업을
꺼내 처리하는 우선순위 풀.  사용자가 보고 있는 슬롯의 작업을 우선 처리해서
'첫 슬롯 준비되는 즉시 Stage 1 진입' 을 가능하게 한다.

※ 한때 ``ThumbnailWorker(QThread)`` 라는 단일 스레드 '호환용' 클래스가 함께 있었으나,
  그 호환 대상이던 호출자가 모두 풀로 옮겨 간 뒤로 **생성되는 곳이 0곳**이라 제거했다.
"""

from __future__ import annotations

import heapq
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .. import config
from ..models.slot import ImageItem
from ..utils import image_io


# ---------------------------------------------------------------------------
# Priority classes (lower = sooner)
# ---------------------------------------------------------------------------
PRIORITY_CENTER = 0           # 현재 활성 슬롯의 ‘결정 중인 사진’ 본인
PRIORITY_ACTIVE_SLOT = 1      # 현재 활성 슬롯의 다른 사진들
PRIORITY_NEXT_SLOT = 2        # 다음 슬롯 (look-ahead)
PRIORITY_BACKGROUND = 3       # 그 외 모든 백그라운드 채우기


# 진행 신호를 올리는 최소 간격(초).  이보다 촘촘한 보고는 화면에 남는 것이 없다.
PROGRESS_MIN_GAP_S = 0.08


class _EmitGate:
    """마지막 통과로부터 ``gap`` 초가 지났을 때만 True — 여러 워커가 함께 쓴다."""

    def __init__(self, gap: float) -> None:
        self._gap = float(gap)
        self._lock = threading.Lock()
        self._last = 0.0

    def check(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._last < self._gap:
                return False
            self._last = now
            return True


class ThumbnailerSignals(QObject):
    progress = pyqtSignal(int, int, str)   # done, total, current path
    finished = pyqtSignal()
    failed = pyqtSignal(str)               # error message
    item_ready = pyqtSignal(object)        # ImageItem (캐시 완료)


# ---------------------------------------------------------------------------
# 우선순위 큐 + 멀티 워커 풀
# ---------------------------------------------------------------------------
@dataclass(order=True)
class _Job:
    """heapq 비교용 키: (priority, seq) 만 사용. 나머지는 비교에서 제외."""

    priority: int
    seq: int
    slot: str = field(compare=False)
    item: ImageItem = field(compare=False)
    also_mid: bool = field(compare=False, default=True)


class ThumbnailPool(QObject):
    """우선순위 큐 + 멀티 워커 스레드 풀.

    ``enqueue`` 로 작업을 적재하고 ``start`` 로 워커를 띄운다. 활성 슬롯이
    바뀔 때 ``reprioritize_slot(slot_name, PRIORITY_ACTIVE_SLOT)`` 으로
    재정렬해 사용자가 보는 슬롯의 작업을 먼저 끝낸다.
    """

    def __init__(self,
                 *,
                 tier: Optional[config.SizingTier] = None,
                 also_mid: bool = True,
                 num_workers: Optional[int] = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.signals = ThumbnailerSignals()
        self._tier = tier
        self._also_mid = bool(also_mid)
        cpu = os.cpu_count() or 2
        self._num_workers = int(num_workers) if num_workers else max(2, cpu - 1)

        self._heap: list[_Job] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._stop = False
        self._workers: list[_PoolWorker] = []
        self._total = 0
        self._done = 0
        # finished 시그널이 race condition 으로 두 번 emit / 한 번도 안 됨을 막기
        # 위한 단발 플래그.  _on_worker_progress 가 lock 아래서 set.
        self._finished_emitted = False
        self._progress_gap = _EmitGate(PROGRESS_MIN_GAP_S)

    # ------------------------------------------------------------------
    def enqueue(self, items: Iterable[ImageItem], *,
                priority: int = PRIORITY_BACKGROUND) -> None:
        added = 0
        with self._lock:
            for it in items:
                self._seq += 1
                heapq.heappush(self._heap, _Job(
                    priority=priority,
                    seq=self._seq,
                    slot=it.slot,
                    item=it,
                    also_mid=self._also_mid,
                ))
                added += 1
            self._total += added

    def reprioritize_slot(self, slot_name: str, new_priority: int) -> None:
        """해당 슬롯에 속한 대기 중 작업의 우선순위를 낮춰(앞으로) 재삽입."""
        with self._lock:
            kept: list[_Job] = []
            moved: list[_Job] = []
            for j in self._heap:
                if j.slot == slot_name and j.priority > new_priority:
                    self._seq += 1
                    moved.append(_Job(
                        priority=new_priority,
                        seq=self._seq,
                        slot=j.slot,
                        item=j.item,
                        also_mid=j.also_mid,
                    ))
                else:
                    kept.append(j)
            self._heap = kept + moved
            heapq.heapify(self._heap)

    def pending(self) -> int:
        with self._lock:
            return len(self._heap)

    def total(self) -> int:
        return self._total

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._workers:
            return
        for _ in range(self._num_workers):
            w = _PoolWorker(self)
            w.start()
            self._workers.append(w)

    def stop(self) -> None:
        self._stop = True

    def wait(self, msec: int = 0) -> None:
        """★ `msec` 는 **총액**이다 — 워커마다 주면 안 된다.

        예전엔 워커 수만큼 곱해져서, 12코어 PC 에서 `wait(1000)` 이 최대 11초가 됐다
        (느린 NAS 디코드에 워커들이 붙잡혀 있으면 실제로 그만큼 창이 안 닫혔다)."""
        import time
        deadline = time.monotonic() + max(0, int(msec)) / 1000.0
        for w in self._workers:
            remain = int(max(0.0, deadline - time.monotonic()) * 1000)
            w.wait(remain)

    # ------------------------------------------------------------------
    # 내부 사용 — 워커가 한 작업을 끝낼 때마다 호출
    # ------------------------------------------------------------------
    def _on_worker_progress(self, item: ImageItem, ok: bool, err: str) -> None:
        # 멀티 워커가 동시에 호출 — _done 증가와 ‘마지막 작업이냐’ 판단을 lock
        # 으로 묶지 않으면 race condition 으로 finished 가 한 번도 emit 되지
        # 않거나 두 번 emit 될 수 있다.
        with self._lock:
            self._done += 1
            done = self._done
            total = self._total
            is_finished = (done >= total and not self._finished_emitted)
            if is_finished:
                self._finished_emitted = True
        if not ok:
            self.signals.failed.emit(f"{item.path}: {err}")
        else:
            self.signals.item_ready.emit(item)
        # ★ 진행은 **간격으로 솎아** 올린다.  풀은 사용자가 화면을 쓰는 동안에도
        #   계속 도는데(첫 슬롯만 기다리고 진입한다 — main_window), 사진 1만 장이면
        #   신호 1만 개가 GUI 스레드 큐로 들어가 그만큼 라벨 갱신과 ETA 계산이 돈다.
        #   눈이 읽을 수 있는 것은 초당 수 회뿐이라 그 이상은 렉으로만 남는다.
        #   마지막 한 번은 **반드시** 올린다(완료 수치가 어긋나면 안 된다).
        if is_finished or self._progress_gap.check():
            self.signals.progress.emit(done, total, str(item.path))
        if is_finished:
            self.signals.finished.emit()


class _PoolWorker(QThread):
    """heap 에서 작업을 꺼내 처리하는 단일 워커 스레드."""

    def __init__(self, pool: ThumbnailPool) -> None:
        super().__init__()
        self._pool = pool

    def run(self) -> None:  # type: ignore[override]
        while True:
            if self._pool._stop:
                return
            with self._pool._lock:
                if not self._pool._heap:
                    return
                job = heapq.heappop(self._pool._heap)
            ok = True
            err = ""
            try:
                image_io.get_thumb_path(job.item.path, tier=self._pool._tier)
                if job.also_mid:
                    image_io.get_mid_path(job.item.path, tier=self._pool._tier)
            except Exception as exc:
                ok = False
                err = str(exc)
            self._pool._on_worker_progress(job.item, ok, err)
