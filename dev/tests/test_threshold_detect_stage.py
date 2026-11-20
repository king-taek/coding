"""Overkill 튜닝 — 검출 단계(Dark/Bright) 하한: Zone ini [Surface] 읽기 · 극성 가르기 · 올리면 빠지는 Defect · 화면."""

from __future__ import annotations

from pathlib import Path

import pytest

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

_SURF = """[General]
ZoneID=63
ZoneName=Scan Area
[Surface]
Enable=1
AlgorithmName=Surface
BrightArea=30
BrightDiameter=3.000000
DarkArea=38
DarkDiameter=0
DarkLength=9999999
"""

_EQUIP = ("[SURFACE]\n4=Very Dark;\tRT2D_VERY_DARK\n8=Area Dark;\tRT2D_AREA_DARK\n16=Area Bright;\tRT2D_AREA_BRIGHT\n"
          "4096=Big;\tRT2D_BIG\n536870912=Event Defect;\tRT2D_EVENT\n")


def _zone(**detect) -> R.Zone:
    return R.Zone(63, "Scan Area", {29: (R.Filter(R.FEATURE_WIDTH, 5, 3.0, 3.0),)},
                  {tuple(k.split("_")): v for k, v in detect.items()})


def _d(i, stage, area, width, by_filter=False) -> P.Defect:
    return P.Defect(i, "Zones", 63, 29, 29, area, width, 9.0, 0.0, float(i), 0.0, by_filter=by_filter,
                    stage=stage if not by_filter else None)


def test_parse_detect_limits():
    z = R._parse_zone(_SURF)
    assert z.detect == {("bright", "area"): 30.0, ("bright", "width"): 3.0, ("dark", "area"): 38.0}   # 0 · 9999999 아님 → Length 는 안 읽음
    assert R._parse_zone(_SURF.replace("Enable=1", "Enable=0")).detect == {}
    assert R._parse_zone(_SURF.replace("AlgorithmName=Surface", "AlgorithmName=Other")).detect == {}
    assert R._parse_zone("[General]\nZoneID=1\n").detect == {}


def test_stage_masks_from_file_not_hardcoded(tmp_path):
    (tmp_path / "EquipmentClassifications.ini").write_text(_EQUIP, encoding="utf-8")
    R.surface_result_types.cache_clear()
    m = R.stage_masks(tmp_path)
    assert m == {"dark": 8 + 4, "bright": 16}                      # Event · Big 은 극성 없음
    assert R.detect_polarity(m, 8) == "dark" and R.detect_polarity(m, 16) == "bright"
    assert R.detect_polarity(m, 4096) is None and R.detect_polarity(m, 8 | 16) is None
    assert R.detect_polarity(m, None) is None
    R.surface_result_types.cache_clear()


def test_detect_scaled_with_recipe_pixels(monkeypatch):
    monkeypatch.setattr(R, "recipe_scale", lambda folder, zd: (2.0, 4.0))
    z = R.Zone(1, "x", {}, {("dark", "area"): 10.0, ("dark", "width"): 3.0})
    out = R.zones_in_flt_px(Path("."), {("Recipe2-Zones", 1): z})[("Recipe2-Zones", 1)]
    assert out.detect == {("dark", "area"): 40.0, ("dark", "width"): 6.0}


def test_detect_drop_by_polarity_and_override_keys():
    z = {("Zones", 63): _zone(dark_area=38.0, dark_width=3.0, bright_area=30.0)}
    dark, bright = _d(0, "dark", 40.0, 3.5), _d(1, "bright", 40.0, 3.5)
    assert P.is_kept(dark, z) and P.is_kept(bright, z)                          # 레시피 값 = 안 빠짐
    ov = {("Zones", 63, "dark", "area"): 45.0}
    assert P.why_dropped(dark, z, ov) == ("dark", "area") and P.is_kept(bright, z, ov)   # 극성이 다르면 영향 없음
    assert P.why_dropped(dark, z, {("Zones", 63, "dark", "width"): 4.0}) == ("dark", "width")
    assert P.detect_ok(bright, z["Zones", 63], "width") is None                          # bright_width 는 레시피에 없음(꺼짐)
    # 키 충돌 없음 — Feature Filter 키(정수 Classify)는 검출 단계 Defect 에 영향이 없고, 그 반대도 같다
    assert P.is_kept(dark, z, {("Zones", 63, 29, R.FEATURE_WIDTH): 99.0})
    ev = _d(2, None, 40.0, 3.5, by_filter=True)
    assert not P.is_kept(ev, z, {("Zones", 63, 29, R.FEATURE_WIDTH): 99.0})
    assert P.is_kept(ev, z, {("Zones", 63, "dark", "area"): 999.0})
    assert P.is_kept(_d(3, None, 1.0, 0.1), z, {("Zones", 63, "dark", "area"): 999.0})   # 극성 모름(Big 등)은 안 건드림


def test_same_scan_mag_recipe_is_not_rescaled(tmp_path):
    """같은 배율(5x · 5x) 레시피는 픽셀 크기 표기가 조금 달라도(1.538 vs 1.536) 환산하지 않는다 — 환산하면
    x5 DarkArea 38 이 38.10 이 돼 최솟값 38.099 Defect 가 빠졌다(실측).  배율이 다르면(20x) 환산한다."""
    (tmp_path / "Params_WaferInfo.ini").write_text("[I]\nRefPixelSizeX=1.5359512\nRefPixelSizeY=1.5359512\n")
    (tmp_path / "Recipe2-ProductInfo.ini").write_text("[G]\nScan2DPixelSize=1.538\n")
    (tmp_path / "Recipe3-ProductInfo.ini").write_text("[G]\nScan2DPixelSize=0.3859\n")
    (tmp_path / "RecipesInfo.ini").write_text(
        "[Recipe-1]\nName=Align\nScanMag=5\n[Recipe-2]\nName=x5\nScanMag=5\n[Recipe-3]\nName=x20\nScanMag=20\n")
    assert R.recipe_scale(tmp_path, "Recipe2-Zones") == (1.0, 1.0)
    lin, _area = R.recipe_scale(tmp_path, "Recipe3-Zones")
    assert abs(lin - 0.3859 / 1.5359512) < 1e-9
    z = {("Recipe2-Zones", 63): _zone(dark_area=38.0, dark_width=3.0)}
    import dataclasses
    d = dataclasses.replace(_d(0, "dark", 38.09902, 3.0070), zone_dir="Recipe2-Zones")
    assert P.recipe_misses([d], R.zones_in_flt_px(tmp_path, z)) == []


def test_recommend_raises_detect_to_min_of_picked_same_polarity():
    z = {("Zones", 63): _zone(dark_area=38.0, dark_width=3.0, bright_area=30.0)}
    picked = [_d(0, "dark", 50.0, 4.0), _d(1, "dark", 60.0, 3.5)]
    ov, missed = P.recommend(picked, z)
    assert ov == {("Zones", 63, "dark", "area"): 50.0, ("Zones", 63, "dark", "width"): 3.5} and missed == []
    other = _d(2, "dark", 41.0, 3.2)
    assert not P.is_kept(other, z, ov) and all(P.is_kept(d, z, ov) for d in picked)
    assert P.recommend([_d(3, "dark", 38.0, 3.0)], z)[0] == {}                 # 레시피보다 안 올림


def test_load_all_marks_stage(tmp_path, monkeypatch):
    """Type 비트 → by_filter · stage · rtype (비트는 파일에서 읽는다)."""
    (tmp_path / "EquipmentClassifications.ini").write_text(_EQUIP, encoding="utf-8")
    R.surface_result_types.cache_clear()
    d = P.Defect(0, "Zones", 1, 29, 29, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0)
    masks = R.stage_masks(tmp_path)
    import dataclasses
    d8 = dataclasses.replace(d, stage=R.detect_polarity(masks, 8), rtype=8, by_filter=False)
    assert d8.stage == "dark"
    R.surface_result_types.cache_clear()


# ---------------------------------------------------------------- 화면
pytest.importorskip("PyQt6.QtWidgets")


def test_dialog_shows_detect_group_and_raised_dark_area_drops_type8(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.i18n import ko as _KO
    zones = {("Zones", 63): _zone(dark_area=38.0, dark_width=3.0)}
    defects = (_d(0, "dark", 40.0, 3.5), _d(1, "dark", 60.0, 3.5), _d(2, None, 99.0, 9.0, by_filter=True))
    w = T.WaferData(tmp_path, defects, (), zones, {29: R.ClassInfo("A", False)}, (1.0, 1.0))
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(w)
        assert ("dark", "area") in dlg.sliders and ("dark", "width") in dlg.sliders
        assert ("bright", "area") not in dlg.sliders                       # 레시피에 없음(검출 Defect 도 없음)
        texts = [lab.text() for lab in dlg.rules.findChildren(T.QLabel)]
        assert _KO.THRESH_PREVIEW_DETECT_STAGE in texts
        assert dlg.drop_value.text() == "0" and not dlg.reset_btn.isEnabled()
        dlg.set_value("dark", "area", 50.0)
        assert dlg._ov == {("Zones", 63, "dark", "area"): 50.0}
        assert dlg.drop_value.text() == "1" and dlg.reset_btn.isEnabled() and dlg.export_btn.isEnabled()
        assert [d for d, _why in dlg.dropped()] == [defects[0]]
        dlg.set_value("dark", "area", 10.0)                                # 레시피 아래는 레시피로 맞춘다
        assert dlg._ov == {} and dlg.drop_value.text() == "0"
        dlg.set_value("dark", "width", 3.6)
        assert dlg.drop_value.text() == "2"
        dlg.reset()
        assert dlg._ov == {} and dlg.drop_value.text() == "0"
        out = tmp_path / "r.html"
        dlg.set_value("dark", "area", 50.0)
        dlg.export_html(out)
        assert "Dark Area" in out.read_text(encoding="utf-8")
    finally:
        dlg.deleteLater()
