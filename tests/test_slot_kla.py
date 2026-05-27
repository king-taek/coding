"""KLA 폴더의 하위 .001 파일명에서 slot 명을 뽑아 scan 이 정합하는지 검증 (#4)."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import _kla_slot_name, scan


def _touch(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")


def test_kla_slot_name_extracts_token_after_last_underscore(tmp_path):
    folder = tmp_path / "GUID_FOLDER"
    _touch(folder / "00cd0487-998d-410c-adef-e3c8a4f0656b_TB500INT.271@6324_W6459079XYE1.001")
    assert _kla_slot_name(folder) == "W6459079XYE1"


def test_kla_slot_name_none_without_001(tmp_path):
    folder = tmp_path / "Slot_01"
    _touch(folder / "a.jpeg")
    assert _kla_slot_name(folder) is None


def test_scan_matches_kla_folder_to_named_slot(tmp_path):
    # ref 측: slot 명으로 명명된 폴더.
    ref_root = tmp_path / "ref"
    _touch(ref_root / "W6459079XYE1" / "img1.jpeg")
    # val 측: KLA 폴더(임의 이름) + 하위 .001 에 slot 명 인코딩.
    val_root = tmp_path / "val"
    kla_dir = val_root / "LOT_AB123"
    _touch(kla_dir / "xx_TB500INT.271@6324_W6459079XYE1.001")
    _touch(kla_dir / "shot1.jpeg")

    sr = scan(ref_root, val_root)

    # .001 에서 뽑은 slot 키로 ref/val 이 같은 slot 에 모인다.
    assert "W6459079XYE1" in sr.slots
    slot = sr.slots["W6459079XYE1"]
    assert slot.has_both
    assert slot.kla_folder == "LOT_AB123"
    assert sr.ref_only == [] and sr.val_only == []
    # .001 은 이미지로 취급되지 않는다(검증 이미지는 shot1.jpeg 1장뿐).
    assert [p.name for p in (i.path for i in slot.val_images)] == ["shot1.jpeg"]
