"""유사도 캐시 버전 · 조용한 실패 제거 회귀 가드.

H6   특징(.npz)·점수(.json.gz) 캐시 키에 알고리즘 버전 — 공식이 바뀌면 옛 파일이 적중하면 안 된다.
H6   좌표(orb_xy) 없는 옛 npz 는 캐시 미스(중앙가중 ORB 가 조용히 꺼지면 안 된다).
L11  npz 는 pickle 없이 읽는다.
L12  임베딩 캐시 키는 중앙 crop 이 켜졌을 때만 정확한 비율을 싣는다(운영 키 cc0 은 불변).
L13  선계산 채점은 점수 시그니처가 키로 삼는 components/center_strength 를 실제로 넘긴다.
M18  채점 예외는 0점 후보가 아니라 '제외 + 기록' 이다 / 좌표 폴백은 기준 1장 실패로 죽지 않는다.
M17  고효율 모드에서 임베딩이 없는 검증 사진도 결과에서 사라지지 않는다.
"""
from __future__ import annotations

import logging
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets")
cv2 = pytest.importorskip("cv2")

from aoi_verification.app import config                              # noqa: E402
from aoi_verification.app.learning import embedder_openvino as ov     # noqa: E402
from aoi_verification.app.models.slot import ImageItem               # noqa: E402
from aoi_verification.app.similarity import pipeline                 # noqa: E402
from aoi_verification.app.similarity import slot_features as sf      # noqa: E402
from aoi_verification.app.utils import cache                         # noqa: E402
from aoi_verification.app.workers import coord_matcher as CM         # noqa: E402
from aoi_verification.app.workers import efficiency_matcher as eff   # noqa: E402
from aoi_verification.app.workers import matcher as M                # noqa: E402


def _img(path: Path, seed: int) -> Path:
    rng = np.random.default_rng(seed)
    cv2.imwrite(str(path), (rng.random((120, 120, 3)) * 255).astype(np.uint8))
    return path


def _item(slot, path, side) -> ImageItem:
    return ImageItem(slot=slot, path=Path(path), side=side)


# ---------------------------------------------------------------------------
# H6 — 알고리즘 버전이 캐시 키에 있다
# ---------------------------------------------------------------------------
def test_feature_cache_key_changes_with_version(tmp_path, monkeypatch):
    img = _img(tmp_path / "a.png", 1)
    k1 = cache.cache_path(img, "feature", extra=pipeline.feature_cache_extra(None, "ref"))
    monkeypatch.setattr(pipeline, "_FEATURE_VERSION", "f-next")
    k2 = cache.cache_path(img, "feature", extra=pipeline.feature_cache_extra(None, "ref"))
    assert k1 != k2


def test_feature_cache_extra_keeps_cfg_part():
    cfg = config.SimilarityConfig(center_crop=True, center_ratio=0.25)
    assert pipeline.feature_cache_extra(cfg, "ref").endswith("c25")
    # side 별 분리(cfg.cache_extra 규칙)도 그대로
    assert pipeline.feature_cache_extra(cfg, None) == pipeline._FEATURE_VERSION


def test_extract_does_not_hit_cache_written_by_other_version(tmp_path, monkeypatch):
    img = _img(tmp_path / "a.png", 2)
    pipeline.extract(img)
    n_before = len(list(cache.paths.feature_cache_dir().glob("*.npz")))
    assert n_before == 1
    monkeypatch.setattr(pipeline, "_FEATURE_VERSION", "f-next")
    pipeline.extract(img)                       # 옛 버전 파일은 안 맞아 새로 계산·저장
    assert len(list(cache.paths.feature_cache_dir().glob("*.npz"))) == 2


def test_score_signature_changes_with_version(monkeypatch):
    before = sf._score_signature(None)
    monkeypatch.setattr(pipeline, "_FEATURE_VERSION", "f-next")
    assert sf._score_signature(None) != before


def test_old_npz_without_orb_xy_is_recomputed(tmp_path):
    """좌표 저장 이전의 옛 캐시: 디스크립터는 있고 orb_xy 는 없다 → 미스로 다시 계산."""
    img = _img(tmp_path / "a.png", 3)
    feat = pipeline.extract(img)
    assert feat.orb_desc is not None and feat.orb_xy is not None
    f = cache.cache_path(img, "feature", extra=pipeline.feature_cache_extra(None, None))
    assert f.exists()
    # orb_xy 만 빠진 옛 포맷으로 덮어쓴다.
    np.savez_compressed(str(f), phash=feat.phash, roi_gray=feat.roi_gray,
                        orb_kp=np.array([feat.orb_kp], dtype=np.int32),
                        orb_desc=feat.orb_desc)
    with pytest.raises(ValueError):
        pipeline.Feature.load(f, img)
    again = pipeline.extract(img)
    assert again.orb_xy is not None             # 중앙가중이 조용히 꺼지지 않는다
    assert "orb_xy" in np.load(str(f)).files    # 새 포맷으로 다시 저장됨


def test_npz_without_keypoints_is_a_valid_cache(tmp_path):
    """디스크립터가 아예 없으면(키포인트 0개) 좌표가 없는 게 정상 — 미스 아님."""
    f = tmp_path / "x.npz"
    np.savez_compressed(str(f), phash=np.zeros(4, np.uint8),
                        roi_gray=np.zeros((4, 4), np.uint8),
                        orb_kp=np.array([0], dtype=np.int32))
    loaded = pipeline.Feature.load(f, Path("x"))
    assert loaded.orb_desc is None and loaded.orb_xy is None


# ---------------------------------------------------------------------------
# L11 — allow_pickle=False
# ---------------------------------------------------------------------------
def test_feature_npz_round_trips_without_pickle(tmp_path):
    img = _img(tmp_path / "a.png", 4)
    feat = pipeline.extract(img)
    dst = tmp_path / "f.npz"
    feat.save(dst)
    back = pipeline.Feature.load(dst, img)
    assert np.array_equal(back.phash, feat.phash)
    assert np.array_equal(back.roi_gray, feat.roi_gray)
    assert np.array_equal(back.orb_desc, feat.orb_desc)
    assert np.array_equal(back.orb_xy, feat.orb_xy)
    assert back.orb_kp == feat.orb_kp


def test_feature_load_refuses_pickled_objects(tmp_path):
    f = tmp_path / "evil.npz"
    np.savez(str(f), phash=np.array([{"a": 1}], dtype=object),
             roi_gray=np.zeros((2, 2), np.uint8),
             orb_kp=np.array([0], dtype=np.int32))
    with pytest.raises(ValueError):
        pipeline.Feature.load(f, Path("x"))


# ---------------------------------------------------------------------------
# L12 — 임베딩 캐시 키의 중앙 crop 비율
# ---------------------------------------------------------------------------
def test_emb_signature_carries_center_ratio_only_when_crop_is_on():
    c25 = config.SimilarityConfig(center_crop=True, center_ratio=0.25)
    c30 = config.SimilarityConfig(center_crop=True, center_ratio=0.0)    # 0 → 레거시 0.3
    off = config.SimilarityConfig(center_crop=False, center_ratio=0.25)
    s25 = ov._emb_signature("mk", c25, "ref")
    s30 = ov._emb_signature("mk", c30, "ref")
    assert s25 != s30                                   # 비율이 다르면 키가 다르다
    assert "0.25" in s25 and "0.3" in s30
    # crop 이 꺼진 쪽은 비율이 있어도 키에 안 실린다 — 운영 키(cc0)는 바이트 단위로 불변.
    assert ov._emb_signature("mk", off, "ref") == f"mk|px{ov._INPUT_PX}|cc0|{ov._EMB_VERSION}"
    assert ov._emb_signature("mk", None, "ref") == f"mk|px{ov._INPUT_PX}|cc0|{ov._EMB_VERSION}"
    # side=None 은 crop 을 적용하지 않는다(config._center_crop_for) → cc0.
    assert ov._emb_signature("mk", c25, None).count("|cc0|") == 1


# ---------------------------------------------------------------------------
# L13 — 선계산 채점이 시그니처가 키로 삼는 값을 실제로 쓴다
# ---------------------------------------------------------------------------
def _run_precompute(monkeypatch, cfg):
    calls: list = []

    class _F:
        def __init__(self, p):
            self.path = p

    monkeypatch.setattr(pipeline, "extract", lambda p, **kw: _F(p))

    def fake_score(a, b, weights=None, **kw):
        calls.append(kw)
        return 0.5

    monkeypatch.setattr(pipeline, "score", fake_score)
    refs = [_item("S", "/tmp/S/r0.jpg", "ref")]
    vals = [_item("S", "/tmp/S/v0.jpg", "val"), _item("S", "/tmp/S/v1.jpg", "val")]
    w = sf.SlotPrecomputeWorker([("S", refs, vals)], sf.SlotFeatureCache(),
                                sf.SlotScoreCache(), cfg=cfg)
    w.run()
    return calls


def test_precompute_passes_components_and_center_strength(monkeypatch):
    cfg = config.SimilarityConfig(rerank_components=frozenset({"orb"}),
                                  orb_center_weight=0.5)
    calls = _run_precompute(monkeypatch, cfg)
    assert len(calls) == 2
    assert all(c == {"components": {"orb"}, "center_strength": 0.5} for c in calls)


def test_precompute_default_cfg_scores_like_before(monkeypatch):
    calls = _run_precompute(monkeypatch, config.SimilarityConfig())
    assert calls and all(c == {"components": None, "center_strength": 0.0} for c in calls)
    assert all(c == {"components": None, "center_strength": 0.0}
               for c in _run_precompute(monkeypatch, None))


# ---------------------------------------------------------------------------
# M18 — 채점 예외는 0점 후보가 아니다
# ---------------------------------------------------------------------------
def test_score_ref_classical_skips_and_records_failures(monkeypatch, caplog):
    ref = _item("S", "/tmp/r.jpg", "ref")
    ok = _item("S", "/tmp/ok.jpg", "val")
    bad = _item("S", "/tmp/bad.jpg", "val")

    def fake_extract(p, **kw):
        if Path(p).name == "bad.jpg":
            raise OSError("cannot read")
        return object()

    monkeypatch.setattr(M.sim, "extract", fake_extract)
    monkeypatch.setattr(M.sim, "score", lambda a, b, **kw: 0.7)
    with caplog.at_level(logging.WARNING, logger="aoi.match"):
        out = M.score_ref_classical(ref, [ok, bad], threshold=0.0)
    assert [c.item.path for c in out] == [ok.path]          # 0% 후보로 둔갑하지 않는다
    assert out.failed == [bad.path]
    assert isinstance(out, list)                             # 기존 호출자 호환
    assert any("bad.jpg" in r.getMessage() for r in caplog.records)


def test_score_ref_classical_all_ok_has_no_failures(monkeypatch):
    ref = _item("S", "/tmp/r.jpg", "ref")
    vals = [_item("S", f"/tmp/v{i}.jpg", "val") for i in range(3)]
    monkeypatch.setattr(M.sim, "extract", lambda p, **kw: object())
    scores = iter([0.3, 0.9, 0.6])
    monkeypatch.setattr(M.sim, "score", lambda a, b, **kw: next(scores))
    out = M.score_ref_classical(ref, vals, threshold=0.0)
    assert out.failed == []
    assert [c.score for c in out] == [0.9, 0.6, 0.3]         # 내림차순 불변


def test_coord_fallback_survives_unreadable_reference(monkeypatch, caplog):
    """폴백 기준 사진 1장이 못 읽혀도 실행 전체가 중단되지 않는다."""
    bad = _item("A1", "/tmp/r_bad.jpg", "ref")
    good = _item("A1", "/tmp/r_good.jpg", "ref")
    with_coord = _item("A1", "/tmp/r_has.jpg", "ref")
    val = _item("A1", "/tmp/v.jpg", "val")

    class _C:
        def __init__(self, col, row, x, y):
            self.col, self.row, self.x, self.y = col, row, x, y

    monkeypatch.setattr(CM, "_resolve_batch", lambda paths, progress=None: {
        with_coord.path: _C(1, 1, 0.0, 0.0), val.path: _C(1, 1, 10.0, 0.0)})

    class _Cand:
        def __init__(self, item, score):
            self.item, self.score = item, score

    def fake_classical(ref, vals, **kw):
        if ref.path == bad.path:
            raise OSError("unreadable reference")
        return [_Cand(vals[0], 0.8)]

    monkeypatch.setattr(CM, "score_ref_classical", fake_classical)
    results: dict = {}
    fin: list = []
    sched = CM.CoordScheduler([("A1", [bad, good, with_coord], [val])], results=results)
    sched.signals.finished.connect(lambda: fin.append(1))
    with caplog.at_level(logging.WARNING, logger="aoi.match"):
        sched._run()
    assert fin == [1]                                        # 끝까지 돌았다
    assert results[("A1", bad.path)] == []
    assert ("A1", bad.path) in sched.failed_set
    assert results[("A1", good.path)] == [(val.path, pytest.approx(0.8))]
    assert ("A1", good.path) not in sched.failed_set
    assert results[("A1", with_coord.path)]                  # 좌표 경로는 영향 없음
    assert any("r_bad.jpg" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# M17 — 임베딩이 없는 검증 사진도 결과에 남는다
# ---------------------------------------------------------------------------
def _fake_embed_factory(skip_names=(), raise_on_val=False):
    def _fake(paths, **kw):
        if raise_on_val:
            raise RuntimeError("device lost")
        out = {}
        for p in paths:
            if Path(p).name in skip_names:
                continue
            h = sum(Path(p).name.encode()) % 9973
            v = np.random.default_rng(h).normal(size=16).astype(np.float32)
            out[Path(p)] = v / (np.linalg.norm(v) + 1e-9)
        return out
    return _fake


def _run_eff(tmp_path, monkeypatch, fake_embed):
    refs = [_item("S1", tmp_path / f"r{i}.jpg", "ref") for i in range(2)]
    vals = [_item("S1", tmp_path / f"v{i}.jpg", "val") for i in range(5)]
    for i, it in enumerate(refs + vals):
        _img(it.path, 100 + i)
    monkeypatch.setattr(eff._ov, "available_units", lambda: ["GPU"])
    monkeypatch.setattr(eff._ov, "compile_model_on", lambda mk, dev, batch=1: object())
    monkeypatch.setattr(eff._ov, "device_embed", fake_embed)
    results: dict = {}
    sched = eff.EfficiencyScheduler([("S1", refs, vals)],
                                    cfg=config.SimilarityConfig(engine="efficiency"),
                                    threshold=0.0, results=results)
    sched._run()
    return refs, vals, results


def test_efficiency_keeps_val_whose_embedding_failed(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="aoi.match"):
        refs, vals, results = _run_eff(
            tmp_path, monkeypatch, _fake_embed_factory(skip_names={"v2.jpg"}))
    for r in refs:
        cands = results[("S1", Path(r.path))]
        got = [Path(p) for p, _ in cands]
        assert sorted(got) == sorted(Path(v.path) for v in vals)    # 5장 전부, 중복 없음
        scores = [s for _, s in cands]
        assert scores == sorted(scores, reverse=True)               # 내림차순 계약
        miss = dict(cands)[Path(tmp_path / "v2.jpg")]
        assert 0.0 <= miss <= 1.0                                   # 고전 점수로 채점됨
    assert any("임베딩 실패" in r.getMessage() for r in caplog.records)      # 실패 건수 로그
    assert any("임베딩 없는 후보" in r.getMessage() for r in caplog.records)


def test_efficiency_nothing_failed_adds_nothing(tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="aoi.match"):
        refs, vals, results = _run_eff(tmp_path, monkeypatch, _fake_embed_factory())
    for r in refs:
        assert len(results[("S1", Path(r.path))]) == len(vals)
    assert not [r for r in caplog.records if r.name == "aoi.match"]


def test_efficiency_slot_level_embedding_failure_is_logged_and_falls_back(
        tmp_path, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger="aoi.match"):
        refs, vals, results = _run_eff(
            tmp_path, monkeypatch, _fake_embed_factory(raise_on_val=True))
    for r in refs:                                   # 고전 폴백으로 전부 채워진다
        assert len(results[("S1", Path(r.path))]) == len(vals)
    assert any("슬롯 S1 임베딩 실패" in r.getMessage() for r in caplog.records)
