"""KLA 정보파일 탐색 — 윈도 썸네일 캐시(``Thumbs.db``)는 정보파일 후보가 아니다.

실물(현택님 수집 zip, 2026-11-15) ``dev/좌표 확인/KLA/예시4(확장자 없는 정보파일)``:
Conder CTY RDL4 00NUR030XYA3 — 확장자 없는 정보파일 + ``.pass`` + ``Thumbs.db``.
``Thumbs.db`` 는 사진 미리보기가 박혀 있어 저장소에 담지 않고(사진으로 친다) 테스트가 가짜를 만든다.
고치기 전에는 후보가 둘이라 :func:`kla_info._find_info_file` 이 None 을 줘 좌표 있는 7장이 통째로 빠졌다.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aoi_verification.app.coords import clear_caches, kla_info

_GOLD = Path(__file__).resolve().parents[1] / "좌표 확인" / "KLA" / "예시4(확장자 없는 정보파일)"
_INFO = "00NUR030XYA3"


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _names() -> list[str]:
    txt = (_GOLD / "사진목록.txt").read_text(encoding="utf-8")
    return [ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith("#")]


def _slot(root: Path, *, thumbs: bool) -> Path:
    d = root / _INFO
    shutil.copytree(_GOLD, d, ignore=shutil.ignore_patterns("사진목록.txt"))
    for name in _names():
        (d / name).write_bytes(b"")
    if thumbs:
        (d / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504)  # OLE 머리만 흉내
    return d


def test_thumbs_db_is_not_an_info_candidate(tmp_path):
    d = _slot(tmp_path, thumbs=True)
    assert kla_info._find_info_file(d).name == _INFO
    assert [p.name for p in kla_info._info_candidates(d)] == [_INFO]


def test_coordinates_are_the_same_with_or_without_thumbs_db(tmp_path):
    with_db = kla_info.load_folder(_slot(tmp_path / "a", thumbs=True))
    clear_caches()
    without = kla_info.load_folder(_slot(tmp_path / "b", thumbs=False))
    assert len(_names()) == 30
    assert len(with_db) == 7                      # 정보파일 TiffFileName 이 있는 사진만 좌표가 있다
    assert {k: (c.col, c.row, c.x, c.y) for k, c in with_db.items()} == \
           {k: (c.col, c.row, c.x, c.y) for k, c in without.items()}


def test_001_still_comes_first(tmp_path):
    d = _slot(tmp_path, thumbs=True)
    (d / "other.001").write_text('WaferID "X";\n', encoding="utf-8")
    assert kla_info._find_info_file(d).name == "other.001"
