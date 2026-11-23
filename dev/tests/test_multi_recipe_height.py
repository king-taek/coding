"""멀티 레시피 스캔의 3D(Height) Defect 정보 — 실물 골든(AOI-16 T254 6324 FLU-1122N GX57527979, 2026-11-23 수집).

정답은 같은 웨이퍼의 장비 Export ``Defects.csv`` 다(좌표 답안지, 메모 equipment-export).
지키는 것:
  ① 사진 정보 ``Type=2`` 사진은 Height.flt 에서 읽고 **Surface 값을 절대 붙이지 않는다**
     (예전엔 옆 Surface Defect 값이 붙었다 — 이 웨이퍼 4장, 10곳 전체 6장).
  ② 모든 사진의 Type('<검사>-<Zone>') · 값(area/width/length 또는 height) · Classify 가 Export 와 같다.
  ③ 사이드카(``Height.flt.md``)가 없으면 배치를 지어내지 않고 정보 없음.
  ④ 이름 사전(EquipmentClassifications · ManReClassify)에서 이름을 읽고, 음수 분류는 절댓값 이름.
  ⑤ Zone 이름은 그 레시피의 Zone 폴더를 먼저 본다(레시피 없이 부르면 예전과 같다).
수집한 10곳 전체에서는 사진 2,568/2,568 장이 ② 를 통과했다(분석은 쓰레드 기록).
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from aoi_verification.app import coords
from aoi_verification.app.coords import camtek_ini, class_names, geometry, height_flt, single_info, zone_name
from aoi_verification.app.coords.models import KIND_HEIGHT, KIND_SURFACE

ROOT = Path(__file__).resolve().parents[2]
WAFER = ROOT / "dev" / "좌표 확인" / "멀티레시피(Height)" / "GX57527979(T254 FLU-1122N AOI-16)"


def _photos():
    names = [ln.strip() for ln in (WAFER / "사진목록.txt").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    return [WAFER / n for n in names if ".c." in n]


def _export():
    rows = list(csv.reader(open(WAFER / "Defects.csv", encoding="utf-8-sig")))
    return rows[0], rows[1:]


def _row_for(p, g, hdr, rows):
    k = coords.resolve(p)
    ic, ir, ix, iy = (hdr.index(h) for h in ("Col", "Row", "X", "Y"))
    typ = f"{g.kind}-{g.zone_name}"
    hits = [r for r in rows if r[1] == typ and int(r[ic]) == k.screen_col and int(r[ir]) == k.screen_row
            and abs(float(r[ix]) - k.x) <= 6 and abs(float(r[iy]) - k.y) <= 6]
    assert len(hits) == 1, (p.name, typ, len(hits))
    return hits[0]


def test_every_photo_matches_the_equipment_export():
    hdr, rows = _export()
    icl = hdr.index("Classify")
    kinds = {KIND_HEIGHT: 0, KIND_SURFACE: 0}
    for p in _photos():
        res = geometry.resolve(p)
        assert res.status == "ok", p.name
        g = res.geometry
        r = _row_for(p, g, hdr, rows)
        kinds[g.kind] += 1
        if g.kind == KIND_HEIGHT:
            assert g.height_um == pytest.approx(float(r[2]), abs=0.01)     # Export 3번째 열 = Height
            assert g.area_um2 is None and g.contrast is None
        else:
            assert g.area_um2 == pytest.approx(float(r[2]), rel=0.01, abs=0.01)
            assert g.width_um == pytest.approx(float(r[3]), abs=0.01)
            assert g.length_um == pytest.approx(float(r[4]), abs=0.01)
        assert g.class_name == r[icl], p.name
        assert g.type_name                                                    # 종류 이름이 비지 않음
        assert g.recipe_name == ("BS_3D" if g.kind == KIND_HEIGHT else f"BS_{g.recipe}")   # RecipesInfo.ini
    assert kinds == {KIND_HEIGHT: 11, KIND_SURFACE: 340}


def test_height_photo_never_takes_a_surface_record():
    """``Type=2`` 사진은 Surface.flt 5 µm 안에 Surface Defect 가 있어도 Height 레코드를 쓴다."""
    types = camtek_ini.load_type_folder(WAFER)
    height = [p for p in _photos() if types.get(p.stem.lower()) == height_flt.HEIGHT_PHOTO_TYPE]
    assert len(height) == 11
    for p in height:
        assert geometry.resolve(p).geometry.kind == KIND_HEIGHT
    # 예전 경로(Surface.flt 최근접)가 실제로 엉뚱한 값을 붙이던 사진이 이 웨이퍼에 있다 — 회귀 가드가 공허하지 않게.
    from aoi_verification.app.coords import abs_coord, surface_flt
    recs = surface_flt.load_folder(WAFER)
    near = [p for p in height if geometry._nearest(recs, abs_coord.absolute_xy(p), geometry.GEOMETRY_MATCH_TOL)]
    assert len(near) == 4


def test_info_lines_for_a_height_photo():
    p = WAFER / "275316.275316.c._9_.4.jpeg"
    lines = single_info.defect_lines(p)
    assert lines[:5] == ["recipe BS_3D / zone amk_mask_6_170", "type Height-amk_mask_6_170",
                         f"defect {geometry.resolve(p).geometry.type_name}", "classify Undersized Bump",
                         f"height {geometry.resolve(p).geometry.height_um:.2f} ㎛"]
    assert not any(ln.startswith(("area", "width", "length", "contrast")) for ln in lines)


def test_no_sidecar_means_no_height_info(tmp_path):
    for f in WAFER.iterdir():
        if f.is_file() and f.name != "Height.flt.md":
            shutil.copy(f, tmp_path / f.name)
    p = tmp_path / "275316.275316.c._9_.4.jpeg"
    res = geometry.resolve(p)
    assert res.status == "no_data" and res.geometry is None


def test_class_names_read_from_the_folder():
    assert class_names.type_name(WAFER, 2, 2097152) == "Missing Bump"
    assert class_names.type_name(WAFER, 0, 2048) == "Long bright defect"
    assert class_names.type_name(WAFER, 2, 3) == ""                 # 사전에 없는 값은 지어내지 않는다
    assert class_names.type_name(WAFER, None, 2048) == ""
    assert class_names.class_name(WAFER, 51) == "B R Height LSL"
    assert class_names.class_name(WAFER, -22) == "Scratch"           # 사람이 다시 분류한 표시
    assert class_names.class_name(WAFER, 9999) == ""


def test_zone_name_prefers_the_recipe_folder(tmp_path):
    for sub, name in (("Zones", "A"), ("Recipe3-Zones", "B")):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / f"{name}.ini").write_text(f"[General]\nZoneName={name}\nZoneID=1\n")
    assert zone_name.name_for(tmp_path, 1) == "A"                    # 레시피 없이 = 예전 동작
    assert zone_name.name_for(tmp_path, 1, 1) == "A"
    assert zone_name.name_for(tmp_path, 1, 3) == "B"
    assert zone_name.name_for(tmp_path, 1, 4) == "A"                 # 그 레시피 폴더가 없으면 합친 표


def test_class_entry_with_empty_value_does_not_swallow_next_line(tmp_path):
    (tmp_path / "ManReClassify.ini").write_text("[General]\n5=\n6=Scratch,Defect\n", encoding="utf-8")
    assert class_names.class_name(tmp_path, 5) == ""
    assert class_names.class_name(tmp_path, 6) == "Scratch"


def test_height_photo_without_any_result_file_is_unsupported(tmp_path):
    shutil.copy(WAFER / "ColorImageGrabingInfo.ini", tmp_path)
    assert geometry.resolve(tmp_path / "275316.275316.c._9_.4.jpeg").status == "no_flt"
