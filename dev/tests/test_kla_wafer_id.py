"""KLA 정보파일에서 slot명(WaferID) 판독 — 순수 로직(무거운 의존성 없음).

``WaferID "XXXX";`` 는 정보파일 **헤더**에 있고, 확장자는 ``.001`` 일 수도 아예 없을
수도 있다.  사진과 무관하게 읽히므로 **사진 0장 폴더도 식별**된다.
"""

from __future__ import annotations

from aoi_verification.app.coords import kla_info

# 실제 샘플(dev/좌표 확인/KLA/예시1)의 헤더 앞부분.
_HEADER = """FileVersion 1 2;
InspectionStationID "KLA" "LDS CIRCL" "K3";
LotID "TB500INT.271@6324";
DiePitch 3.7247930000e+004 4.4905340000e+004;
WaferID "W6459076XYG1";
Slot 10;
"""


def _folder(tmp_path, name: str, info_name: str | None, text: str = _HEADER):
    d = tmp_path / name
    d.mkdir()
    if info_name is not None:
        (d / info_name).write_text(text, encoding="utf-8")
    return d


def test_reads_wafer_id_from_001(tmp_path):
    d = _folder(tmp_path, "s1", "0abc_TB500INT.271@6324_W6459076XYG1.001")
    (d / "W6459076XYG1_2_0_23_2.jpg").write_bytes(b"x")
    assert kla_info.read_wafer_id(d) == "W6459076XYG1"


def test_reads_wafer_id_from_extensionless_file(tmp_path):
    """확장자가 아예 없는 정보파일도 읽는다."""
    d = _folder(tmp_path, "s2", "resultfile")
    assert kla_info.read_wafer_id(d) == "W6459076XYG1"


def test_reads_when_two_non_image_files(tmp_path):
    """비-사진 파일이 2개여도 후보를 전부 훑어 읽는다.

    좌표용 ``_find_info_file`` 은 이 상황에서 None 을 주지만(어느 것이 정보파일인지
    확신 불가), WaferID 판독은 첫 성공값을 쓴다."""
    d = _folder(tmp_path, "s3", "other.log", text="관계없는 파일\n")
    (d / "resultfile").write_text(_HEADER, encoding="utf-8")
    assert kla_info._find_info_file(d) is None
    assert kla_info.read_wafer_id(d) == "W6459076XYG1"


def test_prefers_001_over_other_candidates(tmp_path):
    d = _folder(tmp_path, "s4", "zzz_other", text=_HEADER.replace(
        "W6459076XYG1", "WRONGID00001"))
    (d / "a.001").write_text(_HEADER, encoding="utf-8")
    assert kla_info.read_wafer_id(d) == "W6459076XYG1"


def test_reads_from_folder_without_images(tmp_path):
    """사진이 한 장도 없어도 판독된다 — 파일명 추측·OCR 로는 불가능했던 경우."""
    d = _folder(tmp_path, "s5", "W6459081XYE1.001",
                text=_HEADER.replace("W6459076XYG1", "W6459081XYE1"))
    assert not list(d.glob("*.jpg"))
    assert kla_info.read_wafer_id(d) == "W6459081XYE1"


def test_none_when_no_info_file(tmp_path):
    d = _folder(tmp_path, "s6", None)
    (d / "shot.jpg").write_bytes(b"x")
    assert kla_info.read_wafer_id(d) is None


def test_none_when_no_wafer_id_line(tmp_path):
    d = _folder(tmp_path, "s7", "a.001", text="LotID \"X\";\nSlot 3;\n")
    assert kla_info.read_wafer_id(d) is None


def test_ini_is_not_treated_as_info_file(tmp_path):
    """Camtek 의 ColorImageGrabingInfo.ini 는 후보에서 제외된다."""
    d = _folder(tmp_path, "s8", "ColorImageGrabingInfo.ini")
    assert kla_info.read_wafer_id(d) is None


def test_wafer_id_is_uppercased(tmp_path):
    d = _folder(tmp_path, "s9", "a.001",
                text='WaferID "w6459076xyg1";\n')
    assert kla_info.read_wafer_id(d) == "W6459076XYG1"


def test_reads_slot_number_from_the_same_header(tmp_path):
    """`Slot 10;` — 카세트 슬롯 번호.  슬롯 고르기 화면이 WaferID 와 함께 보여 준다."""
    d = _folder(tmp_path, "s9", "a.001")
    assert kla_info.read_header_ids(d) == ("W6459076XYG1", "10")
    assert kla_info.read_slot_no(d) == "10"


def test_slot_number_absent_is_none(tmp_path):
    d = _folder(tmp_path, "s10", "a.001", text=_HEADER.replace("Slot 10;\n", ""))
    assert kla_info.read_header_ids(d) == ("W6459076XYG1", None)


def test_non_kla_folder_has_no_ids(tmp_path):
    d = _folder(tmp_path, "s11", None)
    (d / "a.jpg").write_bytes(b"x")
    assert kla_info.read_header_ids(d) == (None, None)


# ---------------------------------------------------------------------------
# '일부 슬롯만 진행' + KLA — 고른 웨이퍼의 짝 폴더가 스캔에서 빠지면 안 된다
# ---------------------------------------------------------------------------
def _kla_lot(tmp_path):
    """기준 = KLA(폴더명 = 검사 시각), 검증 = Camtek(폴더명 = WaferID)."""
    ref, val = tmp_path / "ref", tmp_path / "val"
    for slot, (ts, wid) in enumerate([("2026-08-02-23-09_1", "WAAA0001"),
                                      ("2026-08-02-23-10_2", "WAAA0002"),
                                      ("2026-08-02-23-11_3", "WAAA0003")], start=1):
        d = ref / ts
        d.mkdir(parents=True)
        (d / f"x_LOT_{wid}.001").write_text(
            _HEADER.replace("W6459076XYG1", wid).replace("Slot 10;", f"Slot {slot};"),
            encoding="utf-8")
        (d / f"{wid}_2_0_23_1.jpg").write_bytes(b"x")
        v = val / wid
        v.mkdir(parents=True)
        (v / "1.jpg").write_bytes(b"x")
    return ref, val


def test_subset_scan_keeps_the_kla_partner_folder(tmp_path):
    """KLA 폴더를 골랐을 때 검증 쪽 WaferID 폴더도 함께 열린다.

    예전에는 이름으로만 걸러 짝이 스캔에서 빠졌고, KLA 해석이 짝을 못 찾아 그 웨이퍼가
    '기준 전용' 으로 떨어져 매칭이 통째로 사라졌다."""
    from aoi_verification.app.models.slot import merge_unmatched_by_wafer_id, scan
    ref, val = _kla_lot(tmp_path)
    sr = scan(ref, val, only={"2026-08-02-23-10_2"})
    assert sorted(sr.slots) == ["2026-08-02-23-10_2", "WAAA0002"]
    merge_unmatched_by_wafer_id(
        sr, {"2026-08-02-23-10_2": kla_info.read_wafer_id(ref / "2026-08-02-23-10_2")}, {})
    assert sr.common_slot_names == ["WAAA0002"]


def test_subset_scan_finds_kla_partner_on_the_val_side(tmp_path):
    """반대로 검증이 KLA 여도 같다 — 고른 기준 슬롯(WaferID)의 KLA 폴더를 찾는다."""
    from aoi_verification.app.models.slot import scan
    ref, val = _kla_lot(tmp_path)
    sr = scan(val, ref, only={"WAAA0003"})
    assert sorted(sr.slots) == ["2026-08-02-23-11_3", "WAAA0003"]


def test_subset_scan_reads_nothing_when_names_pair_up(tmp_path, monkeypatch):
    """이름으로 짝이 다 맞으면(일반 Camtek↔Camtek) 정보파일을 하나도 읽지 않는다."""
    from aoi_verification.app.models import slot as slot_mod
    for side in ("ref", "val"):
        for n in ("S1", "S2"):
            (tmp_path / side / n).mkdir(parents=True)
    calls = []
    monkeypatch.setattr(kla_info, "read_wafer_id", lambda d: calls.append(d))
    sr = slot_mod.scan(tmp_path / "ref", tmp_path / "val", only={"S1"})
    assert sorted(sr.slots) == ["S1"]
    assert calls == []
