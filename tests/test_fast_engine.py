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


def _save_img(path, seed, size=(120, 150)):
    from PIL import Image
    h, w = size
    Image.fromarray(
        np.random.RandomState(seed).randint(0, 255, (h, w, 3), dtype=np.uint8)
    ).save(path)


# ---- SimilarityConfig ----------------------------------------------------
def test_default_cfg_cache_extra_empty():
    """기본 모드 + 전처리 OFF 면 캐시 키 extra 가 빈 문자열 (기존 캐시 호환)."""
    assert config.DEFAULT_SIM_CONFIG.cache_extra() == ""
    assert config.DEFAULT_SIM_CONFIG.has_preprocess is False


def test_cfg_cache_extra_changes_with_toggles():
    a = config.SimilarityConfig(center_crop=True)
    b = config.SimilarityConfig(kla_crop=True)
    c = config.SimilarityConfig(center_crop=True)
    assert a.cache_extra("ref") != ""
    assert a.cache_extra("ref") != b.cache_extra("ref")
    assert a.cache_extra("ref") == c.cache_extra("ref")   # 결정적
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


# ---- 사전 계산 진행 바 회귀 (0% 멈춤 버그) -------------------------------
def _drive_match_page(tmp_path, engine, monkeypatch=None):
    """MatchPage 를 합성 데이터로 구동하고 (finished?, max_progress_pct) 반환.

    ``_loading.set_progress`` 를 가로채 사전 계산 단계에서 진행 바 값이 0 보다
    커지는지 관찰한다.
    """
    import time
    from PyQt6.QtWidgets import QApplication
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages.match_page import MatchPage

    app = QApplication.instance() or QApplication([])

    # 첫 슬롯에 충분한 쌍을 만들어 progress emit(25쌍마다)이 일어나게 한다.
    queue = [ImageItem("S0", tmp_path / "r0.png", "ref")]
    _save_img(tmp_path / "r0.png", 0)
    vals = []
    for k in range(30):
        vp = tmp_path / f"v0_{k}.png"
        _save_img(vp, 100 + k)
        vals.append(ImageItem("S0", vp, "val"))
    pool = {"S0": vals}

    mp = MatchPage()
    seen = {"max": -1, "calls": 0}
    orig = mp._loading.set_progress

    def spy(done, total, message=""):
        seen["calls"] += 1
        if total > 0:
            seen["max"] = max(seen["max"], int(done * 100 / total))
        return orig(done, total, message)

    mp._loading.set_progress = spy
    done = {"f": False}
    mp.finished.connect(lambda: done.__setitem__("f", True))
    cfg = config.SimilarityConfig(engine=engine, top_k=5)
    mp.load_state(queue, pool, threshold=0.0, auto_mode=True, engine_cfg=cfg)

    start = time.time()
    while not done["f"] and time.time() - start < 40:
        app.processEvents()
        time.sleep(0.01)
    return done["f"], seen["max"], seen["calls"]


def test_precompute_progress_moves_basic(tmp_path, isolated_cache):
    """정밀(기본) 모드: 사전 계산 중 진행 바가 0% 를 넘어야 한다 (멈춤 버그 회귀)."""
    finished, max_pct, calls = _drive_match_page(tmp_path, "basic")
    assert finished is True
    assert calls > 0, "set_progress 가 한 번도 호출되지 않음 (진행 바 갱신 안 됨)"
    assert max_pct > 0, "진행 바가 0% 에서 움직이지 않음"


def test_precompute_progress_moves_fast(tmp_path, isolated_cache, monkeypatch):
    """고속 모드: 임베딩을 mock 하고 사전 계산 중 진행 바가 0% 를 넘는지 확인."""
    import re
    from aoi_verification.app.workers import fast_matcher as fm
    if not fm.is_available():
        pytest.skip("hnswlib/torch 미설치")

    def fake_embed(items, *, cfg=None):
        out = {}
        for it in items:
            name = Path(it.path).name
            s = int(re.search(r"\d", name).group())
            rng = np.random.RandomState(1000 + s)
            v = rng.randn(16).astype(np.float32)
            v = v / np.linalg.norm(v)
            out[Path(it.path)] = v
        return out

    # FastIndexWorker 가 부르는 임베딩 함수만 교체 (가중치 다운로드 회피).
    monkeypatch.setattr(fm, "compute_slot_embeddings", fake_embed)
    finished, max_pct, calls = _drive_match_page(tmp_path, "fast")
    assert finished is True
    assert calls > 0
    assert max_pct > 0


# ---- 고속 모드 의존성 — 경량 디스크립터라 추가 설치 불필요 ----------------
def test_fast_deps_no_install_needed(monkeypatch):
    from aoi_verification.app.learning import fast_deps_installer as fdi
    # torch 없어도 고속 모드는 동작 (경량 디스크립터, NumPy/cv2 만 사용).
    monkeypatch.setattr(fdi, "is_torch_installed", lambda: False)
    assert fdi.fast_ready() is True
    assert fdi.missing_packages() == []
    assert fdi.missing_packages(recommend_openvino=False) == []


# ---- hnswlib 없이 NumPy 브루트포스 폴백 검색 -----------------------------
def test_brute_force_index_used_without_hnswlib(monkeypatch):
    """hnswlib 미가용 시 build_from 이 BruteForceIndex 를 쓰고 정확히 클러스터링."""
    monkeypatch.setattr(ann, "hnswlib_available", lambda: False)
    rng = np.random.RandomState(7)
    centers = [_unit(rng.randn(16)) for _ in range(3)]
    emb = {}
    truth = {}
    for s in range(3):
        for k in range(6):
            p = Path(f"/tmp/bf_{s}_{k}.png")
            emb[p] = _unit(centers[s] + 0.03 * rng.randn(16))
            truth[p] = s
    idx, paths = ann.build_from(emb)
    assert type(idx).__name__ == "BruteForceIndex"
    q = _unit(centers[1] + 0.03 * rng.randn(16))
    hits = idx.query(q, 5)
    top = [truth[paths[label]] for label, _ in hits]
    assert top[:3] == [1, 1, 1]
    sims = [s for _, s in hits]
    assert sims == sorted(sims, reverse=True)
    assert ann.is_available() is True       # NumPy 폴백 → 항상 사용 가능


# ---- 중앙 영역(30%) 단일 토글 → ref·val 모두 적용 (#2/#7) -----------------
def test_center_crop_cache_extra():
    cfg = config.SimilarityConfig(center_crop=True)
    assert cfg.cache_extra("ref") == "c30"
    assert cfg.cache_extra("val") == "c30"      # 켜면 기준·검증 모두
    assert cfg.cache_extra(None) == ""          # side 미지정 → crop 안 함
    assert cfg.has_preprocess is True
    off = config.SimilarityConfig(center_crop=False)
    assert off.cache_extra("ref") == "" and off.has_preprocess is False
