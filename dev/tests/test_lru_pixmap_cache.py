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


# ── load_thumb_qpixmap 도 같은 LRU 를 탄다 (#렉) ──────────────────────────
def test_load_thumb_qpixmap_hits_the_cache(qapp, tmp_path, monkeypatch):
    """같은 (경로·크기·종류)는 디스크를 **한 번만** 읽는다.

    매치 검토는 행마다 이 함수를 부르고 크기 슬라이더를 움직일 때마다 다시 부른다 —
    캐시가 없던 시절엔 그때마다 디스크 재읽기 + SmoothTransformation 이 GUI
    스레드에서 반복돼 스크롤이 끊겼다."""
    from aoi_verification.app.utils import image_io

    src = tmp_path / "die.png"
    _make_pix(qapp, 64).save(str(src))
    image_io.clear_tile_cache()

    reads: list[str] = []
    real_path = image_io.get_thumb_path

    def counting_path(p):
        reads.append(str(p))
        return src                       # 실제 캐시 파일 대신 준비한 이미지

    monkeypatch.setattr(image_io, "get_thumb_path", counting_path)
    try:
        first = image_io.load_thumb_qpixmap(src, 48)
        second = image_io.load_thumb_qpixmap(src, 48)
        assert not first.isNull()
        assert second is first, "같은 요청인데 픽스맵을 새로 만들었다(캐시 미스)"
        assert len(reads) == 1, f"디스크를 {len(reads)}번 읽었다 — 캐시가 동작하지 않는다"

        image_io.load_thumb_qpixmap(src, 96)          # 크기가 다르면 별개 항목
        assert len(reads) == 2
    finally:
        monkeypatch.setattr(image_io, "get_thumb_path", real_path)
        image_io.clear_tile_cache()


def test_clear_tile_cache_also_drops_thumb_pixmaps(qapp, tmp_path, monkeypatch):
    """세션 전환에서 함께 비워져야 한다 — 안 그러면 옛 사진이 남는다."""
    from aoi_verification.app.utils import image_io

    src = tmp_path / "die.png"
    _make_pix(qapp, 64).save(str(src))
    monkeypatch.setattr(image_io, "get_thumb_path", lambda p: src)
    image_io.clear_tile_cache()
    first = image_io.load_thumb_qpixmap(src, 48)
    image_io.clear_tile_cache()
    assert image_io.load_thumb_qpixmap(src, 48) is not first
