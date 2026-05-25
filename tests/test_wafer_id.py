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


def test_merge_ocr_wid_matches_other_side_folder_name():
    """한쪽만 OCR — 그 WaferID 가 반대쪽 폴더명과 같으면 매칭(사용자 규칙).

    val 폴더 'DIE_1' OCR → '00MML090XYG5'.  ref 폴더 이름이 '00MML090XYG5'
    (WaferID 로 명명)이고 OCR 은 안 됨 → 이름-OCR 매칭.  slot명은 ref 폴더명 유지.
    """
    sr = _make_scan(["00MML090XYG5"], ["DIE_1"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {}, {"DIE_1": "00MML090XYG5"})
    assert paired == [("00MML090XYG5", "DIE_1")]
    assert sr.common_slot_names == ["00MML090XYG5"]
    assert sr.ref_only == [] and sr.val_only == []


def test_merge_ref_ocr_matches_val_folder_name_keeps_ref_name():
    """반대 방향 — ref OCR 가 val 폴더명과 같아도 매칭, slot명은 ref 폴더명."""
    sr = _make_scan(["RING_A"], ["00MML090XYG5"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {"RING_A": "00MML090XYG5"}, {})
    assert paired == [("RING_A", "00MML090XYG5")]
    assert sr.common_slot_names == ["RING_A"]   # 원본 ref 폴더명 유지


def test_merge_name_match_is_case_insensitive():
    """폴더명/WaferID 키 비교는 대소문자 무시(OCR 은 대문자 정규화)."""
    sr = _make_scan(["w6460169xyf6"], ["DIE_2"])
    paired = wafer_id.merge_unmatched_by_wafer_id(
        sr, {}, {"DIE_2": "W6460169XYF6"})
    assert paired == [("w6460169xyf6", "DIE_2")]


def test_drop_empty_unmatched_skips_imageless_folders():
    """사진이 없는 한쪽 전용 폴더는 미매칭 목록에서 제외(그냥 넘어감)."""
    from aoi_verification.app.models.slot import (ImageItem, ScanResult, Slot,
                                                  drop_empty_unmatched)
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


# ---------------------------------------------------------------------------
# rec-only 빠른 경로 + det+rec 폴백
# ---------------------------------------------------------------------------
class _FakeReader:
    """크롭 높이가 임계 이상일 때만 WaferID 를 돌려주는 가짜 RapidOCR.

    rec-only 호출(use_det=False)이면 ``[text, score]`` 형태, det+rec 면
    ``[box, text, score]`` 형태로 돌려준다(실제 RapidOCR 과 동일).
    """

    def __init__(self, min_h: int, value: str) -> None:
        self.min_h = min_h
        self.value = value
        self.heights: list[int] = []

    def __call__(self, arr, use_det=True, use_cls=True, use_rec=True):
        h = int(arr.shape[0])
        self.heights.append(h)
        if h >= self.min_h:
            text = f"WaferID : {self.value}"
            if use_det is False:
                return ([[text, 0.99]], 0.01)              # rec-only 형태
            box = [[0, 0], [1, 0], [1, 1], [0, 1]]
            return ([[box, text, 0.99]], 0.01)             # det+rec 형태
        return (None, 0.0)


class _RecOnlyReader:
    """rec-only 호출에서 즉시 WaferID 를 돌려주는 가짜(검출은 호출되면 안 됨)."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.det_calls = 0
        self.rec_calls = 0

    def __call__(self, arr, use_det=True, use_cls=True, use_rec=True):
        if use_det is False:
            self.rec_calls += 1
            return ([[f"WaferID : {self.value}", 0.98]], 0.01)
        self.det_calls += 1
        return (None, 0.0)


class _DetOnlyReader:
    """rec-only 는 빈 결과, det+rec 에서만 WaferID 를 돌려주는 가짜(폴백 검증)."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.det_calls = 0
        self.rec_calls = 0

    def __call__(self, arr, use_det=True, use_cls=True, use_rec=True):
        if use_det is False:
            self.rec_calls += 1
            return (None, 0.0)
        self.det_calls += 1
        box = [[0, 0], [1, 0], [1, 1], [0, 1]]
        return ([[box, f"WaferID : {self.value}", 0.99]], 0.01)


def _patch_img(monkeypatch, size=(1000, 1000)):
    from PIL import Image as PILImage
    from aoi_verification.app.utils import image_io
    fake_img = PILImage.new("RGB", size, (0, 0, 0))
    monkeypatch.setattr(image_io, "_open", lambda p: fake_img)


def test_fast_path_skips_detection(monkeypatch):
    """rec-only 빠른 경로가 검출 없이 WaferID 를 읽으면 det 은 호출되지 않는다."""
    reader = _RecOnlyReader("W6460169XYF6")
    monkeypatch.setattr(wafer_id, "_get_reader", lambda: reader)
    _patch_img(monkeypatch)
    assert wafer_id.read_wafer_id(Path("/x.jpg")) == "W6460169XYF6"
    assert reader.rec_calls >= 1
    assert reader.det_calls == 0


def test_falls_back_to_detection_when_fast_fails(monkeypatch):
    """빠른 경로(rec-only)가 실패하면 det+rec 폴백으로 넘어가 인식한다."""
    reader = _DetOnlyReader("W6459081XYE1")
    monkeypatch.setattr(wafer_id, "_get_reader", lambda: reader)
    _patch_img(monkeypatch)
    assert wafer_id.read_wafer_id(Path("/x.jpg")) == "W6459081XYE1"
    assert reader.rec_calls >= 1 and reader.det_calls >= 1


def test_read_wafer_id_crop_ladder_retries(monkeypatch):
    """det+rec 폴백에서 작은 크롭이 실패하면 더 큰 크롭으로 재시도."""
    reader = _FakeReader(min_h=200, value="W6460169XYF6")
    monkeypatch.setattr(wafer_id, "_get_reader", lambda: reader)
    _patch_img(monkeypatch)
    got = wafer_id.read_wafer_id(Path("/x.jpg"))
    assert got == "W6460169XYF6"
    # 빠른 경로(작은 줄 밴드)는 실패하고, det+rec 사다리의 큰 크롭에서 성공.
    assert reader.heights[0] < 200 and max(reader.heights) >= 200


# ---------------------------------------------------------------------------
# 폴더 내 여러 장 시도 — 빠른 경로 우선, 전부 실패 시 첫 장에만 폴백
# ---------------------------------------------------------------------------
def test_read_folder_uses_multiple_images(monkeypatch):
    calls: list[str] = []

    def fake_one(p, robust=False):
        calls.append(str(p))
        return None if str(p).endswith("a.jpg") else "WID999"

    monkeypatch.setattr(wafer_id, "_read_one", fake_one)
    got = wafer_id.read_folder_wafer_id([Path("/f/a.jpg"), Path("/f/b.jpg")])
    assert got == "WID999"
    assert calls == ["/f/a.jpg", "/f/b.jpg"]


def test_read_folder_none_when_all_fail(monkeypatch):
    monkeypatch.setattr(wafer_id, "_read_one", lambda p, robust=False: None)
    monkeypatch.setattr(wafer_id, "_read_robust_only", lambda p: None)
    assert wafer_id.read_folder_wafer_id([Path("/a"), Path("/b")]) is None


def test_read_folder_robust_fallback_on_first(monkeypatch):
    """빠른 경로가 모두 실패하면 첫 장에 det+rec 폴백을 적용한다."""
    monkeypatch.setattr(wafer_id, "_read_one", lambda p, robust=False: None)
    monkeypatch.setattr(wafer_id, "_read_robust_only", lambda p: "WIDROB")
    assert wafer_id.read_folder_wafer_id(
        [Path("/a"), Path("/b")]) == "WIDROB"


def test_read_folder_respects_limit(monkeypatch):
    seen: list = []
    monkeypatch.setattr(wafer_id, "_read_one",
                        lambda p, robust=False: seen.append(p) or None)
    monkeypatch.setattr(wafer_id, "_read_robust_only", lambda p: None)
    wafer_id.read_folder_wafer_id([Path(str(i)) for i in range(10)], limit=3)
    assert len(seen) == 3


def test_header_crop_image_none_on_bad_path():
    assert wafer_id.header_crop_image(Path("/nonexistent_xyz.jpg")) is None
