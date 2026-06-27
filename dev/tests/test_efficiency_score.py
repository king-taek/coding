"""고효율 모드 — 점수 정규화([0,1]) 단위 테스트.

임베딩 유닛은 코사인 유사도를 ``(cos+1)/2`` 로 정규화해 고전 파이프라인([0,1])
과 동일 임계치를 적용한다.  실제 OpenVINO 없이 ``device_embed`` 를 모킹해
정규화 + 랭킹을 검증한다."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import ImageItem
from aoi_verification.app.workers import efficiency_matcher as eff


def test_cos_to_unit_bounds():
    assert eff._cos_to_unit(1.0) == 1.0      # 완전 일치
    assert eff._cos_to_unit(0.0) == 0.5      # 직교
    assert eff._cos_to_unit(-1.0) == 0.0     # 반대
    # 범위 클램프
    assert eff._cos_to_unit(2.0) == 1.0
    assert eff._cos_to_unit(-2.0) == 0.0


def _item(name, slot="S", side="val"):
    return ImageItem(slot=slot, path=Path(name), side=side)


def test_embed_unit_normalizes_cosine(monkeypatch):
    """val a(동일)·b(직교)·c(반대) → 점수 1.0 / 0.5 / 0.0 (내림차순)."""
    import numpy as np

    vecs = {
        Path("r.png"): np.array([1.0, 0.0], dtype=np.float32),
        Path("a.png"): np.array([1.0, 0.0], dtype=np.float32),
        Path("b.png"): np.array([0.0, 1.0], dtype=np.float32),
        Path("c.png"): np.array([-1.0, 0.0], dtype=np.float32),
    }

    def fake_device_embed(paths, **kw):
        return {Path(p): vecs[Path(p)] for p in paths if Path(p) in vecs}

    monkeypatch.setattr(eff._ov, "device_embed", fake_device_embed)

    ref = _item("r.png", side="ref")
    vals = [_item("a.png"), _item("b.png"), _item("c.png")]
    unit = eff._EmbedUnit("gpu", eff._ov.MODEL_MOBILENET_V3, "GPU",
                          cfg=None, threshold=0.0)
    cands = unit.match(ref, vals)

    by_name = {c.item.path.name: c.score for c in cands}
    assert abs(by_name["a.png"] - 1.0) < 1e-6
    assert abs(by_name["b.png"] - 0.5) < 1e-6
    assert abs(by_name["c.png"] - 0.0) < 1e-6
    # 내림차순 정렬
    assert [c.item.path.name for c in cands] == ["a.png", "b.png", "c.png"]
    # 모든 점수 [0,1]
    assert all(0.0 <= c.score <= 1.0 for c in cands)


def test_embed_unit_threshold_filters(monkeypatch):
    import numpy as np

    vecs = {
        Path("r.png"): np.array([1.0, 0.0], dtype=np.float32),
        Path("a.png"): np.array([1.0, 0.0], dtype=np.float32),   # 1.0
        Path("b.png"): np.array([0.0, 1.0], dtype=np.float32),   # 0.5
    }
    monkeypatch.setattr(eff._ov, "device_embed",
                        lambda paths, **kw: {Path(p): vecs[Path(p)]
                                             for p in paths if Path(p) in vecs})
    ref = _item("r.png", side="ref")
    vals = [_item("a.png"), _item("b.png")]
    unit = eff._EmbedUnit("npu", eff._ov.MODEL_RESNET18, "NPU",
                          cfg=None, threshold=0.75)
    cands = unit.match(ref, vals)
    assert [c.item.path.name for c in cands] == ["a.png"]   # b(0.5) 임계치 미달
