"""요청 3·4 — `.t.` 토큰 파일 무시 + '일부 슬롯만 진행' 슬롯 필터."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models import slot as slot_mod


def test_is_ignored_name_dot_t_token():
    # 점으로 구분된 't' 토큰 → 무시
    assert slot_mod.is_ignored_name("-86955.68631.t.1.jpg") is True
    assert slot_mod.is_ignored_name("abc.t.png") is True
    assert slot_mod.is_ignored_name("t.jpg") is True
    # 't' 토큰이 아니면 통과
    assert slot_mod.is_ignored_name("-86955.68631.1.jpg") is False
    assert slot_mod.is_ignored_name("test.123.jpg") is False     # 'test' != 't'
    assert slot_mod.is_ignored_name("photo.jpg") is False


def test_is_ignored_name_wafer_macro_photo():
    """결함 사진이 아닌 웨이퍼 전경 사진은 열거 단계에서 제외한다.

    슬롯마다 1장씩 들어 있고 ColorImageGrabingInfo.ini 에 항목이 없어 좌표를 만들 수
    없다(실측: 사진 수 = INI 항목 수 + 1 이 전 폴더에서 성립)."""
    assert slot_mod.is_ignored_name(
        "CognexInSight17xx_Bottom_OnPal_Station1_Slot21.jpg") is True
    assert slot_mod.is_ignored_name("cognexinsight9000_Top_Slot3.jpg") is True
    # 결함 사진(점 좌표 파일명)은 그대로 통과
    assert slot_mod.is_ignored_name("188063.58548.c.-210404622.2.jpeg") is False


def test_zero_defect_slot_survives_as_one_sided(tmp_path):
    """★ 웨이퍼 전경 사진을 빼면 결함 0건 슬롯은 사진 0장이 된다 — 사라지면 안 된다.

    ``push_one_sided_to_unmatched`` 가 '기준/검증 전용' 으로 되돌려 결과에 남긴다
    (안 그러면 common_slot_names 에도 ref_only/val_only 에도 없어 통째로 사라진다)."""
    ref, val = tmp_path / "ref", tmp_path / "val"
    for base, has_defect in ((ref, True), (val, False)):
        d = base / "W1"
        d.mkdir(parents=True)
        _touch(d / "CognexInSight17xx_Bottom_OnPal_Station1_Slot21.jpg")
        if has_defect:
            _touch(d / "188063.58548.c.-210404622.2.jpeg")

    sr = slot_mod.scan(ref, val)
    assert sr.slots["W1"].ref_images and not sr.slots["W1"].val_images
    assert sr.common_slot_names == []            # 그대로 두면 사라진다
    slot_mod.push_one_sided_to_unmatched(sr)
    assert sr.ref_only == ["W1"]                 # 결과에 남는다


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\x00")


def test_list_images_skips_ignored(tmp_path):
    folder = tmp_path / "S01"
    _touch(folder / "a.jpg")
    _touch(folder / "b.t.1.jpg")          # 무시
    _touch(folder / "c.png")
    _touch(folder / "-86955.68631.t.1.jpg")  # 무시
    _touch(folder / "notes.txt")          # 이미지 아님

    names = sorted(p.name for p in slot_mod._list_images(folder))
    assert names == ["a.jpg", "c.png"]


def test_scan_then_slot_subset_filter(tmp_path):
    ref = tmp_path / "ref"
    val = tmp_path / "val"
    for root in (ref, val):
        for s in ("S01", "S02", "S03"):
            _touch(root / s / "img.jpg")

    sr = slot_mod.scan(ref, val)
    assert sr.common_slot_names == ["S01", "S02", "S03"]

    # main_window._on_start 의 필터 로직과 동일하게 부분집합으로 축소.
    sel = {"S01", "S03"}
    sr.slots = {n: s for n, s in sr.slots.items() if n in sel}
    assert sr.common_slot_names == ["S01", "S03"]


def test_list_slot_dirs_wrapper(tmp_path):
    ref = tmp_path / "ref"
    for s in ("A", "B"):
        (ref / s).mkdir(parents=True)
    (ref / "loose.jpg").write_bytes(b"\x00")     # 파일은 슬롯 아님
    dirs = slot_mod.list_slot_dirs(ref)
    assert sorted(dirs.keys()) == ["A", "B"]
