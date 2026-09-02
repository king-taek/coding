"""파일 무시 규칙(웨이퍼 전경 사진 + `.t.` 사진) + '일부 슬롯만 진행' 스캔 범위."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models import slot as slot_mod


def test_dot_t_token_is_ignored_again():
    """★ 점으로 구분된 't' 토큰 파일은 **항상 뺀다**(사용자 요청).

    이 자리는 네 번 바뀌었다(`is_ignored_name` 주석) — 마지막 결정은 '항상 뺀다' 다.
    부분 문자열이 아니라 **토큰**이다: `test.123.jpg` 는 멀쩡한 사진이다.
    """
    assert slot_mod.is_ignored_name("-86955.68631.t.1.jpg") is True
    assert slot_mod.is_ignored_name("abc.t.png") is True
    assert slot_mod.is_ignored_name("t.jpg") is True
    assert slot_mod.is_ignored_name("-86955.68631.1.jpg") is False
    assert slot_mod.is_ignored_name("test.123.jpg") is False
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
    _touch(folder / "b.t.1.jpg")          # `.t.` — 항상 뺀다
    _touch(folder / "c.png")
    _touch(folder / "-86955.68631.t.1.jpg")  # `.t.` — 항상 뺀다
    _touch(folder / "CognexInSight17xx_Bottom_Slot3.jpg")   # 웨이퍼 전경 — 무시
    _touch(folder / "notes.txt")          # 이미지 아님

    names = sorted(p.name for p in slot_mod._list_images(folder))
    assert names == ["a.jpg", "c.png"]
    # 슬롯 선택 팝업의 장수도 같은 열거를 쓴다 — 화면 숫자와 실제 처리 장수가 같다.
    assert slot_mod.count_images(folder) == 2


def test_scan_only_opens_the_selected_slots(tmp_path, monkeypatch):
    """'일부 슬롯만 진행' — 스캔이 **그 폴더들만** 연다(사용자 신고: 전부 훑어서 느리다).

    예전에는 전부 훑은 뒤 `main_window._on_scan_done` 이 결과를 줄였다.  NAS 에서는
    폴더마다 왕복이라 25슬롯 중 3개만 골라도 25개를 기다렸다."""
    ref = tmp_path / "ref"
    val = tmp_path / "val"
    for root in (ref, val):
        for s in ("S01", "S02", "S03"):
            _touch(root / s / "img.jpg")

    opened: list[str] = []
    real = slot_mod._list_images
    monkeypatch.setattr(slot_mod, "_list_images",
                        lambda folder: (opened.append(Path(folder).name),
                                        real(folder))[1])
    sel = {"S01", "S03"}
    sr = slot_mod.scan(ref, val, only=sel)
    assert sr.common_slot_names == ["S01", "S03"]
    assert "S02" not in sr.slots, "고르지 않은 슬롯이 결과에 남았다"
    assert set(opened) == sel, f"고르지 않은 폴더를 열었다: {sorted(set(opened))}"

    # `only=None` 은 전체 — 기존 동작 그대로.
    opened.clear()
    assert slot_mod.scan(ref, val).common_slot_names == ["S01", "S02", "S03"]
    assert set(opened) == {"S01", "S02", "S03"}


def test_only_restricts_one_sided_slots_too(tmp_path):
    """한쪽에만 있는 폴더도 고른 것만 남는다 — ref_only/val_only 가 이 목록에서 나온다."""
    _touch(tmp_path / "ref" / "S01" / "a.jpg")
    _touch(tmp_path / "ref" / "R9" / "a.jpg")       # 기준 전용
    _touch(tmp_path / "val" / "S01" / "a.jpg")
    _touch(tmp_path / "val" / "V9" / "a.jpg")       # 검증 전용
    sr = slot_mod.scan(tmp_path / "ref", tmp_path / "val", only={"S01", "V9"})
    assert sr.ref_only == [] and sr.val_only == ["V9"]
    assert sorted(sr.slots) == ["S01", "V9"]


def test_list_slot_dirs_wrapper(tmp_path):
    ref = tmp_path / "ref"
    for s in ("A", "B"):
        (ref / s).mkdir(parents=True)
    (ref / "loose.jpg").write_bytes(b"\x00")     # 파일은 슬롯 아님
    dirs = slot_mod.list_slot_dirs(ref)
    assert sorted(dirs.keys()) == ["A", "B"]
