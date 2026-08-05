"""KLA 정보파일 탐색의 **성능 계약** — 사진은 stat 하지 않는다 (순수 로직).

Setup 첫 화면에서 폴더를 고르면 `_detect_die_geometry` 가 슬롯마다
`kla_info.load_folder` 를 부르고, 그 안의 `_find_info_file` 이 폴더를 훑는다.
예전에는 조건이 ``p.is_file() and 확장자검사`` 순서라 **사진 한 장마다 stat** 이 돌았다
— 사진 500장 × 슬롯 20개면 stat 1만 회고, NAS 에서는 그게 그대로 왕복 1만 회라
폴더를 고르는 순간 창이 몇 초간 멈췄다(실측 10초).

지금은 이름만 보면 되는 확장자 검사를 앞에 두어 사진이 stat 대상에서 빠진다.
세 조건 모두 부작용이 없어 **결과는 바뀌지 않는다** — 아래 계약 테스트와 동치성
테스트가 둘 다를 못 박는다.  누가 "가독성" 을 이유로 순서를 되돌리면 여기서 걸린다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords import kla_info

_HEADER = 'DiePitch 3.7247930000e+004 4.4905340000e+004;\nWaferID "W6459076XYG1";\n'


@pytest.fixture
def counting_stat(monkeypatch):
    """`Path.is_file()` 호출을 세고 대상 이름을 기록한다."""
    seen: list[str] = []
    real = Path.is_file

    def spy(self):
        seen.append(self.name)
        return real(self)

    monkeypatch.setattr(Path, "is_file", spy)
    return seen


def _slot(tmp_path, *, info_name: str | None, n_img: int = 40) -> Path:
    d = tmp_path / "slot"
    d.mkdir(parents=True)
    for i in range(n_img):
        (d / f"W1_2_0_{i}_2.jpg").write_bytes(b"")
    if info_name is not None:
        (d / info_name).write_text(_HEADER, encoding="utf-8")
    return d


# ── 성능 계약 ────────────────────────────────────────────────────────────
def test_find_info_file_does_not_stat_photos(tmp_path, counting_stat):
    """★ 사진(.jpg)은 stat 대상이 아니다 — 확장자로 먼저 걸러야 한다."""
    d = _slot(tmp_path, info_name="WAFERINFO")
    assert kla_info._find_info_file(d).name == "WAFERINFO"
    assert not [n for n in counting_stat if n.endswith(".jpg")], (
        "사진을 stat 했다 — `is_file()` 이 확장자 검사보다 앞에 있다")


def test_info_candidates_does_not_stat_photos(tmp_path, counting_stat):
    """`read_wafer_id` 가 쓰는 후보 열거도 같은 계약을 지킨다."""
    d = _slot(tmp_path, info_name="WAFERINFO")
    assert [p.name for p in kla_info._info_candidates(d)] == ["WAFERINFO"]
    assert not [n for n in counting_stat if n.endswith(".jpg")]


def test_stat_count_does_not_grow_with_photo_count(tmp_path, monkeypatch):
    """사진이 10배가 돼도 stat 횟수는 그대로다(사진 수와 무관)."""
    counts = []
    for n_img in (20, 200):
        seen: list[str] = []
        real = Path.is_file
        monkeypatch.setattr(Path, "is_file",
                            lambda self: (seen.append(self.name), real(self))[1])
        d = _slot(tmp_path / f"r{n_img}", info_name="WAFERINFO", n_img=n_img)
        kla_info._find_info_file(d)
        counts.append(len(seen))
        monkeypatch.undo()
    assert counts[0] == counts[1], f"사진 수에 비례해 stat 이 늘었다: {counts}"


# ── 동치성 — 순서를 바꿔도 고르는 파일이 같다 ──────────────────────────────
@pytest.mark.parametrize("extra, expected", [
    (None,                      "WAFERINFO"),   # 정보파일 하나뿐
    (("dir", "RESULTS"),        "WAFERINFO"),   # 하위 폴더는 후보가 아니다
    (("dir", "thumbs.jpg"),     "WAFERINFO"),   # 사진 이름의 폴더도 후보가 아니다
    (("file", ".hidden"),       "WAFERINFO"),   # 숨김 파일 제외
    (("file", "log.ini"),       "WAFERINFO"),   # 제외 확장자
    (("file", "LOTLOG"),        None),          # 후보 2개 → 확신 불가
])
def test_selection_is_unchanged_by_layout(tmp_path, extra, expected):
    d = _slot(tmp_path, info_name="WAFERINFO")
    if extra is not None:
        kind, name = extra
        (d / name).mkdir() if kind == "dir" else (d / name).write_bytes(b"x")
    got = kla_info._find_info_file(d)
    assert (got.name if got else None) == expected


def test_unreadable_photo_does_not_break_lookup(tmp_path, monkeypatch):
    """읽을 수 없는 사진이 섞여도 정보파일을 찾는다.

    `Path.is_file()` 은 EACCES 를 삼키지 않고 **다시 던진다**(무시하는 건 errno
    2/20/9/40 뿐).  사진을 stat 하던 시절에는 권한 없는 사진 한 장이
    `load_folder_raw` 밖으로 `PermissionError` 를 새어나가게 해 **매칭이 통째로
    실패**했다.  이제 사진은 stat 하지 않으므로 그 경로 자체가 없다."""
    d = _slot(tmp_path, info_name="WAFERINFO")
    real = Path.stat

    def deny(self, *a, **kw):
        if self.name == "W1_2_0_7_2.jpg":
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", deny)
    assert kla_info._find_info_file(d).name == "WAFERINFO"
    assert kla_info.read_wafer_id(d) == "W6459076XYG1"
