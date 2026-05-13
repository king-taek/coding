"""learning.dataset — append/load round-trip, dedup, content-hash."""

from pathlib import Path

from aoi_verification.app.learning.dataset import TrainingDataStore
from aoi_verification.app.models.result import MatchResult


def _mk_image(tmp: Path, name: str, payload: bytes) -> Path:
    p = tmp / name
    p.write_bytes(payload)
    return p


def test_append_session_appends_lines(isolated_cache, tmp_path):
    a = _mk_image(tmp_path, "a.jpg", b"\x01" * 1024)
    b = _mk_image(tmp_path, "b.jpg", b"\x02" * 1024)
    store = TrainingDataStore()
    matches = [MatchResult(slot="S01", ref_path=a, val_path=b,
                           score=0.91, direction="A→B")]
    n = store.append_session(matches, ref_machine="1호기", val_machine="3호기")
    assert n == 1
    assert store.count() == 1
    pairs = store.load_all()
    assert pairs[0].slot == "S01"
    assert pairs[0].ref_hash and pairs[0].val_hash       # 컨텐츠 해시 기록됨
    assert pairs[0].ref_machine == "1호기"


def test_dedup_by_content_hash(isolated_cache, tmp_path):
    a = _mk_image(tmp_path, "a.jpg", b"\x01" * 1024)
    b = _mk_image(tmp_path, "b.jpg", b"\x02" * 1024)
    store = TrainingDataStore()
    m = MatchResult(slot="S01", ref_path=a, val_path=b,
                    score=0.91, direction="A→B")
    assert store.append_session([m], ref_machine="A", val_machine="B") == 1
    # 같은 사진을 한 번 더 — content hash 가 같아 dedup
    assert store.append_session([m], ref_machine="A", val_machine="B") == 0
    assert store.count() == 1


def test_path_change_with_same_content_still_dedups(isolated_cache, tmp_path):
    a = _mk_image(tmp_path, "a.jpg", b"\x01" * 1024)
    b = _mk_image(tmp_path, "b.jpg", b"\x02" * 1024)
    store = TrainingDataStore()
    m1 = MatchResult(slot="S01", ref_path=a, val_path=b,
                     score=0.91, direction="A→B")
    store.append_session([m1], ref_machine="A", val_machine="B")
    # 동일 컨텐츠를 다른 폴더로 이동시킨 시나리오
    moved_dir = tmp_path / "moved"
    moved_dir.mkdir()
    a2 = _mk_image(moved_dir, "a.jpg", b"\x01" * 1024)
    b2 = _mk_image(moved_dir, "b.jpg", b"\x02" * 1024)
    m2 = MatchResult(slot="S01", ref_path=a2, val_path=b2,
                     score=0.91, direction="A→B")
    assert store.append_session([m2], ref_machine="A", val_machine="B") == 0
    assert store.count() == 1
