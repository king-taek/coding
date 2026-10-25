"""image_io 캐시 안전 — 원자적 쓰기 / 잘린 캐시 재생성 / 실패 픽스맵은 LRU 에 안 넣는다."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PIL.Image")
pytest.importorskip("PyQt6.QtWidgets")
from PIL import Image                                          # noqa: E402

from aoi_verification.app.utils import cache, image_io         # noqa: E402


def _src(tmp_path, name="a.jpg", size=(1600, 1200)):
    p = tmp_path / name
    Image.new("RGB", size, (30, 120, 60)).save(p, quality=90)
    cache.reset_mtime_cache()
    return p


def _parts(folder):
    return [p for p in folder.iterdir() if ".part" in p.name]


def test_truncated_cache_file_is_regenerated(isolated_cache, tmp_path):
    src = _src(tmp_path)
    out = image_io.get_mid_path(src)
    good = out.read_bytes()
    out.write_bytes(good[: len(good) // 2])            # 쓰다 끊긴 잘린 JPEG (크기 > 0)
    again = image_io.get_mid_path(src)
    assert again == out
    assert out.read_bytes() == good                    # 같은 입력 → 같은 결과로 복구
    with Image.open(out) as im:
        im.load()


def test_valid_cache_file_is_a_hit_and_not_rewritten(isolated_cache, tmp_path):
    src = _src(tmp_path)
    out = image_io.get_mid_path(src)
    mtime = out.stat().st_mtime_ns
    assert image_io.get_mid_path(src) == out
    assert out.stat().st_mtime_ns == mtime


def test_failed_write_keeps_old_cache_and_leaves_no_temp(isolated_cache, tmp_path,
                                                         monkeypatch):
    src = _src(tmp_path)
    out = image_io.get_mid_path(src)
    out.write_bytes(b"\xff\xd8garbage")                # 못 쓰는 상태 → 재생성 시도
    real_save = Image.Image.save

    def boom(self, fp, *a, **k):
        real_save(self, fp, *a, **k)
        raise OSError("disk full")
    monkeypatch.setattr(Image.Image, "save", boom)
    with pytest.raises(OSError):
        image_io.get_mid_path(src)
    assert out.read_bytes() == b"\xff\xd8garbage"      # 반쯤 쓴 파일이 캐시 자리를 차지하지 않는다
    assert _parts(out.parent) == []


def test_normal_generation_leaves_no_temp(isolated_cache, tmp_path):
    src = _src(tmp_path)
    out = image_io.get_thumb_path(src)
    assert out.exists() and _parts(out.parent) == []


# ---------------------------------------------------------------- 타일 픽스맵
def test_failed_tile_pixmap_is_not_cached_then_recovers(qapp, isolated_cache, tmp_path,
                                                        monkeypatch):
    image_io.clear_tile_cache()
    src = _src(tmp_path)
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")
    good_path = image_io.get_thumb_path(src)
    chosen = {"p": broken}
    monkeypatch.setattr(image_io, "get_thumb_path", lambda p: chosen["p"])
    bad = image_io.cached_tile_pixmap(src, 40)
    assert not bad.isNull()                            # 회색 대체 칸은 돌려준다
    key = (str(src), 40, False, False)
    assert image_io._get_tile_cache().get(key) is None  # 그러나 캐시에는 없다
    # 같은 키로 다시 불러도 (고쳐진) 진짜 썸네일이 나온다
    chosen["p"] = good_path
    image_io.cached_tile_pixmap(src, 40)
    assert image_io._get_tile_cache().get(key) is not None


def test_failed_dimmed_tile_pixmap_is_not_cached(qapp, isolated_cache, tmp_path,
                                                 monkeypatch):
    image_io.clear_tile_cache()
    src = _src(tmp_path)
    monkeypatch.setattr(image_io, "get_thumb_path",
                        lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    image_io.cached_tile_pixmap(src, 40, dim=True)
    assert image_io._get_tile_cache().get((str(src), 40, False, True)) is None
