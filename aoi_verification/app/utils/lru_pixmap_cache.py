"""바이트 단위 한도가 있는 LRU QPixmap 캐시.

위젯이 매번 디스크에서 픽스맵을 새로 로드하지 않도록 메모리에 두지만,
총 바이트가 한도를 넘으면 가장 오래된 항목부터 evict 한다.

키는 임의의 hashable (``(path, size_tier)`` 같은 튜플 권장).
값은 QPixmap 인스턴스. 픽스맵의 ``byteCount()`` 로 메모리 추정치를 더한다.

GUI 스레드에서만 호출되는 것을 전제로 한다 — QPixmap 는 main-thread only.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Hashable, Iterator, Optional, Tuple

from PyQt6.QtGui import QPixmap


# QPixmap.byteCount 는 PyQt6 에서 존재하지만, 일부 버전에서는 cacheKey 만
# 노출되거나 size*depth 로 추정해야 한다. 안전한 추정자.
def _estimate_bytes(pix: QPixmap) -> int:
    try:
        # PyQt6 의 QImage.sizeInBytes() 가 가장 정확하지만, QPixmap 에선
        # 4 * width * height 가 좋은 근사값 (32bpp 가정).
        return int(pix.width()) * int(pix.height()) * 4
    except Exception:
        return 0


class LRUPixmapCache:
    """바이트 한도 LRU 픽스맵 캐시."""

    def __init__(self, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        self._max_bytes = int(max_bytes)
        self._store: "OrderedDict[Hashable, Tuple[QPixmap, int]]" = OrderedDict()
        self._total = 0

    # ------------------------------------------------------------------
    def get(self, key: Hashable) -> Optional[QPixmap]:
        try:
            pix, _size = self._store.pop(key)
        except KeyError:
            return None
        self._store[key] = (pix, _size)            # MRU 위치로 다시
        return pix

    def __contains__(self, key: Hashable) -> bool:
        return key in self._store

    def __len__(self) -> int:
        return len(self._store)

    def keys(self) -> Iterator[Hashable]:
        return iter(list(self._store.keys()))

    def total_bytes(self) -> int:
        return self._total

    def max_bytes(self) -> int:
        return self._max_bytes

    # ------------------------------------------------------------------
    def put(self, key: Hashable, pixmap: QPixmap) -> None:
        """추가 또는 갱신. 한도 초과 시 LRU 부터 evict."""
        size = _estimate_bytes(pixmap)
        # 기존 항목 갱신이면 옛 사이즈를 우선 빼고 다시 더한다.
        if key in self._store:
            _old, old_size = self._store.pop(key)
            self._total -= old_size
        self._store[key] = (pixmap, size)
        self._total += size
        self._evict_if_needed()

    def discard(self, key: Hashable) -> None:
        if key not in self._store:
            return
        _pix, size = self._store.pop(key)
        self._total -= size

    def clear(self) -> None:
        self._store.clear()
        self._total = 0

    # ------------------------------------------------------------------
    def _evict_if_needed(self) -> None:
        while self._total > self._max_bytes and self._store:
            _k, (_pix, size) = self._store.popitem(last=False)        # LRU
            self._total -= size

    def evict_until(self, target_bytes: int) -> int:
        """``target_bytes`` 까지 낮춘다. evict 된 항목 수 반환."""
        n = 0
        while self._total > max(0, int(target_bytes)) and self._store:
            _k, (_pix, size) = self._store.popitem(last=False)
            self._total -= size
            n += 1
        return n

    def set_max_bytes(self, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be > 0")
        self._max_bytes = int(max_bytes)
        self._evict_if_needed()
