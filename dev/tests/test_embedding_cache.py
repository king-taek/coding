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
    # mtime 은 세션 단위로 메모이즈되므로(#5), 새 세션처럼 캐시를 초기화한 뒤 확인.
    from aoi_verification.app.utils import cache as _cache
    future = time.time() + 10_000
    os.utime(img, (future, future))
    _cache.reset_mtime_cache()
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


def test_embedding_signature_carries_a_method_version():
    """★ 정확도 가드 — 임베딩 산출 방식이 캐시 키에 있어야 한다.

    키에 '어떻게 만든 벡터인가' 가 없으면, 방식을 바꾼 뒤에도 옛 ``.npy`` 가 그대로
    적중해 **같은 슬롯 안에서 옛 벡터와 새 벡터를 코사인 비교**하게 된다.  느려지는
    게 아니라 매칭 결과가 틀리는 사고다.  (백본을 빌드 때 IR 로 굽는 방식으로 바꿀 때
    실제로 이 구멍이 드러났다.)"""
    assert ov._EMB_VERSION
    assert ov._EMB_VERSION in ov._emb_signature("mk", None, "ref")
    # 모델·해상도·crop 과 독립적으로 붙어야 한다.
    assert ov._EMB_VERSION in ov._emb_signature("other", None, None)
