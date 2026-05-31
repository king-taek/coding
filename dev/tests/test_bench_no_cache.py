"""bench_no_cache 플래그가 특징 디스크 캐시를 우회하는지 검증.

개발자 벤치마크가 '처음 매칭처럼' 동작하려면, cfg.bench_no_cache=True 일 때
pipeline.extract 가 .npz 캐시를 읽지도 쓰지도 않아야 한다.
"""

from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from aoi_verification.app import config
from aoi_verification.app.similarity import pipeline
from aoi_verification.app.utils import cache


def _make_image(path):
    arr = (np.random.default_rng(7).integers(0, 255, (80, 80, 3))).astype("uint8")
    cv2.imwrite(str(path), arr)


def test_extract_with_bench_no_cache_does_not_write_cache(tmp_path, monkeypatch):
    # 캐시 루트를 임시 HOME 으로 격리(실제 사용자 캐시 건드리지 않음).
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    img = tmp_path / "sample.png"
    _make_image(img)

    cache_file = cache.cache_path(img, "feature", extra="")
    assert not cache_file.exists()

    cfg_nocache = config.SimilarityConfig(bench_no_cache=True)
    feat = pipeline.extract(img, cfg=cfg_nocache, side=None)
    assert feat is not None
    # 우회 모드는 캐시 파일을 만들지 않는다.
    assert not cache_file.exists(), "bench_no_cache 인데 캐시가 생성됨"

    # 일반 모드는 캐시를 만든다(대조).
    cfg_cache = config.SimilarityConfig(bench_no_cache=False)
    pipeline.extract(img, cfg=cfg_cache, side=None)
    assert cache_file.exists(), "일반 모드인데 캐시가 생성되지 않음"


def test_device_embed_is_import_safe_without_accelerator():
    """torch/openvino 미설치 환경에서도 device_embed 가 안전히 빈 결과를 준다."""
    from aoi_verification.app.learning import embedder_openvino as ov
    cfg = config.SimilarityConfig(bench_no_cache=True)
    out = ov.device_embed([], model_kind=ov.MODEL_MOBILENET_V3, device="CPU",
                          cfg=cfg)
    assert out == {}
