"""WaferID(파일명 기반) 추출 / slot명 불일치 자동 병합 검증.

OCR 없이 파일명만 파싱하는 순수 로직(Qt 불필요).
"""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import (ImageItem, ScanResult, Slot,
                                              drop_empty_unmatched)
from aoi_verification.app.utils import wafer_id


# ---------------------------------------------------------------------------
# wafer_id_from_filename — 파일명에서 WaferID(slot명) 추출
# ---------------------------------------------------------------------------
def test_filename_kla_token():
    # 'XY'+글자 까지가 slot명, 뒤 인덱스 숫자/토큰은 버림.
    assert wafer_id.wafer_id_from_filename(
        "W6459080XYH2_3_0_23_1") == "W6459080XYH"


def test_filename_with_extension_and_path():
    assert wafer_id.wafer_id_from_filename(
        "/ref/folderA/W6459080XYH2_3_0_23_1.jpg") == "W6459080XYH"


def test_filename_lowercase_normalized_to_upper():
    assert wafer_id.wafer_id_from_filename("w6460169xyf6.png") == "W6460169XYF"


def test_filename_other_equipment_returns_none():
    # KLA 가 아닌 장비 파일명에는 WaferID 가 없다.
    assert wafer_id.wafer_id_from_filename("81090.137592.c.212779204.1") is None


def test_filename_no_xy_returns_none():
    assert wafer_id.wafer_id_from_filename("randomfile_123.jpg") is None


# ---------------------------------------------------------------------------
# wafer_id_from_images — 폴더의 파일명들에서 다수결
# ---------------------------------------------------------------------------
def _items(folder_side, names):
    return [ImageItem(slot="x", path=Path(f"/{folder_side}/{n}"), side=folder_side)
            for n in names]


def test_images_majority_vote():
    imgs = _items("ref", [
        "W6459080XYH2_3_0_23_1.jpg",
        "W6459080XYH2_5_1_10_2.jpg",
        "garbage_no_id.jpg",
    ])
    assert wafer_id.wafer_id_from_images(imgs) == "W6459080XYH"


def test_images_none_when_no_id():
    imgs = _items("val", ["81090.137592.c.1.jpg", "81090.137592.c.2.jpg"])
    assert wafer_id.wafer_id_from_images(imgs) is None


# ---------------------------------------------------------------------------
# merge_unmatched_by_wafer_id — 폴더명/WaferID 키 일치로 짝지어 병합
# ---------------------------------------------------------------------------
def _make_scan(ref_only_names, val_only_names) -> ScanResult:
    """ref_only / val_only 폴더만 있는 ScanResult 를 만든다(공통 slot 없음)."""
    slots: dict[str, Slot] = {}
    for n in ref_only_names:
        slots[n] = Slot(
            name=n,
            ref_images=[ImageItem(slot=n, path=Path(f"/ref/{n}/a.jpg"),
                                  side="ref")],
            val_images=[],
        )
    for n in val_only_names:
        slots[n] = Slot(
            name=n,
            ref_images=[],
            val_images=[ImageItem(slot=n, path=Path(f"/val/{n}/a.jpg"),
                                  side="val")],
        )
    return ScanResult(slots=slots,
                      ref_only=list(ref_only_names),
                      val_only=list(val_only_names))


def test_merge_pairs_by_wafer_id_keeps_ref_folder_name():
    """같은 WaferID 면 병합 — slot명은 원본 ref 폴더명을 유지."""
    sr = _make_scan(["RING_A"], ["DIE_77"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {"RING_A": "W6460169XYF6"}, {"DIE_77": "W6460169XYF6"})
    assert paired == [("RING_A", "DIE_77")]
    assert sr.common_slot_names == ["RING_A"]
    assert all(it.slot == "RING_A" for it in sr.slots["RING_A"].val_images)
    assert "DIE_77" not in sr.slots
    assert sr.ref_only == [] and sr.val_only == []


def test_merge_skips_when_wafer_id_differs():
    sr = _make_scan(["RING_A"], ["DIE_77"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {"RING_A": "AAAA000000A1"}, {"DIE_77": "BBBB111111B2"})
    assert paired == []
    assert sr.ref_only == ["RING_A"] and sr.val_only == ["DIE_77"]


def test_merge_wid_matches_other_side_folder_name():
    """KLA(파일명 WaferID)가 반대쪽 폴더명과 같으면 매칭(사용자 규칙)."""
    # ref 폴더가 WaferID 로 명명, val(KLA) 은 파일명 WaferID 만 있음.
    sr = _make_scan(["W6459080XYH"], ["KLA_RAW_07"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {}, {"KLA_RAW_07": "W6459080XYH"})
    assert paired == [("W6459080XYH", "KLA_RAW_07")]
    assert sr.common_slot_names == ["W6459080XYH"]   # 원본 ref 폴더명 유지
    assert sr.ref_only == [] and sr.val_only == []


def test_merge_name_match_case_insensitive():
    sr = _make_scan(["w6460169xyf6"], ["DIE_2"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {}, {"DIE_2": "W6460169XYF6"})
    assert paired == [("w6460169xyf6", "DIE_2")]


def test_merge_empty_maps_noop():
    sr = _make_scan(["R1"], ["V1"])
    assert wafer_id.merge_unmatched_by_wafer_id(sr, {}, {}) == []
    assert sr.ref_only == ["R1"] and sr.val_only == ["V1"]


# ---------------------------------------------------------------------------
# drop_empty_unmatched — 사진 없는 한쪽 전용 폴더 제외
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
