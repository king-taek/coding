"""요청 6 — 기준 폴더별 '직접 고른 기준 사진' 기록 저장/로드."""

from __future__ import annotations

from pathlib import Path

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


def _siblings(name):
    return sorted(name.parent.glob(name.name + ".corrupt-*"))


def test_corrupt_history_is_moved_aside_then_new_save_works(isolated_cache, tmp_path):
    f = ref_history._file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('{"D:/ref": {"slots": {"S": ["a.jpg"]', encoding="utf-8")   # 잘린 JSON
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    assert ref_history.get_chosen(ref_root) == {}
    kept = _siblings(f)
    assert len(kept) == 1 and "a.jpg" in kept[0].read_text(encoding="utf-8")
    ref_history.save_chosen(ref_root, {"S": ["b.jpg"]})
    assert ref_history.get_chosen(ref_root) == {"S": ["b.jpg"]}
    assert len(_siblings(f)) == 1                    # 원본 보존


def test_transient_read_error_does_not_overwrite_history(isolated_cache, tmp_path,
                                                         monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    ref_history.save_chosen(other, {"S1": ["keep.jpg"]})
    f = ref_history._file()
    before = f.read_text(encoding="utf-8")

    real = Path.read_text

    def flaky(self, *a, **k):
        if self == f:
            raise PermissionError(13, "busy")
        return real(self, *a, **k)
    Path.read_text = flaky                  # monkeypatch.undo() 는 HOME 격리까지 되돌려 직접 복원
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    try:
        ref_history.save_chosen(ref_root, {"S": ["x.jpg"]})  # 읽지 못한 채 쓰면 other 를 잃는다
    finally:
        Path.read_text = real
    assert f.read_text(encoding="utf-8") == before
    assert _siblings(f) == []
    assert ref_history.get_chosen(other) == {"S1": ["keep.jpg"]}


def test_missing_file_is_normal(isolated_cache, tmp_path):
    ref_root = tmp_path / "ref"
    ref_root.mkdir()
    ref_history.save_chosen(ref_root, {"S": ["x.jpg"]})
    assert ref_history.get_chosen(ref_root) == {"S": ["x.jpg"]}
    assert _siblings(ref_history._file()) == []
