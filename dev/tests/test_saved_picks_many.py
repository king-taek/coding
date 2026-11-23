"""자동 추천 저장분(:mod:`threshold.saved_picks`) — 여러 웨이퍼를 한 번에 읽고 쓰기(Overkill 튜닝 폴더 빼기 렉, 2026-11-23).

예전에는 화면을 그릴 때마다 웨이퍼마다 파일을 다시 읽고 풀고, 폴더 키를 만들려고 NAS 에 resolve 를 물었다."""

from __future__ import annotations

import os
from pathlib import Path

from aoi_verification.app.threshold import saved_picks as SP


def test_saved_picks_many_equals_one_by_one(tmp_path, monkeypatch):
    """load_many · save_many 는 폴더마다 load · save 를 차례로 부른 것과 같은 파일 · 결과를 낸다(파일은 한 번 읽는다)."""
    a, b, c = (tmp_path / n for n in "abc")
    for f in (a, b, c):
        f.mkdir()
    SP.save(a, 10, {("Zones", 1): ({1, 2}, {3})})
    SP.save(c, 7, {("Zones", 2): ({4}, set())})
    reads = []
    orig = SP._read_all
    monkeypatch.setattr(SP, "_read_all", lambda: reads.append(1) or orig())
    got = SP.load_many([(a, 10), (b, 5), (c, 99)])
    assert len(reads) == 1
    monkeypatch.setattr(SP, "_read_all", orig)
    assert got == [SP.load(a, 10), SP.load(b, 5), SP.load(c, 99)]
    assert got[0] == {("Zones", 1): ({1, 2}, {3})} and got[1] == {} and got[2] == {}     # c 는 Defect 수가 달라 안 쓴다

    items = [(a, 10, {}), (b, 5, {("Zones", 9): ({0}, {1})}), (c, 7, {("Zones", 2): ({4, 5}, set())})]
    before = SP._path().read_text(encoding="utf-8")
    for f, n, z in items:
        SP.save(f, n, z)
    one_by_one = SP._path().read_text(encoding="utf-8")
    SP._path().write_text(before, encoding="utf-8")
    SP.save_many(items)
    assert SP._path().read_text(encoding="utf-8") == one_by_one


def test_save_many_writes_nothing_when_nothing_changes(tmp_path, monkeypatch):
    """빈 것을 빈 것으로 — 예전 save 처럼 파일을 건드리지 않는다."""
    wrote = []
    monkeypatch.setattr(Path, "write_text", lambda *a, **k: wrote.append(a))
    SP.save_many([(tmp_path, 3, {}), (tmp_path / "x", 1, {("Zones", 1): (set(), set())})])
    assert wrote == []


def test_folder_key_resolves_once_and_not_on_error(tmp_path, monkeypatch):
    """resolve(NAS 왕복)는 폴더마다 한 번 — 실패한 키는 기억하지 않는다(잠깐 끊긴 NAS 때문에 저장분을 잃지 않게)."""
    calls = []
    orig = Path.resolve
    fail = {"on": True}

    def res(self, strict=False):
        calls.append(self)
        if fail["on"]:
            raise OSError("NAS 끊김")
        return orig(self, strict)

    monkeypatch.setattr(Path, "resolve", res)
    f = tmp_path / "w"
    assert SP.folder_key(f) == os.path.normcase(str(f))
    fail["on"] = False
    k = SP.folder_key(f)
    assert k == os.path.normcase(str(orig(f)))
    n = len(calls)
    assert SP.folder_key(f) == k and len(calls) == n          # 두 번째부터는 다시 묻지 않는다
