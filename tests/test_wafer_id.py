"""WaferID OCR 보조 로직 — 파싱 / WaferID 기반 자동 병합 검증.

OCR 엔진(rapidocr-onnxruntime)·Qt 없이도 도는 순수 로직만 테스트한다.
"""

from __future__ import annotations

from pathlib import Path

from aoi_verification.app.models.slot import ImageItem, ScanResult, Slot
from aoi_verification.app.utils import wafer_id


# ---------------------------------------------------------------------------
# _parse_wafer_id
# ---------------------------------------------------------------------------
def test_parse_basic():
    assert wafer_id._parse_wafer_id("WaferID : 00MML090XYG5") == "00MML090XYG5"


def test_parse_no_space_and_lowercase_label():
    assert wafer_id._parse_wafer_id("waferid:W6460169XYF6") == "W6460169XYF6"


def test_parse_within_full_header_line():
    text = ("Lot : TB500INT.292@6324 WaferID : 00MML090XYG5 Gain : 1")
    assert wafer_id._parse_wafer_id(text) == "00MML090XYG5"


def test_parse_normalizes_to_upper():
    assert wafer_id._parse_wafer_id("WaferID : w6459081xye1") == "W6459081XYE1"


def test_parse_missing_returns_none():
    assert wafer_id._parse_wafer_id("Lot : TB500INT.292 Gain : 1") is None
    assert wafer_id._parse_wafer_id("") is None


# ---------------------------------------------------------------------------
# merge_unmatched_by_wafer_id
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
    """같은 WaferID 면 병합 — slot명은 원본 ref 폴더명을 유지한다."""
    sr = _make_scan(["RING_A"], ["DIE_77"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {"RING_A": "W6460169XYF6"}, {"DIE_77": "W6460169XYF6"})

    assert paired == [("RING_A", "DIE_77")]
    # 원본 ref 폴더명으로 공통 slot 이 됨.
    assert sr.common_slot_names == ["RING_A"]
    merged = sr.slots["RING_A"]
    assert merged.has_both
    # val 이미지가 ref 폴더명 slot 으로 재키잉됨(원본 폴더명 사용).
    assert all(it.slot == "RING_A" for it in merged.val_images)
    # val 전용 폴더 엔트리는 제거되고 미매칭 목록도 비워짐.
    assert "DIE_77" not in sr.slots
    assert sr.ref_only == [] and sr.val_only == []


def test_merge_skips_when_wafer_id_differs():
    """WaferID 가 다르면 병합하지 않고 미매칭 유지."""
    sr = _make_scan(["RING_A"], ["DIE_77"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {"RING_A": "AAAA000000A1"}, {"DIE_77": "BBBB111111B2"})
    assert paired == []
    assert sr.common_slot_names == []
    assert sr.ref_only == ["RING_A"] and sr.val_only == ["DIE_77"]


def test_merge_partial_only_matching_pairs():
    """일부만 WaferID 가 겹치면 그 쌍만 병합, 나머지는 미매칭으로 남음."""
    sr = _make_scan(["R1", "R2"], ["V1", "V2"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr,
        {"R1": "WID_SAME0001", "R2": "WID_ONLYREF1"},
        {"V1": "WID_SAME0001", "V2": "WID_ONLYVAL1"},
    )
    assert paired == [("R1", "V1")]
    assert sr.common_slot_names == ["R1"]
    assert "V1" not in sr.slots
    assert sr.ref_only == ["R2"] and sr.val_only == ["V2"]


def test_merge_empty_maps_noop():
    sr = _make_scan(["R1"], ["V1"])
    paired = wafer_id.merge_unmatched_by_wafer_id(sr, {}, {})
    assert paired == []
    assert sr.ref_only == ["R1"] and sr.val_only == ["V1"]


# ---------------------------------------------------------------------------
# ocr_available — rapidocr 미설치 시 graceful False
# ---------------------------------------------------------------------------
def test_ocr_available_false_when_import_fails(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "rapidocr_onnxruntime":
            raise ImportError("no rapidocr")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert wafer_id.ocr_available() is False


def test_read_wafer_id_returns_none_when_reader_unavailable(monkeypatch):
    """Reader 생성 실패(엔진 없음) 시 read_wafer_id 는 None 으로 폴백."""
    monkeypatch.setattr(wafer_id, "_get_reader", lambda: None)
    assert wafer_id.read_wafer_id(Path("/nonexistent.jpg")) is None
