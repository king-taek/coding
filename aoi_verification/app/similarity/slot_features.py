"""Slot 단위 in-RAM 특징 캐시.

Stage 2 에서 한 슬롯의 모든 검증측 이미지 ``Feature`` 객체를 한 번만 추출하고,
같은 슬롯의 여러 reference 가 매칭될 때 디스크 재로드 없이 그대로 재사용한다.

설계 원칙:
- **per-image 디스크 캐시 (``feature_cache_dir`` 의 .npz) 는 그대로 사용**.
  이 모듈은 그 위에 ‘얼마 동안 RAM 에 들고 있을지’ 를 결정하는 매니저일 뿐이다.
- 메모리 규율을 위해 ‘활성 슬롯 1 개’ + 옵션으로 ‘미리 로드해둘 다음 슬롯 1 개’
  만 유지. 슬롯 변경 시 이전 슬롯의 dict 를 명시적으로 비워 RAM 을 빠르게
  돌려준다.
- thread-safe: ``threading.Lock`` 으로 보호.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..models.slot import ImageItem
from . import pipeline as _pipeline
from .pipeline import Feature


class SlotFeatureCache:
    """슬롯명 → ``{Path: Feature}`` 매핑. ``set_active`` 로 활성 슬롯만 유지."""

    def __init__(self, *, keep_lookahead: bool = True) -> None:
        self._lock = threading.Lock()
        self._slots: Dict[str, Dict[Path, Feature]] = {}
        self._active: Optional[str] = None
        self._lookahead: Optional[str] = None
        self._keep_lookahead = bool(keep_lookahead)

    # ------------------------------------------------------------------
    def active_slot(self) -> Optional[str]:
        return self._active

    def has(self, slot: str) -> bool:
        with self._lock:
            return slot in self._slots

    def get_features(self, slot: str) -> Optional[Dict[Path, Feature]]:
        with self._lock:
            d = self._slots.get(slot)
            return None if d is None else dict(d)

    def size(self) -> int:
        with self._lock:
            return sum(len(d) for d in self._slots.values())

    # ------------------------------------------------------------------
    def set_active(self, slot: str) -> None:
        """``slot`` 을 활성으로 표시. 활성 + (옵션) lookahead 외의 슬롯은 제거."""
        with self._lock:
            self._active = slot
            keep = {slot}
            if self._keep_lookahead and self._lookahead:
                keep.add(self._lookahead)
            for k in list(self._slots.keys()):
                if k not in keep:
                    del self._slots[k]

    def set_lookahead(self, slot: Optional[str]) -> None:
        """다음에 진입할 가능성이 높은 슬롯을 표시. 활성/lookahead 외 슬롯 제거."""
        with self._lock:
            self._lookahead = slot
            keep = {self._active or "", slot or ""}
            for k in list(self._slots.keys()):
                if k not in keep:
                    del self._slots[k]

    # ------------------------------------------------------------------
    def build(self, slot: str, items: Iterable[ImageItem]) -> Dict[Path, Feature]:
        """슬롯의 ``Feature`` 들을 추출(또는 캐시 로드) 해서 dict 로 반환·저장.

        이미 빌드된 슬롯은 그대로 반환한다 (idempotent). 항목이 추가됐다면
        새 path 만 추가 추출한다.
        """
        items_list = list(items)
        existing: Dict[Path, Feature] = {}
        with self._lock:
            existing = dict(self._slots.get(slot, {}))

        # 누락된 path 만 새로 추출 (디스크 캐시가 있다면 거의 무비용).
        to_build = [it.path for it in items_list if it.path not in existing]
        for p in to_build:
            try:
                feat = _pipeline.extract(p)
                existing[p] = feat
            except Exception:
                # 단일 이미지 실패는 무시 — 호출자가 빈 dict 로 처리.
                pass

        with self._lock:
            self._slots[slot] = existing
            # 만약 set_active 가 아직 호출되지 않았으면 이 슬롯을 활성으로 간주.
            if self._active is None:
                self._active = slot
        return dict(existing)

    # ------------------------------------------------------------------
    def clear(self) -> None:
        with self._lock:
            self._slots.clear()
            self._active = None
            self._lookahead = None

    def known_slots(self) -> List[str]:
        with self._lock:
            return list(self._slots.keys())
