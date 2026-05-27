"""임베딩 디스크 캐시 round-trip + 키 무효화 검증 (#3).

GPU/OpenVINO 없이 헬퍼만 직접 테스트한다(임베딩 캐시는 device_embed 의 무거운
디코드·추론을 재실행 시 건너뛰게 해 준다)."""

from __future__ import annotations

import os
import time

import numpy as np

from aoi_verification.app.learning import embedder_openvino as ov
from aoi_verification.app.utils import paths


def test_embedding_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "embedding_cache_dir", lambda: tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"x")
    sig = ov._emb_signature("mobilenet_v3", None, "ref")
    vec = np.arange(8, dtype=np.float32)

    assert ov._emb_cache_load(img, sig) is None     # 처음엔 미스
    ov._emb_cache_save(img, vec, sig)
    loaded = ov._emb_cache_load(img, sig)
    assert loaded is not None and np.array_equal(loaded, vec)


def test_embedding_cache_invalidated_by_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "embedding_cache_dir", lambda: tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(b"x")
    sig = ov._emb_signature("mobilenet_v3", None, None)
    ov._emb_cache_save(img, np.ones(4, dtype=np.float32), sig)
    assert ov._emb_cache_load(img, sig) is not None

    # 원본 mtime 이 바뀌면(=재촬영) 키가 달라져 캐시 미스 → 재추출 유도.
    future = time.time() + 10_000
    os.utime(img, (future, future))
    assert ov._emb_cache_load(img, sig) is None


def test_embedding_signature_distinguishes_center_crop():
    class _Cfg:
        def __init__(self, cc):
            self._cc = cc

        def _center_crop_for(self, side):
            return self._cc

    base = ov._emb_signature("mk", None, "ref")
    on = ov._emb_signature("mk", _Cfg(True), "ref")
    off = ov._emb_signature("mk", _Cfg(False), "ref")
    assert on != off
    assert off == base                               # crop off == cfg 없음
