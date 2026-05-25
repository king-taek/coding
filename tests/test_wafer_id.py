"""파일명 ↔ 폴더명 포함관계 기반 slot 매칭 검증(OCR/패턴 무관, 순수 로직)."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import (ImageItem, ScanResult, Slot,
                                              drop_empty_unmatched)
from aoi_verification.app.utils import wafer_id


def _slot(name, side, filenames):
    """한쪽 전용 폴더 Slot 생성 — side('ref'/'val') 에 주어진 파일명들로."""
    items = [ImageItem(slot=name, path=Path(f"/{side}/{name}/{fn}"), side=side)
             for fn in filenames]
    if side == "ref":
        return Slot(name=name, ref_images=items, val_images=[])
    return Slot(name=name, ref_images=[], val_images=items)


def _scan(ref_specs, val_specs) -> ScanResult:
    """ref_specs/val_specs = {폴더명: [파일명,...]} (모두 한쪽 전용)."""
    slots = {}
    for n, fns in ref_specs.items():
        slots[n] = _slot(n, "ref", fns)
    for n, fns in val_specs.items():
        slots[n] = _slot(n, "val", fns)
    return ScanResult(slots=slots,
                      ref_only=list(ref_specs), val_only=list(val_specs))


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------
def test_name_in_files_substring_case_insensitive():
    assert wafer_id._name_in_files(
        "W6459080XYH", ["W6459080XYH2_3_0_23_1".upper()]) is True
    assert wafer_id._name_in_files(
        "w6459080xyh", ["W6459080XYH2_3_0_23_1"]) is True


def test_name_in_files_too_short_rejected():
    # 너무 짧은 이름은 우연 일치를 막기 위해 제외.
    assert wafer_id._name_in_files("12", ["12_345.jpg"]) is False


def test_name_in_files_no_match():
    assert wafer_id._name_in_files(
        "W6459080XYH", ["81090.137592.c.212779204.1"]) is False


# ---------------------------------------------------------------------------
# match_by_filename_containment
# ---------------------------------------------------------------------------
def test_match_val_folder_name_in_ref_filenames():
    """val 폴더가 정확한 slot명, ref(KLA) 파일명에 그 이름이 포함 → 매칭."""
    sr = _scan(
        ref_specs={"KLA_RAW_07": ["W6459080XYH2_3_0_23_1.jpg",
                                  "W6459080XYH2_5_1_10_2.jpg"]},
        val_specs={"W6459080XYH": ["81090.137592.c.212779204.1.jpg"]},
    )
    paired = wafer_id.match_by_filename_containment(sr)
    assert paired == [("KLA_RAW_07", "W6459080XYH")]
    # 정확한 쪽(val) 폴더명이 slot명이 된다.
    assert sr.common_slot_names == ["W6459080XYH"]
    merged = sr.slots["W6459080XYH"]
    assert merged.has_both
    assert all(it.slot == "W6459080XYH"
               for it in merged.ref_images + merged.val_images)
    assert sr.ref_only == [] and sr.val_only == []


def test_match_ref_folder_name_in_val_filenames():
    """ref 폴더가 정확한 slot명, val(KLA) 파일명에 포함 → 매칭, slot명=ref명."""
    sr = _scan(
        ref_specs={"W6459080XYH": ["anything.jpg"]},
        val_specs={"KLA_RAW_07": ["W6459080XYH2_3_0_23_1.jpg"]},
    )
    paired = wafer_id.match_by_filename_containment(sr)
    assert paired == [("W6459080XYH", "KLA_RAW_07")]
    assert sr.common_slot_names == ["W6459080XYH"]
    assert sr.ref_only == [] and sr.val_only == []


def test_no_match_when_name_absent():
    sr = _scan(
        ref_specs={"RAW_A": ["81090.137592.c.1.jpg"]},
        val_specs={"W6459080XYH": ["81090.137592.c.2.jpg"]},
    )
    assert wafer_id.match_by_filename_containment(sr) == []
    assert sr.ref_only == ["RAW_A"] and sr.val_only == ["W6459080XYH"]


def test_partial_only_matching_pairs():
    sr = _scan(
        ref_specs={"KLA1": ["W6459080XYH2_1.jpg"], "KLA2": ["zzzz_1.jpg"]},
        val_specs={"W6459080XYH": ["a.jpg"], "W0000000XYZ": ["b.jpg"]},
    )
    paired = wafer_id.match_by_filename_containment(sr)
    assert paired == [("KLA1", "W6459080XYH")]
    assert sr.common_slot_names == ["W6459080XYH"]
    assert sr.ref_only == ["KLA2"] and sr.val_only == ["W0000000XYZ"]


# ---------------------------------------------------------------------------
# drop_empty_unmatched
# ---------------------------------------------------------------------------
def test_drop_empty_unmatched_skips_imageless_folders():
    slots = {
        "R_ok": Slot("R_ok",
                     ref_images=[ImageItem("R_ok", Path("/r/a.jpg"), "ref")],
                     val_images=[]),
        "R_empty": Slot("R_empty", ref_images=[], val_images=[]),
        "V_ok": Slot("V_ok", ref_images=[],
                     val_images=[ImageItem("V_ok", Path("/v/a.jpg"), "val")]),
        "V_empty": Slot("V_empty", ref_images=[], val_images=[]),
    }
    sr = ScanResult(slots=slots,
                    ref_only=["R_ok", "R_empty"],
                    val_only=["V_ok", "V_empty"])
    drop_empty_unmatched(sr)
    assert sr.ref_only == ["R_ok"]
    assert sr.val_only == ["V_ok"]
