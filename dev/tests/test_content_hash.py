"""utils.content_hash — 컨텐츠가 같으면 같은 해시, 다르면 다른 해시."""

from aoi_verification.app.utils.content_hash import content_hash


def test_same_bytes_yield_same_hash(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    payload = b"A" * 4096 + b"defect" + b"B" * 4096
    a.write_bytes(payload)
    b.write_bytes(payload)
    assert content_hash(a) == content_hash(b)
    assert len(content_hash(a)) == 40       # sha1 hex


def test_different_bytes_yield_different_hash(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"\x00" * 8192)
    b.write_bytes(b"\xff" * 8192)
    assert content_hash(a) != content_hash(b)


def test_size_disambiguates_zero_bytes(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"\x00" * 1024)
    b.write_bytes(b"\x00" * 2048)
    assert content_hash(a) != content_hash(b)


def test_missing_file_returns_empty(tmp_path):
    assert content_hash(tmp_path / "missing.bin") == ""
