"""정답 라벨 만들기 코어 — 헤드리스 단위 테스트 (Qt/torch 불필요)."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.dev import labels as lab
from aoi_verification.app.models.slot import ImageItem


def _item(slot, path, side="val"):
    return ImageItem(slot=slot, path=Path(path), side=side)


def _tasks():
    refs = [_item("S1", "/r/S1/a.jpg", "ref"), _item("S1", "/r/S1/b.jpg", "ref")]
    vals = [_item("S1", "/v/S1/x.jpg"), _item("S1", "/v/S1/y.jpg"),
            _item("S1", "/v/S1/z.jpg")]
    return [("S1", refs, vals)]


# ---------------------------------------------------------------------------
# normalize / save / load / stats
# ---------------------------------------------------------------------------
def test_normalize_handles_multiple_none_and_scalar():
    raw = {"S1": {"r1": ["a", "b", "a"], "r2": [], "r3": "c", "r4": None}}
    n = lab.normalize(raw)
    assert n["S1"]["r1"] == ["a", "b"]      # 중복 제거 + 정렬
    assert n["S1"]["r2"] == []              # 정답 없음
    assert n["S1"]["r3"] == ["c"]           # 스칼라 허용
    assert n["S1"]["r4"] == []              # None → 정답 없음


def test_save_load_roundtrip(tmp_path):
    labels = {"S1": {"r1": ["v1", "v2"], "r2": []}}
    p = tmp_path / "labels.json"
    lab.save(p, labels)
    back = lab.load(p)
    assert back == lab.normalize(labels)


def test_load_missing_returns_empty(tmp_path):
    assert lab.load(tmp_path / "nope.json") == {}


def test_stats_counts_multi_and_none():
    labels = {"S1": {"r1": ["a"], "r2": ["a", "b"], "r3": []}}
    st = lab.stats(labels)
    assert st == {"refs": 3, "labeled": 2, "none": 1, "multi": 1}


# ---------------------------------------------------------------------------
# LabelMakerModel — 선택/순회/정답없음/복수정답/입출력
# ---------------------------------------------------------------------------
def test_model_navigation_and_keys():
    m = lab.LabelMakerModel(_tasks())
    assert m.count() == 2
    assert m.current_key() == ("S1", "/r/S1/a.jpg")
    m.next()
    assert m.current_key() == ("S1", "/r/S1/b.jpg")
    m.next()                                # 끝에서 더 못 감
    assert m.index() == 1
    m.prev()
    assert m.index() == 0


def test_model_toggle_multiple_and_to_labels():
    m = lab.LabelMakerModel(_tasks())
    assert m.toggle("/v/S1/x.jpg") is True       # 선택
    assert m.toggle("/v/S1/y.jpg") is True       # 복수정답
    assert m.toggle("/v/S1/x.jpg") is False      # 다시 누르면 해제
    assert m.selected() == {"/v/S1/y.jpg"}
    assert m.is_reviewed() is True
    out = m.to_labels()
    assert out == {"S1": {"/r/S1/a.jpg": ["/v/S1/y.jpg"]}}


def test_model_set_none_is_reviewed_empty():
    m = lab.LabelMakerModel(_tasks())
    m.set_none()
    assert m.selected() == set()
    assert m.is_reviewed() is True
    # 정답 없음 = 빈 리스트로 기록됨(미검토와 구분).
    assert m.to_labels() == {"S1": {"/r/S1/a.jpg": []}}


def test_model_unreviewed_excluded_from_labels():
    m = lab.LabelMakerModel(_tasks())
    m.toggle("/v/S1/x.jpg")          # a 만 검토
    out = m.to_labels()
    assert "/r/S1/b.jpg" not in out.get("S1", {})   # b 는 미검토 → 제외
    st = m.stats()
    assert st["unreviewed"] == 1 and st["total"] == 2


def test_model_load_labels_roundtrip():
    m = lab.LabelMakerModel(_tasks())
    m.load_labels({"S1": {"/r/S1/a.jpg": ["/v/S1/z.jpg"], "/r/S1/b.jpg": []}})
    assert m.is_selected("/v/S1/z.jpg") is True
    m.next()
    assert m.selected() == set() and m.is_reviewed() is True
    assert m.dirty is False


def test_model_default_ordering_is_filename():
    m = lab.LabelMakerModel(_tasks())
    names = [p.path.name for p in m.current_vals()]
    assert names == sorted(names)        # 기본 파일명순(즉시, 점수 계산 없음)


# ---------------------------------------------------------------------------
# make_template — 스캔 기반 빈 템플릿
# ---------------------------------------------------------------------------
def test_make_template_scaffolds_all_refs(tmp_path):
    ref = tmp_path / "ref" / "S1"
    val = tmp_path / "val" / "S1"
    ref.mkdir(parents=True)
    val.mkdir(parents=True)
    (ref / "a.jpg").write_bytes(b"x")
    (ref / "b.jpg").write_bytes(b"x")
    (val / "x.jpg").write_bytes(b"x")
    tmpl = lab.make_template(tmp_path / "ref", tmp_path / "val")
    assert set(tmpl["S1"].keys()) == {str(ref / "a.jpg"), str(ref / "b.jpg")}
    assert all(v == [] for v in tmpl["S1"].values())     # 채워 넣을 빈 자리
