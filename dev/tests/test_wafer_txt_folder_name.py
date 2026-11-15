"""LIVE 파일명 LOT 의 웨이퍼 맵 — 슬롯 폴더 이름이 맵 이름과 조금 다를 때(줄임 · 메모).

실물(현택님 수집 zip, 2026-11-15, Conder ``179. INTP-KENDALL-104-2608 (CTY)``):

* ``4. PIDS5`` — 폴더 ``00NUR031XY`` · ``0NUR043XYC`` · ``0NUR076XYH`` 가 맵 ``00NUR031XYE5.txt`` ·
  ``00NUR043XYC7.txt`` · ``00NUR076XYH0.txt`` 의 이름 일부다(사람이 줄임).
* ``2. PIDS3 - 복사본`` — 폴더 ``00NUR050XYC0 추가 SRD 필요`` (이름 뒤에 메모).

고치기 전에는 ``<폴더 이름>.txt`` 만 찾아 맵을 못 읽었고, Wafer map 이 die 크기를 추정해 빨간
경고를 띄웠다(11 슬롯 21장).  아래 맵 글과 사진 파일명은 그 실물 그대로다(사진 내용은 쓰지 않으므로 빈 파일).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.coords import clear_caches, resolve_batch, wafer_map as wm, wafer_txt

_HEAD = ("DEVICE:KENDALL_INTP\nLOT:INTP-KENDALL-104-2608@{lot}\nWAFER:{w}\nFNLOC:180\nROWCT:6\n"
         "COLCT:10\nBCEQU:000\nREFPX:1\nREFPY:3\nDUTMS:mm\nXDIES:{dx}\nYDIES:{dy}\n")
_MAPS = {
    "00NUR031XYE5": """\
___ ___ ___ ___ 000 000 ___ ___ ___ ___
___ 000 000 000 000 000 000 031 000 ___
000 000 000 000 000 000 000 000 000 031
031 000 000 000 000 000 031 000 000 000
___ 031 031 000 000 031 000 000 000 ___
___ ___ ___ ___ 000 000 ___ ___ ___ ___""",
    "00NUR043XYC7": """\
___ ___ ___ ___ 000 000 ___ ___ ___ ___
___ 000 000 000 000 000 000 000 000 ___
000 000 000 000 031 000 000 000 000 000
000 000 000 000 000 000 000 000 000 000
___ 000 000 000 000 000 000 000 000 ___
___ ___ ___ ___ 000 000 ___ ___ ___ ___""",
    "00NUR044XYH1": """\
___ ___ ___ ___ 000 000 ___ ___ ___ ___
___ 000 000 000 009 000 000 000 009 ___
000 000 000 000 000 000 009 000 000 000
000 000 000 000 000 000 000 000 000 000
___ 000 000 000 000 000 000 000 031 ___
___ ___ ___ ___ 000 000 ___ ___ ___ ___""",
    "00NUR076XYH0": """\
___ ___ ___ ___ 000 000 ___ ___ ___ ___
___ 000 000 000 000 000 000 000 000 ___
000 001 000 000 000 000 000 000 000 000
000 000 000 000 000 000 000 000 000 000
___ 000 000 000 000 000 000 000 000 ___
___ ___ ___ ___ 031 000 ___ ___ ___ ___""",
    "00NUR050XYC0": """\
___ ___ ___ ___ 000 000 ___ ___ ___ ___
___ 000 000 000 000 000 000 000 000 ___
000 000 000 000 000 000 000 000 000 000
000 000 000 000 000 000 000 000 000 000
___ 000 000 000 000 000 000 000 000 ___
___ ___ ___ ___ 000 000 ___ ___ ___ ___""",          # 2. PIDS3 - 복사본 (bin 이 전부 000)
}
_P5 = "R_KENDALL_A0_PI5_CTY-PIDS5_"
_PHOTOS = {                       # 폴더 이름(실물) → 파일명(실물)
    "00NUR031XY": [
        "00NUR031XY_0_2_Foreign Material_17885.6456697211_38271.5144291541",
        "00NUR031XY_2_1_Foreign Material_11756.7576564525_39559.8241869519",
        "00NUR031XY_5_1_Foreign Material_3731.80337732515_21613.7313172474",
        "00NUR031XY_7_4_Foreign Material_3007.83127033064_25124.7782765763",
        "00NUR031XY_9_3_Foreign Material_14885.1849300381_33991.3986007568"],
    "0NUR043XYC": ["0NUR043XYC_4_3_Foreign Material_5300.65588772472_22820.6868298909"],
    "0NUR076XYH": [
        "0NUR076XYH_1_3_Other_10288.1839060556_27051.9821190799",
        "0NUR076XYH_4_0_Foreign Material_5829.50510130366_45143.0343558427"],
}
_WANT = {"00NUR031XY": "00NUR031XYE5", "0NUR043XYC": "00NUR043XYC7", "0NUR076XYH": "00NUR076XYH0"}


@pytest.fixture(autouse=True)
def _fresh():
    clear_caches()
    yield
    clear_caches()


def _write_map(lot: Path, w: str, *, lot_no="6323", dx="26.26", dy="46.62", name=None) -> None:
    body = "".join(f"RowData:{ln}\n" for ln in _MAPS[w].splitlines())
    (lot / f"{name or w}.txt").write_text(_HEAD.format(lot=lot_no, w=w, dx=dx, dy=dy) + body,
                                          encoding="utf-8")


def _pids5(tmp_path: Path) -> Path:
    lot = tmp_path / "4. PIDS5"
    lot.mkdir(parents=True)
    for w in ("00NUR031XYE5", "00NUR043XYC7", "00NUR044XYH1", "00NUR076XYH0"):
        _write_map(lot, w)
    for folder, stems in _PHOTOS.items():
        (lot / folder).mkdir()
        for s in stems:
            (lot / folder / f"{_P5}{s}.jpg").write_bytes(b"")
    return lot


@pytest.mark.parametrize("folder", list(_PHOTOS))
def test_short_folder_name_finds_its_map(tmp_path, folder):
    lot = _pids5(tmp_path)
    m = wafer_txt.load(lot / folder)
    assert m is not None and m.path.name == f"{_WANT[folder]}.txt"
    # 실물 근거: 사진 칸이 그 맵의 불량 bin 이다(LIVE row 는 아래부터 0).
    for p in (lot / folder).iterdir():
        col, row = (int(t) for t in p.stem[len(_P5) + len(folder) + 1:].split("_")[:2])
        assert (col, m.rows - 1 - row) in m.bad, p.name


def test_short_folder_name_map_has_no_estimate_warning(tmp_path):
    lot = _pids5(tmp_path)
    data = wm.build_map(resolve_batch(sorted((lot / "00NUR031XY").iterdir())))
    assert data.frame.pitch_assumed is False
    assert wm.map_warnings(data) == []


def test_memo_after_the_name_finds_its_map(tmp_path):
    lot = tmp_path / "2. PIDS3 - 복사본"
    lot.mkdir()
    _write_map(lot, "00NUR050XYC0", lot_no="6321", dx="26.59", dy="46.98")
    _write_map(lot, "00NUR043XYC7", lot_no="6321")
    slot = lot / "00NUR050XYC0 추가 SRD 필요"
    slot.mkdir()
    m = wafer_txt.load(slot)
    assert m is not None and m.path.name == "00NUR050XYC0.txt"
    assert (m.die_x, m.die_y) == (26590.0, 46980.0)


def test_exact_name_still_wins(tmp_path, monkeypatch):
    lot = _pids5(tmp_path)
    (lot / "00NUR031XYE5").mkdir()
    monkeypatch.setattr(wafer_txt, "_named_like", lambda _slot: pytest.fail("정확한 이름이 있는데 찾았다"))
    assert wafer_txt.load(lot / "00NUR031XYE5").path.name == "00NUR031XYE5.txt"


@pytest.mark.parametrize("folder", [
    "00NUR04",       # 043 · 044 둘 다 품는다 → 고르지 않는다
    "NUR031",        # 맵 이름 글자의 과반을 못 덮는다
    "00NUR099XY",    # 품는 맵이 없다
])
def test_ambiguous_or_weak_names_pick_nothing(tmp_path, folder):
    lot = _pids5(tmp_path)
    (lot / folder).mkdir()
    assert wafer_txt.load(lot / folder) is None


def test_single_map_lot_is_not_guessed(tmp_path):
    lot = tmp_path / "LOT"
    lot.mkdir()
    _write_map(lot, "00NUR031XYE5")
    (lot / "00NUR031XY").mkdir()
    assert wafer_txt.load(lot / "00NUR031XY") is None


def test_map_whose_header_names_another_wafer_is_refused(tmp_path):
    lot = _pids5(tmp_path)
    (lot / "00NUR031XYE5.txt").unlink()
    _write_map(lot, "00NUR043XYC7", name="00NUR031XYE5")        # 이름은 031 인데 머리는 043
    assert wafer_txt.load(lot / "00NUR031XY") is None
