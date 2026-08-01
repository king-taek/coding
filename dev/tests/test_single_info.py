"""단일 사진 정보 추출 — 순수 로직(무거운 의존성 없음).

``coords.single_info`` 는 엑셀 미매칭 행(D열)과 단일 사진 정보 화면이 **같은 문자열**을
쓰도록 만든 유일한 생산자다.  여기서 검증하는 것:

- ``coord_lines`` / ``geometry_lines`` 가 엑셀에 나가던 표기를 그대로 만든다
  (엑셀 쪽 회귀는 ``test_exporter_unmatched.py`` 가 별도로 지킨다)
- ``describe`` 가 **값을 얻은 항목만** 담는다 — 못 채우는 줄을 빈칸으로 남기지 않는다
"""

from __future__ import annotations

import struct

import pytest

from aoi_verification.app import i18n
from aoi_verification.app.coords import (camtek_ini, kla_info, single_info,
                                         surface_flt)

# 실제 샘플(dev/좌표 확인/KLA/예시1) 헤더 + DefectList 한 행.
_KLA_INFO = """FileVersion 1 2;
InspectionStationID "KLA" "LDS CIRCL" "K3";
DiePitch 3.7247930000e+004 4.4905340000e+004;
WaferID "W6459076XYG1";
TiffFileName W6459076XYG1_2_0_23_2.jpg
 1 100.0 200.0 1234.5 2345.6 -2 -1 0
"""


@pytest.fixture(autouse=True)
def _clear_parser_caches():
    """coords 파서는 폴더 단위 lru_cache — tmp_path 가 재사용돼도 새로 읽게 비운다."""
    for fn in (kla_info.load_folder, kla_info.read_wafer_id,
               camtek_ini.load_abs_folder, surface_flt.load_folder):
        fn.cache_clear()
    yield


def _group(groups, title):
    """제목으로 덩이 찾기 — 없으면 None."""
    for g in groups:
        if g.title == title:
            return g
    return None


def _labels(groups) -> list[str]:
    return [r.label for g in groups for r in g.rows if r.label]


def _values(groups) -> list[str]:
    return [r.value for g in groups for r in g.rows]


# ---------------------------------------------------------------------------
# coord_lines — 엑셀 D열과 같은 표기
# ---------------------------------------------------------------------------
def test_live_filename_gives_colrow_and_xy(tmp_path):
    """LIVE 형식 파일명만으로 col/row · x/y 두 줄이 나온다(정보파일 불필요)."""
    # 좌표 토큰 앞에 '_' 가 하나 더 있어야 한다 — camtek_live 가 KLA 파일명 오인을
    # 막으려고 두는 가드(`'_' in stem[:m.start()]`).
    img = tmp_path / "cam_live_3_2_1000_2000.jpeg"
    img.write_bytes(b"x")
    # col/row 토큰은 **보정 없이** 쓴다 — camtek_ini 와 같은 규약이다
    # (예전의 row −1 정규화는 되돌려진 변경의 잔재였다 → 조사 문서 §6-G).
    assert single_info.coord_lines(img) == ["col 3 / row 2",
                                            "x 1000 / y 2000 ㎛"]


def test_kla_adds_native_coordinate_line(tmp_path):
    """KLA 결함은 변환값 아래에 자체 원본 좌표(XREL/YREL)를 한 줄 더 붙인다."""
    (tmp_path / "info.001").write_text(_KLA_INFO, encoding="utf-8")
    img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
    img.write_bytes(b"x")

    lines = single_info.coord_lines(img)
    assert len(lines) == 3
    assert lines[0] == "col 1 / row 3"      # XINDEX -2 +KLA_ZERO_X, YINDEX -1 +KLA_ZERO_Y
    assert lines[2] == i18n.KO.EXPORT_KLA_NATIVE_FMT.format(x=1234.5, y=2345.6)
    # 줄바꿈은 붙이는 쪽(엑셀 exporter)이 넣는다 — 여기서는 순수 텍스트.
    assert not any(line.startswith("\n") for line in lines)


def test_no_coordinate_source_gives_no_lines(tmp_path):
    img = tmp_path / "plain.jpeg"
    img.write_bytes(b"x")
    assert single_info.coord_lines(img) == []


# ---------------------------------------------------------------------------
# geometry_lines
# ---------------------------------------------------------------------------
def _install_flt_schema(monkeypatch):
    """합성 스키마(32B) 설치 — test_exporter_unmatched 와 같은 레이아웃."""
    fields = {"actual_x": (0, "f"), "actual_y": (4, "f"), "area": (8, "f"),
              "blob_breadth": (12, "f"), "blob_feret_max": (16, "f"),
              "contrast": (20, "f"), "zone": (24, "B"), "recipe": (25, "B")}
    monkeypatch.setattr(surface_flt, "_FIELDS", dict(fields))
    monkeypatch.setattr(surface_flt, "_RECORD_SIZE", 32)
    monkeypatch.setattr(surface_flt, "_HEADER_BYTES", 0)
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", True)
    surface_flt.load_folder.cache_clear()
    camtek_ini.load_abs_folder.cache_clear()


def _write_flt(folder, x, y, area, breadth, feret, contrast, zone=1, recipe=2):
    buf = bytearray(32)
    for off, v in ((0, x), (4, y), (8, area), (12, breadth),
                   (16, feret), (20, contrast)):
        struct.pack_into("<f", buf, off, float(v))
    struct.pack_into("<B", buf, 24, int(zone))
    struct.pack_into("<B", buf, 25, int(recipe))
    (folder / "Surface.flt").write_bytes(bytes(buf))


def _camtek_folder(tmp_path, monkeypatch, *, contrast=108.0):
    """Surface.flt + INI + 이름 매핑이 갖춰진 Camtek 폴더."""
    _install_flt_schema(monkeypatch)
    img = tmp_path / "z_miss.jpeg"
    img.write_bytes(b"x")
    _write_flt(tmp_path, 112743.1, 226527.0, 55.0, 2.0, 11.0, contrast)
    (tmp_path / "ColorImageGrabingInfo.ini").write_text(
        "[z_miss.jpeg]\nX=112743.1\nY=226527.0\nCol=3\nRow=5\n",
        encoding="utf-8")
    (tmp_path / "ProductInfo.ini").write_text(
        "[Z]\nZoneName=PI_Opening\nZoneID=1\n"
        "[R]\nRecipeName=PI\nRecipeNumber=2\n", encoding="utf-8")
    return img


def test_geometry_lines_order_and_names(tmp_path, monkeypatch):
    """표기 순서: recipe/zone → area → width → length → contrast, 이름만 표기."""
    img = _camtek_folder(tmp_path, monkeypatch)
    lines = single_info.geometry_lines(img)
    assert lines[0] == "recipe PI / zone PI_Opening"
    assert lines[1].startswith("area ")
    assert lines[2].startswith("width ")
    assert lines[3].startswith("length ")
    assert lines[4] == "contrast 108.00"


def test_contrast_zero_is_dash_not_number(tmp_path, monkeypatch):
    """contrast 0 = '측정 안 함' — 0.00 으로 오해되지 않게 '—' 로."""
    img = _camtek_folder(tmp_path, monkeypatch, contrast=0.0)
    assert single_info.geometry_lines(img)[4] == (
        f"{i18n.KO.DEFECT_CONTRAST_LABEL} {i18n.KO.DEFECT_CONTRAST_NONE_VALUE}")


def test_no_surface_flt_gives_unsupported_marker(tmp_path, monkeypatch):
    _install_flt_schema(monkeypatch)
    img = tmp_path / "a.jpeg"
    img.write_bytes(b"x")
    assert single_info.geometry_lines(img) == [i18n.KO.GEOM_NOT_SUPPORTED]


def test_disabled_schema_gives_no_lines_at_all(tmp_path, monkeypatch):
    """스키마 미충전이면 마커조차 띄우지 않는다 — 엑셀의 기존 동작 보존."""
    monkeypatch.setattr(surface_flt, "_SCHEMA_READY", False)
    img = tmp_path / "a.jpeg"
    img.write_bytes(b"x")
    assert single_info.geometry_lines(img) == []


def test_defect_lines_is_geometry_then_coords(tmp_path, monkeypatch):
    img = _camtek_folder(tmp_path, monkeypatch)
    assert single_info.defect_lines(img) == (
        single_info.geometry_lines(img) + single_info.coord_lines(img))


# ---------------------------------------------------------------------------
# describe — 제도 시트 표: 그룹 · 값 있는 항목만 · 계측 없으면 덩이 자체가 없다
# ---------------------------------------------------------------------------
def test_describe_omits_rows_it_cannot_fill(tmp_path):
    """정보파일이 없는 폴더 — '파일' 덩이만, WaferID·좌표 출처 등은 빠진다."""
    img = tmp_path / "plain.jpeg"
    img.write_bytes(b"x")
    groups = single_info.describe(img)
    assert [g.title for g in groups] == [i18n.KO.IMAGE_INFO_GROUP_FILE]
    assert _labels(groups) == [i18n.KO.IMAGE_INFO_ROW_FILE,
                               i18n.KO.IMAGE_INFO_ROW_FOLDER]
    assert all(v for v in _values(groups)), "값이 빈 행을 남기면 안 된다"


def test_describe_without_any_measurement_has_no_defect_group(tmp_path):
    """계측이 0 이면 '결함 계측' 덩이를 만들지 않는다.

    스탬프만 남기면 '이 자재는 원래 측정 안 함'(정상)과 '정보파일이 없어 못 읽음'
    (비정상)이 구별되지 않고, 화면이 복구 안내를 띄울 기회를 잃는다."""
    img = tmp_path / "plain.jpeg"
    img.write_bytes(b"x")
    groups = single_info.describe(img)
    assert single_info.has_measurements(groups) is False
    assert _group(groups, i18n.KO.IMAGE_INFO_GROUP_DEFECT) is None


def test_describe_kla_includes_wafer_id_and_source(tmp_path):
    (tmp_path / "info.001").write_text(_KLA_INFO, encoding="utf-8")
    img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
    img.write_bytes(b"x")

    groups = single_info.describe(img)
    files = _group(groups, i18n.KO.IMAGE_INFO_GROUP_FILE)
    by_label = {r.label: r.value for r in files.rows if r.label}
    assert by_label[i18n.KO.IMAGE_INFO_ROW_WAFER_ID] == "W6459076XYG1"
    assert by_label[i18n.KO.IMAGE_INFO_ROW_SOURCE] == \
        i18n.KO.IMAGE_INFO_SOURCE_NAMES["kla"]
    # geometry 가 없으므로 픽셀 크기 행은 아예 없다.
    assert i18n.KO.IMAGE_INFO_ROW_PIXEL not in _labels(groups)


def test_describe_defect_values_match_the_excel_numbers(tmp_path, monkeypatch):
    """표의 값 = 엑셀 줄의 수치 — 두 화면이 어긋나지 않음을 고정.

    표기 모양은 다르다(표는 '5 / 3', 엑셀은 'col 5 / row 3').  같아야 하는 것은
    **수치**이고, 그래서 Measure 하나가 세 표기를 함께 들고 있다."""
    img = _camtek_folder(tmp_path, monkeypatch)
    for m in single_info.measures(img):
        # 'col / row' 처럼 라벨이 값 사이에 끼는 표기가 있어 문자열 포함은 성립하지
        # 않는다.  성립해야 하는 것은 **값 토큰이 전부 엑셀 줄에도 있다**는 것.
        for token in m.value.split(" / "):
            assert token in m.line, f"{token!r} 가 {m.line!r} 안에 없다"
    # 엑셀 줄 목록도 같은 Measure 에서 나온다.
    assert single_info.defect_lines(img) == [
        m.line for m in single_info.measures(img)]


def test_describe_defect_group_carries_measures_and_stamp(tmp_path, monkeypatch):
    img = _camtek_folder(tmp_path, monkeypatch)
    groups = single_info.describe(img)
    assert single_info.has_measurements(groups) is True
    defect = _group(groups, i18n.KO.IMAGE_INFO_GROUP_DEFECT)
    labels = [r.label for r in defect.rows if r.label]
    assert i18n.KO.DEFECT_COLROW_LABEL in labels
    assert i18n.KO.DEFECT_AREA_LABEL in labels
    assert i18n.KO.IMAGE_INFO_ROW_PIXEL in labels
    # 수치 행은 모노로 찍힌다(도면 컨셉의 계측 표기).
    assert all(r.mono for r in defect.rows if r.label)
    # 마지막은 판정 스탬프 — 측정정보가 있으므로 'ok'.
    assert defect.rows[-1].stamp == "ok"
    assert defect.rows[-1].value == i18n.KO.IMAGE_INFO_STAMP_MEASURED


def test_describe_stamps_unsupported_material(tmp_path, monkeypatch):
    """Surface.flt 가 없어도 좌표는 있는 자재 — 덩이는 있고 스탬프는 '없음'."""
    _install_flt_schema(monkeypatch)
    (tmp_path / "info.001").write_text(_KLA_INFO, encoding="utf-8")
    img = tmp_path / "W6459076XYG1_2_0_23_2.jpg"
    img.write_bytes(b"x")
    defect = _group(single_info.describe(img), i18n.KO.IMAGE_INFO_GROUP_DEFECT)
    assert defect is not None
    assert defect.rows[-1].stamp == "none"
    assert defect.rows[-1].value == i18n.KO.GEOM_NOT_SUPPORTED


def test_describe_survives_missing_file(tmp_path):
    """존재하지 않는 경로여도 죽지 않는다(fail-safe)."""
    groups = single_info.describe(tmp_path / "nope.jpeg")
    assert _labels(groups) == [i18n.KO.IMAGE_INFO_ROW_FILE,
                               i18n.KO.IMAGE_INFO_ROW_FOLDER]
