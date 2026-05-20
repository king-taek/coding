"""기준 사진 1장 vs 같은 슬롯의 검증 후보 N장 — 유사도 정렬 워커.

``val_features`` 가 주어지면 디스크에서 ``Feature`` 를 다시 읽지 않고 사용한다
(slot_features.SlotFeatureCache 와 함께 쓰면 같은 슬롯의 reference 들끼리
val side 특징을 공유해 큰 폭으로 속도가 빨라진다).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..config import CONFIG
from ..models.slot import ImageItem
from ..similarity import pipeline as sim


@dataclass
class Candidate:
    item: ImageItem
    score: float


class MatcherSignals(QObject):
    done = pyqtSignal(list)            # list[Candidate]
    progress = pyqtSignal(int, int)
    failed = pyqtSignal(str)


class MatcherWorker(QThread):
    """단일 기준 이미지에 대해 후보들의 score 를 계산해 정렬해 돌려준다.

    ``slot_cache`` 가 주어지면 워커 thread 가 해당 슬롯의 모든 val Feature 를
    한 번 빌드해 캐시에 저장한다. 같은 슬롯의 다음 reference 부터는 캐시
    히트로 디스크 재로드가 사라진다.
    """

    def __init__(self,
                 ref_item: ImageItem,
                 val_items: Iterable[ImageItem],
                 threshold: Optional[float] = None,
                 *,
                 val_features: Optional[Mapping[Path, sim.Feature]] = None,
                 slot_cache: Optional[object] = None,
                 cfg=None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ref = ref_item
        self._vals: list[ImageItem] = list(val_items)
        self._threshold = threshold if threshold is not None else CONFIG.default_threshold
        # 호출자가 미리 빌드한 val Feature 캐시 (Optional). 누락된 path 는 폴백으로 sim.extract.
        self._val_features: dict[Path, sim.Feature] = dict(val_features or {})
        self._slot_cache = slot_cache
        self._cfg = cfg                 # 강화/KLA 전처리 설정
        self.signals = MatcherSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        try:
            ref_feat = sim.extract(self._ref.path, cfg=self._cfg)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return

        # 슬롯 캐시에 미적재면 워커 thread 에서 한 번 빌드 (GUI 미블록).
        if self._slot_cache is not None and not self._val_features:
            try:
                self._val_features = self._slot_cache.build(
                    self._ref.slot, self._vals, cfg=self._cfg,
                )
            except Exception:
                self._val_features = {}

        total = len(self._vals)
        out: list[Candidate] = []
        for idx, vi in enumerate(self._vals, start=1):
            if self._stop:
                break
            try:
                vf = self._val_features.get(vi.path)
                if vf is None:
                    vf = sim.extract(vi.path, cfg=self._cfg)
                    # 다음 reference 를 위해 인메모리에 추가.
                    self._val_features[vi.path] = vf
                s = sim.score(ref_feat, vf)
            except Exception as exc:
                self.signals.failed.emit(f"{vi.path}: {exc}")
                s = 0.0
            if s >= self._threshold:
                out.append(Candidate(item=vi, score=s))
            self.signals.progress.emit(idx, total)

        out.sort(key=lambda c: c.score, reverse=True)
        self.signals.done.emit(out)
