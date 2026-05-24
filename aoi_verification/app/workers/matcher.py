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


def score_ref_classical(ref_item: ImageItem,
                        val_items: list[ImageItem],
                        *,
                        threshold: float,
                        cfg=None,
                        val_features: Optional[Mapping[Path, sim.Feature]] = None,
                        progress_cb=None,
                        stop_cb=None) -> list[Candidate]:
    """기준 1장 vs 후보 N장을 고전 파이프라인(pHash+ORB+SSIM)으로 채점.

    ``MatcherWorker`` 와 고효율 모드 CPU 유닛이 공유하는 순수 함수 (Qt 비의존).
    점수는 ``sim.score`` 가 이미 [0,1] 로 돌려준다.  ``val_features`` 가 주어지면
    재추출을 생략한다.  ``progress_cb(idx,total)`` / ``stop_cb()->bool`` 은 옵션.
    """
    ref_feat = sim.extract(ref_item.path, cfg=cfg, side="ref")
    vfmap: dict[Path, sim.Feature] = dict(val_features or {})
    total = len(val_items)
    out: list[Candidate] = []
    for idx, vi in enumerate(val_items, start=1):
        if stop_cb is not None and stop_cb():
            break
        try:
            vf = vfmap.get(vi.path)
            if vf is None:
                vf = sim.extract(vi.path, cfg=cfg, side="val")
                vfmap[vi.path] = vf
            s = sim.score(ref_feat, vf)
        except Exception:
            s = 0.0
        if s >= threshold:
            out.append(Candidate(item=vi, score=s))
        if progress_cb is not None:
            progress_cb(idx, total)
    out.sort(key=lambda c: c.score, reverse=True)
    return out


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
        # 슬롯 캐시에 미적재면 워커 thread 에서 한 번 빌드 (GUI 미블록).
        if self._slot_cache is not None and not self._val_features:
            try:
                self._val_features = self._slot_cache.build(
                    self._ref.slot, self._vals, cfg=self._cfg,
                )
            except Exception:
                self._val_features = {}

        try:
            out = score_ref_classical(
                self._ref, self._vals,
                threshold=self._threshold, cfg=self._cfg,
                val_features=self._val_features,
                progress_cb=lambda idx, total: self.signals.progress.emit(idx, total),
                stop_cb=lambda: self._stop,
            )
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(out)
