"""KLA slot명(WaferID) 해석 — OCR 라벨 파싱 + WaferID/폴더명 병합 (순수 로직).

WaferID 는 KLA **정보파일**에서 읽는 것이 1순위이고(파서 테스트는
``test_kla_wafer_id.py``), 여기서는 그 결과로 ref↔val 을 병합하는 로직을 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import (ImageItem, ScanResult, Slot,
                                              drop_empty_unmatched,
                                              merge_unmatched_by_wafer_id,
                                              push_one_sided_to_unmatched)
from aoi_verification.app.utils import wafer_id


def _slot(name, side, filenames):
    items = [ImageItem(slot=name, path=Path(f"/{side}/{name}/{fn}"), side=side)
             for fn in filenames]
    if side == "ref":
        return Slot(name=name, ref_images=items, val_images=[],
                    ref_dir=Path(f"/ref/{name}"))
    return Slot(name=name, ref_images=[], val_images=items,
                val_dir=Path(f"/val/{name}"))


def _scan(ref_specs, val_specs) -> ScanResult:
    slots = {}
    for n, fns in ref_specs.items():
        slots[n] = _slot(n, "ref", fns)
    for n, fns in val_specs.items():
        slots[n] = _slot(n, "val", fns)
    return ScanResult(slots=slots,
                      ref_only=list(ref_specs), val_only=list(val_specs))


# ---------------------------------------------------------------------------
# OCR 폴백의 라벨 파싱
# ---------------------------------------------------------------------------
def test_parse_wafer_id_handles_label_variants():
    # 'WaferID :' / 'WAFER ID:' 둘 다, Lot/Gain 줄과 섞여 있어도 WaferID 값만 추출.
    assert wafer_id._parse_wafer_id("WaferID : 00MML090XYG5") == "00MML090XYG5"
    assert wafer_id._parse_wafer_id("WAFER ID: W6459153XYF5") == "W6459153XYF5"
    multi = "Lot : AB12CD\nWaferID : 00NJ3159XYC1\nGain : 12"
    assert wafer_id._parse_wafer_id(multi) == "00NJ3159XYC1"
    assert wafer_id._parse_wafer_id("Gain : 12") is None


# ---------------------------------------------------------------------------
# WaferID/폴더명 키로 병합
# ---------------------------------------------------------------------------
def test_merge_by_wafer_id_from_info_file():
    """val 폴더명이 WaferID, ref(KLA) 정보파일에서 같은 WaferID → 병합."""
    sr = _scan(
        ref_specs={"KLA_RAW_07": ["FrontSideADRImg_1.jpg"]},
        val_specs={"W6459080XYHX": ["81090.137592.c.1.jpg"]},
    )
    wid_ref = {"KLA_RAW_07": "W6459080XYHX"}       # 정보파일에서 읽은 값
    paired = merge_unmatched_by_wafer_id(sr, wid_ref, {})
    assert paired == [("KLA_RAW_07", "W6459080XYHX")]
    # 병합 slot명 = WaferID(교집합 키) — KLA 임의 폴더명이 아니라.
    assert sr.common_slot_names == ["W6459080XYHX"]
    merged = sr.slots["W6459080XYHX"]
    assert merged.has_both
    assert all(it.slot == "W6459080XYHX"
               for it in merged.ref_images + merged.val_images)
    assert sr.ref_only == [] and sr.val_only == []


def test_merge_carries_folder_dirs():
    """병합 슬롯이 양쪽 원본 폴더 경로를 이어받는다(정보파일을 나중에도 찾을 수 있게)."""
    sr = _scan(ref_specs={"KLA_RAW_07": ["a.jpg"]},
               val_specs={"W6459080XYHX": ["b.jpg"]})
    merge_unmatched_by_wafer_id(sr, {"KLA_RAW_07": "W6459080XYHX"}, {})
    merged = sr.slots["W6459080XYHX"]
    assert merged.ref_dir == Path("/ref/KLA_RAW_07")
    assert merged.val_dir == Path("/val/W6459080XYHX")


def test_merge_slot_named_wafer_id_when_kla_is_ref():
    """KLA 가 기준(ref) 쪽이어도 slot명은 WaferID(=검증 폴더명) 가 된다."""
    sr = _scan(
        ref_specs={"KLA_LOT_X": ["shot.jpg"]},              # 기준=KLA(임의 폴더명)
        val_specs={"W1234567ABCD": ["shot.jpg"]},           # 검증=WaferID 폴더명
    )
    wid_ref = {"KLA_LOT_X": "W1234567ABCD"}
    merge_unmatched_by_wafer_id(sr, wid_ref, {})
    assert sr.common_slot_names == ["W1234567ABCD"]
    assert "KLA_LOT_X" not in sr.slots


def test_merge_no_match_when_wafer_id_differs():
    sr = _scan(
        ref_specs={"RAW_A": ["a.jpg"]},
        val_specs={"W6459080XYHX": ["b.jpg"]},
    )
    wid_ref = {"RAW_A": "ZZZZ1234XYZ9"}
    assert merge_unmatched_by_wafer_id(sr, wid_ref, {}) == []
    assert sr.ref_only == ["RAW_A"] and sr.val_only == ["W6459080XYHX"]


def test_merge_both_kla_pairs_by_wafer_id():
    """둘 다 KLA — 양쪽 폴더명이 모두 임의여도 정보파일 WaferID 로 정확히 짝지어진다."""
    sr = _scan(
        ref_specs={"RAW_A": ["a.jpg"], "RAW_B": ["b.jpg"]},
        val_specs={"RAW_X": ["x.jpg"], "RAW_Y": ["y.jpg"]},
    )
    info_ref = {"RAW_A": "W1111111XYA1", "RAW_B": "W2222222XYB2"}
    info_val = {"RAW_X": "W2222222XYB2", "RAW_Y": "W1111111XYA1"}
    merge_unmatched_by_wafer_id(sr, info_ref, info_val)
    # 나열 순서가 아니라 WaferID 로 짝지어야 한다(A↔Y, B↔X).
    assert sr.common_slot_names == ["W1111111XYA1", "W2222222XYB2"]
    assert sr.slots["W1111111XYA1"].ref_images[0].path == Path("/ref/RAW_A/a.jpg")
    assert sr.slots["W1111111XYA1"].val_images[0].path == Path("/val/RAW_Y/y.jpg")
    assert sr.slots["W2222222XYB2"].ref_images[0].path == Path("/ref/RAW_B/b.jpg")
    assert sr.slots["W2222222XYB2"].val_images[0].path == Path("/val/RAW_X/x.jpg")


def test_merge_second_pass_keeps_first_pass_ids():
    """2차(OCR) 병합엔 1차(정보파일) 결과를 **합쳐** 넘겨야 한다.

    OCR dict 만 넘기면 정보파일로 읽은 쪽의 WaferID 키가 사라져, 같은 WaferID 인데도
    병합에 실패한다."""
    sr = _scan(ref_specs={"RAW_A": ["a.jpg"]}, val_specs={"RAW_X": ["x.jpg"]})
    info_ref = {"RAW_A": "W6459080XYHX"}        # 기준: 정보파일로 판독 → OCR 생략
    info_val: dict = {}                        # 검증: 정보파일 없음 → OCR 대상
    assert merge_unmatched_by_wafer_id(sr, info_ref, info_val) == []

    ocr_val = {"RAW_X": "W6459080XYHX"}
    paired = merge_unmatched_by_wafer_id(
        sr, {**info_ref}, {**info_val, **ocr_val})
    assert paired == [("RAW_A", "RAW_X")]
    assert sr.common_slot_names == ["W6459080XYHX"]


def test_merge_skips_when_slot_name_collides():
    """slot명이 무관한 기존 슬롯과 겹치면 병합하지 않는다 — 덮어쓰면 사진이 사라진다."""
    sr = _scan(
        ref_specs={"RAW_A": ["a.jpg"], "RAW_B": ["b.jpg"]},
        val_specs={"RAW_X": ["x.jpg"], "RAW_Y": ["y.jpg"]},
    )
    # 두 쌍이 **같은** WaferID 를 갖는 병리적 입력(중복 내보내기).
    dup = "W6459080XYHX"
    info_ref = {"RAW_A": dup, "RAW_B": dup}
    info_val = {"RAW_X": dup, "RAW_Y": dup}
    merge_unmatched_by_wafer_id(sr, info_ref, info_val)

    # 첫 쌍만 병합되고, 두 번째 쌍은 덮어쓰지 않고 수동 매핑으로 남는다.
    assert sr.common_slot_names == [dup]
    assert sr.slots[dup].ref_images[0].path == Path("/ref/RAW_A/a.jpg")
    assert sr.ref_only == ["RAW_B"] and sr.val_only == ["RAW_Y"]
    assert sr.slots["RAW_B"].ref_images                  # 사진이 살아 있다
    assert sr.slots["RAW_Y"].val_images


# ---------------------------------------------------------------------------
# 사진 0장 폴더 정리 / 한쪽만 있는 슬롯 되돌리기
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


def test_push_one_sided_to_unmatched():
    """병합됐지만 한쪽 사진이 0장인 슬롯을 '기준/검증 전용' 으로 되돌린다.

    그대로 두면 common_slot_names 에도 ref_only/val_only 에도 없어 결과에서 사라진다."""
    slots = {
        "BOTH": Slot("BOTH",
                     ref_images=[ImageItem("BOTH", Path("/r/a.jpg"), "ref")],
                     val_images=[ImageItem("BOTH", Path("/v/a.jpg"), "val")]),
        "REF_ONLY": Slot("REF_ONLY",
                         ref_images=[ImageItem("REF_ONLY", Path("/r/b.jpg"), "ref")],
                         val_images=[]),
        "VAL_ONLY": Slot("VAL_ONLY", ref_images=[],
                         val_images=[ImageItem("VAL_ONLY", Path("/v/c.jpg"), "val")]),
        "NEITHER": Slot("NEITHER", ref_images=[], val_images=[]),
    }
    sr = ScanResult(slots=slots, ref_only=[], val_only=[])
    push_one_sided_to_unmatched(sr)
    assert sr.ref_only == ["REF_ONLY"]
    assert sr.val_only == ["VAL_ONLY"]
    assert sr.common_slot_names == ["BOTH"]     # 양쪽 다 있는 슬롯은 그대로


def test_push_one_sided_does_not_duplicate_existing():
    """이미 ref_only/val_only 에 있는 이름은 중복 추가하지 않는다."""
    slots = {"R": Slot("R", ref_images=[ImageItem("R", Path("/r/a.jpg"), "ref")],
                       val_images=[])}
    sr = ScanResult(slots=slots, ref_only=["R"], val_only=[])
    push_one_sided_to_unmatched(sr)
    assert sr.ref_only == ["R"]
