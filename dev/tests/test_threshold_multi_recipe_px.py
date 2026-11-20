"""다중 레시피 폴더의 Zone 기준 단위 — `RecipeN-Zones` 값은 그 레시피 픽셀, Surface.flt 는 폴더 픽셀.

실물 근거(출처 등급 관측, AOI-20 TB500_RDL3 Multi 11장 2026-11-20): 레시피 3 Surface_RDL 폭 5.1832932 px × 0.3859
= 2.000 µm — 숫자를 그대로 비교하면 레시피 3 Defect 가 0/190 통과, µm 로 맞추면 190/190(= 장비).
여기서는 같은 구조를 합성 폴더(폴더 1.6 · 레시피 3 = 0.4)로 만든다 — 합성이라 골든 기대값이 아니라 동작 검사다.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

_MD = """<RecordSize Size="152"/>
<Field Name="reclassify" Id="0" Offset="0" Vartype="2"/>
<Field Name="orgReclassify" Id="1" Offset="2" Vartype="2"/>
<Field Name="ActualX" Id="5" Offset="24" Vartype="5"/>
<Field Name="ActualY" Id="6" Offset="32" Vartype="5"/>
<Field Name="zone" Id="13" Offset="61" Vartype="17"/>
<Field Name="recipe" Id="14" Offset="62" Vartype="17"/>
<Field Name="area" Id="17" Offset="80" Vartype="5"/>
<Field Name="BlobBreadth" Id="18" Offset="88" Vartype="5"/>
<Field Name="BlobFeretMax" Id="20" Offset="104" Vartype="5"/>
<Field Name="Contrast" Id="25" Offset="136" Vartype="5"/>
"""

_ZONE3 = """[General]
ZoneName=Surface_RDL
ZoneID=1
[GenesisV12]
AdaptDetect_Dark_Contrast_Seed=20.000000
FixedDetect_Dark_Contrast_Seed=255.000000
[Surface Feature Filter Width]
Classify=5
FeatureType=2
FilterType=5
LowValue=5.0
HighValue=5.0
[Surface Feature Filter Area]
Classify=5
FeatureType=1
FilterType=5
LowValue=25.0
HighValue=25.0
"""

_CLASSES = "[General]\n5=B R Surface,Defect,1,1,1\n"


def _rec(recipe, area, width):
    b = bytearray(152)
    struct.pack_into("<hh", b, 0, 5, 5)
    struct.pack_into("<dd", b, 24, 1000.0, 2000.0)
    struct.pack_into("<BB", b, 61, 1, recipe)
    struct.pack_into("<ddd", b, 80, area, width, 9.0)
    struct.pack_into("<d", b, 104, 9.5)
    return bytes(b)


def _wafer(tmp_path: Path, *, recipe_px: bool = True) -> Path:
    w = tmp_path / "W"
    (w / "Recipe3-Zones").mkdir(parents=True)
    (w / "Recipe3-Zones" / "Surface_RDL.ini").write_text(_ZONE3)
    (w / "Params_WaferInfo.ini").write_text("RefPixelSizeX=1.6\nRefPixelSizeY=1.6\n")
    if recipe_px:
        (w / "Recipe3-ProductInfo.ini").write_text("Scan2DPixelSize=0.4\n")
    (w / "RecipesInfo.ini").write_text("[Recipe-1]\nName=Align\n[Recipe-3]\nName=x20\n")
    (w / "ManReClassify.ini").write_text(_CLASSES)
    # 폭 1.3 px(2.08 µm) 남음 · 1.2 px(1.92 µm) 빠짐 — 면적은 충분히 크다(25 px² × 0.16 = 4 µm² → flt 1.5625 px²)
    (w / "Surface.flt").write_bytes(_rec(3, 5.0, 1.3) + _rec(3, 5.0, 1.2))
    (w / "Surface.flt.md").write_text(_MD)
    return w


def test_zones_in_flt_px_scale(tmp_path):
    w = _wafer(tmp_path)
    z = R.zones_in_flt_px(w, R.read_zones(w))[("Recipe3-Zones", 1)]
    fs = {f.feature: f for f in z.bundles[5]}
    assert fs[R.FEATURE_WIDTH].low == pytest.approx(5.0 * 0.4 / 1.6)          # 1.25 px
    assert fs[R.FEATURE_AREA].low == pytest.approx(25.0 * 0.16 / 2.56)        # 1.5625 px²


def test_recipe_values_keep_and_drop_by_um(tmp_path):
    w = _wafer(tmp_path)
    D, _ = P.load_defects(w)
    raw = R.read_zones(w)
    assert len(P.recipe_misses(D, raw)) == 2                                  # 고치기 전: 둘 다 빠짐(레시피 px ≠ flt px)
    Z = R.zones_in_flt_px(w, raw)
    assert [d.width for d in P.recipe_misses(D, Z)] == [1.2]                  # 1.92 µm < 2 µm 만 빠진다
    assert P.is_kept(D[0], Z)
    f = Z[("Recipe3-Zones", 1)].bundles[5][0]
    assert f.low * 1.6 == pytest.approx(2.0)                                  # 화면 레시피 µm = flt px × 폴더 px


def test_unknown_recipe_px_leaves_values(tmp_path, caplog):
    w = _wafer(tmp_path, recipe_px=False)
    raw = R.read_zones(w)
    with caplog.at_level("WARNING", logger="aoi.threshold"):
        assert R.zones_in_flt_px(w, raw) == raw
    assert "픽셀 크기를 몰라" in caplog.text


def test_plain_zones_untouched(tmp_path):
    w = _wafer(tmp_path)
    (w / "Zones").mkdir()
    (w / "Zones" / "A.ini").write_text(_ZONE3)
    z = R.read_zones(w)
    assert R.zones_in_flt_px(w, z)[("Zones", 1)] == z[("Zones", 1)]


def test_detect_values_and_recipe_names(tmp_path):
    w = _wafer(tmp_path)
    assert R.read_detect_values(w, "Recipe3-Zones", 1) == [
        ("AdaptDetect_Dark_Contrast_Seed", "20.000000"), ("FixedDetect_Dark_Contrast_Seed", "255.000000")]
    assert R.read_detect_values(w, "Recipe3-Zones", 9) == []
    assert R.read_recipe_names(w) == {1: "Align", 3: "x20"}


def test_zone_label_shows_recipe_when_multi():
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import zone_label
    names = {1: "Align", 3: "x20"}
    assert zone_label("Zones", None, 1) == ("Zone 1", "")
    assert zone_label("Zones", None, 1, names, True) == ("Zone 1", "Align")
    assert zone_label("Recipe3-Zones", None, 1, names, True) == ("Zone 1", "x20")
    assert zone_label("Recipe2-Zones", None, 1, names, True) == ("Zone 1", "레시피 2")
