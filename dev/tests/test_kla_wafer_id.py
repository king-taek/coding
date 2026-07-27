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
