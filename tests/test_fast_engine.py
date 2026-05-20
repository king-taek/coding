"""고속 모드 엔진 신규 모듈 단위 테스트 — SimilarityConfig / preprocess /
EmbeddingIndex / SlotScoreCache LRU 상한."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from aoi_verification.app import config
from aoi_verification.app.similarity import embedding_index as ann
from aoi_verification.app.similarity import preprocess as pp
from aoi_verification.app.similarity.slot_features import SlotScoreCache


# ---- SimilarityConfig ----------------------------------------------------
def test_default_cfg_cache_extra_empty():
    """기본 모드 + 전처리 OFF 면 캐시 키 extra 가 빈 문자열 (기존 캐시 호환)."""
    assert config.DEFAULT_SIM_CONFIG.cache_extra() == ""
    assert config.DEFAULT_SIM_CONFIG.has_preprocess is False


def test_cfg_cache_extra_changes_with_toggles():
    a = config.SimilarityConfig(grayscale=True)
    b = config.SimilarityConfig(contrast=True)
    c = config.SimilarityConfig(grayscale=True)
    assert a.cache_extra() != ""
    assert a.cache_extra() != b.cache_extra()
    assert a.cache_extra() == c.cache_extra()       # 결정적
    assert config.SimilarityConfig(kla_crop=True).has_preprocess is True


# ---- preprocess ----------------------------------------------------------
def test_kla_crop_removes_bands():
    from PIL import Image
    img = Image.fromarray(np.zeros((200, 100, 3), dtype=np.uint8))
    out = pp.kla_crop_rgb(img, 0.1, 0.1)
    assert out.size == (100, 160)


def test_kla_crop_noop_when_zero():
    from PIL import Image
    img = Image.fromarray(np.zeros((50, 50, 3), dtype=np.uint8))
    assert pp.kla_crop_rgb(img, 0.0, 0.0).size == (50, 50)


def test_gray_transforms_full_range_and_shape():
    g = np.random.RandomState(3).randint(60, 190, (40, 40), dtype=np.uint8)
    hs = pp.grayscale_highsens(g)
    hc = pp.high_contrast(g)
    assert hs.shape == g.shape and hc.shape == g.shape
    assert hs.dtype == np.uint8 and hc.dtype == np.uint8


def test_apply_gray_chain_respects_flags():
    g = np.random.RandomState(4).randint(60, 190, (32, 32), dtype=np.uint8)
    same = pp.apply_gray_chain(g, config.SimilarityConfig())     # 전부 OFF
    assert np.array_equal(same, g)
    diff = pp.apply_gray_chain(g, config.SimilarityConfig(contrast=True))
    assert diff.shape == g.shape


# ---- EmbeddingIndex ------------------------------------------------------
def _unit(v):
    return (v / (np.linalg.norm(v) + 1e-9)).astype(np.float32)


def test_embedding_index_clusters_correctly():
    if not ann.is_available():
        pytest.skip("hnswlib 미설치")
    rng = np.random.RandomState(0)
    centers = [_unit(rng.randn(16)) for _ in range(3)]
    emb = {}
    truth = {}
    for s in range(3):
        for k in range(5):
            p = Path(f"/tmp/s{s}_{k}.png")
            emb[p] = _unit(centers[s] + 0.03 * rng.randn(16))
            truth[p] = s
    idx, paths = ann.build_from(emb)
    q = _unit(centers[2] + 0.03 * rng.randn(16))
    hits = idx.query(q, 5)
    top = [truth[paths[label]] for label, _ in hits]
    assert top[:3] == [2, 2, 2]
    # cosine sim 내림차순
    sims = [s for _, s in hits]
    assert sims == sorted(sims, reverse=True)


def test_embedding_index_save_load_roundtrip(tmp_path):
    if not ann.is_available():
        pytest.skip("hnswlib 미설치")
    rng = np.random.RandomState(1)
    emb = {Path(f"/tmp/v{i}.png"): _unit(rng.randn(8)) for i in range(6)}
    idx, paths = ann.build_from(emb)
    fp = tmp_path / "idx.bin"
    idx.save(fp)
    loaded = ann.EmbeddingIndex.load(fp, dim=8, max_elements=len(paths))
    q = emb[paths[0]]
    hits = loaded.query(q, 1)
    assert hits[0][0] == 0          # 자기 자신이 1위


def test_build_from_empty_returns_none():
    assert ann.build_from({}) is None


# ---- SlotScoreCache LRU 상한 ---------------------------------------------
def test_score_cache_lru_eviction():
    """max_pairs 초과 시 가장 오래 접근하지 않은 슬롯이 제거된다 (#17)."""
    c = SlotScoreCache(max_pairs=5)
    for i in range(3):
        c.put("A", Path(f"/r{i}"), Path("/v"), 0.5)   # A: 3 pairs
    for i in range(3):
        c.put("B", Path(f"/r{i}"), Path("/v"), 0.6)   # B: 3 → total 6 > 5
    # A 가 LRU 라서 제거됐어야 함 (B 는 방금 put → 보호).
    assert c.has_slot("B") is True
    assert c.has_slot("A") is False
    assert c.size() <= 5


def test_score_cache_get_refreshes_lru():
    c = SlotScoreCache(max_pairs=6)
    for i in range(3):
        c.put("A", Path(f"/r{i}"), Path("/v"), 0.5)
    for i in range(3):
        c.put("B", Path(f"/r{i}"), Path("/v"), 0.6)
    # A 를 다시 접근 → 최근 사용으로 갱신.
    c.get_pair("A", Path("/r0"), Path("/v"))
    # C 추가로 상한 초과 → 이제 B 가 LRU 라 제거 대상.
    for i in range(3):
        c.put("C", Path(f"/r{i}"), Path("/v"), 0.7)
    assert c.has_slot("A") is True
    assert c.has_slot("C") is True
    assert c.has_slot("B") is False
