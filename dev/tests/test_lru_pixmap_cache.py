"""LRUPixmapCache — 바이트 한도 / LRU evict / discard / clear."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtGui")
from PyQt6.QtGui import QPixmap                                # noqa: E402
from PyQt6.QtWidgets import QApplication                       # noqa: E402

from aoi_verification.app.utils.lru_pixmap_cache import (      # noqa: E402
    LRUPixmapCache, _estimate_bytes,
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_pix(qapp, size: int) -> QPixmap:
    p = QPixmap(size, size)
    p.fill()
    return p


def test_put_get_roundtrip(qapp):
    c = LRUPixmapCache(max_bytes=10 * 1024 * 1024)
    pix = _make_pix(qapp, 100)
    c.put("a", pix)
    assert c.get("a") is pix
    assert "a" in c
    assert len(c) == 1


def test_get_missing_returns_none(qapp):
    c = LRUPixmapCache(max_bytes=10 * 1024 * 1024)
    assert c.get("missing") is None


def test_lru_evicts_oldest_first(qapp):
    # 30×30 ≈ 3.6 KB. 한도 8 KB 면 두 개까지 들어가고 세 번째에 evict.
    c = LRUPixmapCache(max_bytes=8 * 1024)
    c.put("a", _make_pix(qapp, 30))
    c.put("b", _make_pix(qapp, 30))
    # 'a' 가 가장 오래된 상태. 'c' 를 추가 → 'a' evict.
    c.put("c", _make_pix(qapp, 30))
    assert "a" not in c
    assert "b" in c and "c" in c


def test_get_promotes_to_mru(qapp):
    c = LRUPixmapCache(max_bytes=8 * 1024)
    c.put("a", _make_pix(qapp, 30))
    c.put("b", _make_pix(qapp, 30))
    # 'a' 접근 → MRU. 'c' 추가 시 'b' 가 evict 되어야 함.
    _ = c.get("a")
    c.put("c", _make_pix(qapp, 30))
    assert "a" in c
    assert "b" not in c
    assert "c" in c


def test_discard_and_clear(qapp):
    c = LRUPixmapCache(max_bytes=10 * 1024 * 1024)
    c.put("a", _make_pix(qapp, 50))
    c.put("b", _make_pix(qapp, 50))
    c.discard("a")
    assert "a" not in c
    assert "b" in c
    c.clear()
    assert len(c) == 0
    assert c.total_bytes() == 0


def test_set_max_bytes_evicts(qapp):
    c = LRUPixmapCache(max_bytes=10 * 1024 * 1024)
    for k in range(5):
        c.put(k, _make_pix(qapp, 100))
    assert len(c) == 5
    # 한도를 줄이면 LRU 부터 evict.
    c.set_max_bytes(80 * 1024)
    assert len(c) < 5


def test_evict_until_target(qapp):
    c = LRUPixmapCache(max_bytes=10 * 1024 * 1024)
    for k in range(5):
        c.put(k, _make_pix(qapp, 100))
    evicted = c.evict_until(0)
    assert evicted == 5
    assert len(c) == 0


def test_zero_max_bytes_rejected():
    with pytest.raises(ValueError):
        LRUPixmapCache(max_bytes=0)


def test_estimate_bytes_uses_width_height(qapp):
    p = _make_pix(qapp, 100)
    est = _estimate_bytes(p)
    assert est == 100 * 100 * 4
