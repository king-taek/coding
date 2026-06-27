"""요청 6 — 기준 폴더별 '직접 고른 기준 사진' 기록 저장/로드."""

from __future__ import annotations

from aoi_verification.app.utils import ref_history


def test_roundtrip(isolated_cache, tmp_path):
    ref_root = tmp_path / "ref folder"
    ref_root.mkdir()

    assert ref_history.has_history(ref_root) is False
    assert ref_history.get_chosen(ref_root) == {}

    ref_history.save_chosen(ref_root, {"S01": ["a.jpg", "b.jpg"], "S02": []})
    assert ref_history.has_history(ref_root) is True
    assert ref_history.get_chosen(ref_root) == {"S01": ["a.jpg", "b.jpg"]}


def test_empty_save_is_noop(isolated_cache, tmp_path):
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    ref_history.save_chosen(ref_root, {})
    assert ref_history.has_history(ref_root) is False


def test_key_is_absolute_path(isolated_cache, tmp_path):
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    ref_history.save_chosen(ref_root, {"S": ["x.jpg"]})
    # 상대/비정규 경로로도 같은 절대경로면 동일 기록을 가리킨다.
    same = ref_root / "."
    assert ref_history.get_chosen(same) == {"S": ["x.jpg"]}


def test_overwrite_updates(isolated_cache, tmp_path):
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    ref_history.save_chosen(ref_root, {"S": ["x.jpg"]})
    ref_history.save_chosen(ref_root, {"S": ["y.jpg", "z.jpg"]})
    assert ref_history.get_chosen(ref_root) == {"S": ["y.jpg", "z.jpg"]}
