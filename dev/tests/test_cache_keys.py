"""캐시 키 — mtime 메모이즈 + resolve() 미사용으로 NAS stat 왕복 감소 (#5)."""

from __future__ import annotations


from aoi_verification.app.utils import cache


def test_cache_path_memoizes_mtime(tmp_path, monkeypatch):
    import os
    src = tmp_path / "img.jpg"
    src.write_bytes(b"x")
    src_abs = os.path.abspath(str(src))
    calls = {"n": 0}
    real_stat = cache.os.stat

    def counting_stat(p, *a, **k):
        if str(p) == src_abs:               # 원본(NAS) stat 만 카운트
            calls["n"] += 1
        return real_stat(p, *a, **k)

    monkeypatch.setattr(cache.os, "stat", counting_stat)
    cache.reset_mtime_cache()
    # 같은 원본에 대해 thumb/mid/feature 3종 키를 계산해도 원본 stat 은 1회만.
    cache.cache_path(src, "thumb")
    cache.cache_path(src, "mid")
    cache.cache_path(src, "feature", extra="abc")
    assert calls["n"] == 1


def test_cache_key_changes_with_size_option_and_extra(tmp_path):
    src = tmp_path / "img.jpg"
    src.write_bytes(b"x")
    cache.reset_mtime_cache()
    a = cache.cache_path(src, "thumb")
    b = cache.cache_path(src, "mid")
    c = cache.cache_path(src, "feature", extra="t1")
    d = cache.cache_path(src, "feature", extra="t2")
    assert a.name != b.name and c.name != d.name


def test_reset_mtime_cache_clears(tmp_path, monkeypatch):
    src = tmp_path / "img.jpg"
    src.write_bytes(b"x")
    cache.reset_mtime_cache()
    cache.cache_path(src, "thumb")
    assert cache._mtime_cache                      # 채워짐
    cache.reset_mtime_cache()
    assert not cache._mtime_cache                  # 비워짐


def test_failed_stat_is_not_memoized(tmp_path):
    """없는(또는 순간 못 읽은) 파일의 0.0 이 세션 내내 굳으면 안 된다 — 나중에 생기면 실제 mtime."""
    src = tmp_path / "late.jpg"
    cache.reset_mtime_cache()
    assert cache.memo_mtime(src) == 0.0
    assert cache._abspath(src) not in cache._mtime_cache
    src.write_bytes(b"x")
    assert cache.memo_mtime(src) == src.stat().st_mtime > 0
    # 성공한 값은 메모된다.
    assert cache._abspath(src) in cache._mtime_cache
