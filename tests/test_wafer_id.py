"""KLA slot명(WaferID) 해석 — 파일명 파싱 + WaferID/폴더명 병합 (순수 로직)."""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import (ImageItem, ScanResult, Slot,
                                              drop_empty_unmatched)
from aoi_verification.app.utils import wafer_id


def _slot(name, side, filenames):
    items = [ImageItem(slot=name, path=Path(f"/{side}/{name}/{fn}"), side=side)
             for fn in filenames]
    if side == "ref":
        return Slot(name=name, ref_images=items, val_images=[])
    return Slot(name=name, ref_images=[], val_images=items)


def _scan(ref_specs, val_specs) -> ScanResult:
    slots = {}
    for n, fns in ref_specs.items():
        slots[n] = _slot(n, "ref", fns)
    for n, fns in val_specs.items():
        slots[n] = _slot(n, "val", fns)
    return ScanResult(slots=slots,
                      ref_only=list(ref_specs), val_only=list(val_specs))


# ---------------------------------------------------------------------------
# 파일명 → WaferID 파싱
# ---------------------------------------------------------------------------
def test_parse_wafer_id_from_filename():
    assert wafer_id.parse_wafer_id_from_filename(
        "W6459153XYF5_3_0_23_1.jpg") == "W6459153XYF5"
    assert wafer_id.parse_wafer_id_from_filename(
        "00NJ3159XYC1_0_-3_7_2.jpg") == "00NJ3159XYC1"
    assert wafer_id.parse_wafer_id_from_filename(
        "00nwv257xya5_-1_-1_23_3.jpg") == "00NWV257XYA5"   # 대문자 정규화


def test_parse_wafer_id_rejects_non_wafer_format():
    # WaferID 형식이 아니면(숫자 0개 prefix) None → OCR 폴백 대상.
    assert wafer_id.parse_wafer_id_from_filename(
        "FrontSideADRImg_544131.jpg") is None
    assert wafer_id._is_wafer_id("FrontSideADRImg") is False
    assert wafer_id._is_wafer_id("W6459153XYF5") is True


def test_folder_wafer_id_majority_vote():
    items = [ImageItem("d", Path(f"/d/{fn}"), "val") for fn in (
        "W6459153XYF5_1.jpg", "W6459153XYF5_2.jpg", "GARBAGEname_3.jpg")]
    assert wafer_id.folder_wafer_id_from_filenames(items) == "W6459153XYF5"


# ---------------------------------------------------------------------------
# WaferID/폴더명 키로 병합
# ---------------------------------------------------------------------------
def test_merge_by_wafer_id_filename_prefix():
    """val 폴더명이 WaferID, ref(KLA) 파일명 prefix 가 같은 WaferID → 병합."""
    sr = _scan(
        ref_specs={"KLA_RAW_07": ["W6459080XYHX_3_0_23_1.jpg"]},
        val_specs={"W6459080XYHX": ["81090.137592.c.1.jpg"]},
    )
    wid_ref = {"KLA_RAW_07": wafer_id.folder_wafer_id_from_filenames(
        sr.slots["KLA_RAW_07"].ref_images)}
    paired = wafer_id.merge_unmatched_by_wafer_id(sr, wid_ref, {})
    assert paired == [("KLA_RAW_07", "W6459080XYHX")]
    assert sr.common_slot_names == ["KLA_RAW_07"]   # 병합 slot명 = ref 폴더명 유지
    assert sr.ref_only == [] and sr.val_only == []


def test_merge_no_match_when_wafer_id_differs():
    sr = _scan(
        ref_specs={"RAW_A": ["ZZZZ1234XYZ9_1.jpg"]},
        val_specs={"W6459080XYHX": ["b.jpg"]},
    )
    wid_ref = {"RAW_A": "ZZZZ1234XYZ9"}
    assert wafer_id.merge_unmatched_by_wafer_id(sr, wid_ref, {}) == []
    assert sr.ref_only == ["RAW_A"] and sr.val_only == ["W6459080XYHX"]


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
