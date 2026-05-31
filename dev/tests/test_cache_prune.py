"""썸네일/중간이미지 캐시 1일 TTL 정리(prune_old_cache) 검증 (#1)."""

from __future__ import annotations

import os
import time

from aoi_verification.app.utils import cache, paths


def test_prune_old_cache_removes_only_stale_jpgs(tmp_path, monkeypatch):
    thumbs = tmp_path / "thumbs"
    mid = tmp_path / "mid"
    thumbs.mkdir()
    mid.mkdir()
    monkeypatch.setattr(paths, "thumb_cache_dir", lambda: thumbs)
    monkeypatch.setattr(paths, "mid_cache_dir", lambda: mid)

    old = time.time() - 2 * 86400        # 2일 전
    fresh = time.time()

    stale_thumb = thumbs / "stale.jpg"
    fresh_thumb = thumbs / "fresh.jpg"
    stale_mid = mid / "stale.jpg"
    keep_npz = thumbs / "feat.npz"       # .jpg 아님 → 보존
    for f in (stale_thumb, fresh_thumb, stale_mid, keep_npz):
        f.write_bytes(b"x")
    os.utime(stale_thumb, (old, old))
    os.utime(stale_mid, (old, old))
    os.utime(fresh_thumb, (fresh, fresh))
    os.utime(keep_npz, (old, old))

    removed = cache.prune_old_cache(max_age_days=1.0)

    assert removed == 2                  # stale_thumb + stale_mid
    assert not stale_thumb.exists()
    assert not stale_mid.exists()
    assert fresh_thumb.exists()
    assert keep_npz.exists()             # 비-jpg 는 건드리지 않음
