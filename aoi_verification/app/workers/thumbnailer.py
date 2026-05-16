"""썸네일 + 중간 이미지 사전 생성 워커.

원본 폴더 스캔이 끝난 직후 호출되어 모든 이미지의 캐시를 미리 만든다.

설계:
- ``ThumbnailWorker(QThread)`` — 호환용 단일 스레드 (기존 호출자 유지).
- ``ThumbnailPool(QObject)`` — 다중 worker QThread 가 공유 heapq 에서 작업을
  꺼내 처리하는 우선순위 풀. 사용자가 보고 있는 슬롯의 작업을 우선 처리해서
  ‘첫 슬롯 준비되는 즉시 Stage 1 진입’ 을 가능하게 한다.

두 클래스 모두 같은 ``ThumbnailerSignals`` 인터페이스를 노출한다.
"""

from __future__ import annotations

import heapq
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
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


class ThumbnailerSignals(QObject):
    progress = pyqtSignal(int, int, str)   # done, total, current path
    finished = pyqtSignal()
    failed = pyqtSignal(str)               # error message
    item_ready = pyqtSignal(object)        # ImageItem (캐시 완료)


# ---------------------------------------------------------------------------
# 호환용 단일 스레드 워커 (외부 호출자는 변경 없이 사용 가능)
# ---------------------------------------------------------------------------
class ThumbnailWorker(QThread):
    """모든 ImageItem 에 대해 썸네일+중간 이미지를 생성하는 QThread.

    ``tier`` 가 주어지면 해당 화질 티어로 캐시. 미지정 시 기본 (200/800).
    """

    def __init__(self,
                 items: Iterable[ImageItem],
                 also_mid: bool = True,
                 *,
                 tier: Optional[config.SizingTier] = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: list[ImageItem] = list(items)
        self._also_mid = also_mid
        self._tier = tier
        self._stop = False
        self.signals = ThumbnailerSignals()

    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        total = len(self._items)
        if total == 0:
            self.signals.finished.emit()
            return

        for idx, item in enumerate(self._items, start=1):
            if self._stop:
                break
            try:
                image_io.get_thumb_path(item.path, tier=self._tier)
                if self._also_mid:
                    image_io.get_mid_path(item.path, tier=self._tier)
            except Exception as exc:
                # 단일 파일 실패는 무시 (로그만 emit)
                self.signals.failed.emit(f"{item.path}: {exc}")
            self.signals.progress.emit(idx, total, str(item.path))

        self.signals.finished.emit()


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

    def done_count(self) -> int:
        return self._done

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
        for w in self._workers:
            w.wait(msec)

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
