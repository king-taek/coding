"""Overkill 튜닝 — 레시피가 다르면 Zone 이름 · 값이 같아도 따로 튜닝한다(현택님 2026-11-23).

레시피가 다르면 스캔을 새로 한 것이다.  예전에는 '같은 레시피' 를 Zone ini 내용 · 픽셀 크기로만 판단해서, 다른 Job
(레시피)으로 스캔한 웨이퍼라도 Zone 값이 같으면 한 Zone 줄 · 한 슬라이더로 합쳤다.  지금은 파일에 적힌 레시피
(``Params_WaferInfo.ini SetupPath`` · ``RecipesInfo.ini`` 이름)도 같아야 합친다 — :func:`rules.recipe_identity`.

한 웨이퍼 폴더 안의 다중 레시피는 이미 ``(Zone 폴더, ZoneID)`` 로 나뉜다 — 실물 T254 4레시피(BS_1~BS_3D,
Zone 이름 셋 다 같음)에서 Zone 줄 6개가 레시피별로 따로 섰다(관측 2026-11-23).  그 규칙도 여기서 못 박는다."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

ROOT = Path(__file__).resolve().parents[2]
FS_UBM = ROOT / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"
SETUP = r"c:\job\R_T254 LIVE_0858711PD_FS UBM\6324\Recipes\FS"


def _copy(dst: Path) -> Path:
    """실물 폴더 사본(사진은 저장소에 두지 않는다 — 빈 파일)."""
    shutil.copytree(FS_UBM, dst)
    for name in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines():
        if name and not name.startswith("#"):
            (dst / name).write_bytes(b"")
    return dst


def _set_setup(w: Path, value: str | None) -> None:
    """``Params_WaferInfo.ini`` 의 SetupPath 줄을 바꾼다(None 이면 줄을 지운다)."""
    p = w / "Params_WaferInfo.ini"
    lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
             if not ln.lower().startswith("setuppath")]
    if value is not None:
        lines.append(f"SetupPath={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- 파일에서 읽는 레시피
def test_recipe_identity_reads_setup_path_and_recipe_names(tmp_path):
    w = _copy(tmp_path / "w")
    assert R.recipe_identity(w) == (SETUP.lower(), ())                  # 끝 '\' 는 뗀다 · 레시피 하나(RecipesInfo 없음)
    (w / "RecipesInfo.ini").write_text("[Recipe-1]\nName=BS_1\n[Recipe-2]\nName=BS_2\n", encoding="utf-8")
    R.read_recipe_names.cache_clear()
    assert R.recipe_identity(w)[1] == ((1, "BS_1"), (2, "BS_2"))
    _set_setup(w, None)
    assert R.recipe_identity(w)[0] == ""                                # 모르면 빈 값(추측하지 않는다)


def test_same_recipe_ignores_unknown_and_case():
    a = (r"c:\job\a\setup1\recipes\x20", ((1, "x20"), (2, "x5")))
    assert R.same_recipe(a, a)
    assert not R.same_recipe(a, (r"c:\job\b\setup1\recipes\x20", a[1]))          # 다른 Job
    assert not R.same_recipe(a, (a[0], ((1, "x20"), (2, "PAD"))))                 # 다른 레시피 구성
    assert R.same_recipe(a, ("", a[1])) and R.same_recipe(a, (a[0], ()))          # 모르는 칸은 증거가 아니다


# --------------------------------------------------------------------------- 여러 폴더 합치기
def test_merge_keeps_same_recipe_and_skips_other_recipe_with_identical_zones(tmp_path):
    """Zone ini · 픽셀 크기가 똑같아도 다른 Job(레시피)으로 스캔한 웨이퍼는 합치지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a = _copy(tmp_path / "lot" / "27125535EWA5")
    same = _copy(tmp_path / "lot" / "27125535EWA6")
    _set_setup(same, SETUP.upper() + "\\")                             # 대소문자 · 끝 '\' 만 다름 = 같은 레시피
    other = _copy(tmp_path / "lot2" / "27125535EWA7")
    _set_setup(other, r"c:\job\R_T254 LIVE_0858711PD_FS UBM - COPY\6324\Recipes\FS")
    unknown = _copy(tmp_path / "lot3" / "27125535EWA8")
    _set_setup(unknown, None)                                           # 모르면 예전처럼 Zone 값으로만
    ws = [T.load_wafer(f) for f in (a, same, other, unknown)]
    assert ws[0].zones == ws[2].zones and ws[0].px == ws[2].px          # Zone 기준 · 픽셀은 똑같다
    m = T.merge_wafers(ws)
    assert m.folders == (a, same, unknown) and m.skipped == (other,)
    assert m.recipe_id == ws[0].recipe_id
    one = {(d.zone_dir, d.zone) for d in ws[0].defects}
    assert {(d.zone_dir, d.zone) for d in m.defects} == one              # 같은 레시피끼리는 여전히 같은 Zone 줄


def test_switching_to_other_recipe_resets_values(styled_qapp, tmp_path):
    """기준 웨이퍼가 다른 레시피로 바뀌면 Zone 값이 같아도 바꾼 값을 이어 가지 않는다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a = _copy(tmp_path / "a" / "27125535EWA5")
    b = _copy(tmp_path / "b" / "27125535EWA9")
    _set_setup(b, r"c:\job\OTHER\6324\Recipes\FS")
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.merge_wafers([T.load_wafer(a)]))
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert dlg._ov
        dlg.set_wafer(T.merge_wafers([T.load_wafer(b)]))
        assert dlg._ov == {} and dlg._base_reset
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        same = _copy(tmp_path / "c" / "27125535EWA6")
        _set_setup(same, r"c:\job\OTHER\6324\Recipes\FS")
        ov = dict(dlg._ov)
        dlg.set_wafer(T.merge_wafers([T.load_wafer(b), T.load_wafer(same)]))    # 같은 레시피를 더하면 값을 이어 간다
        assert dlg._ov == ov and not dlg._base_reset
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------------------- 한 폴더 안 다중 레시피
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

# 레시피마다 이름 · 값이 똑같은 Zone(실물 T254 BS_1 · BS_2 의 Zones.ini = Recipe2-Zones.ini 와 같은 모양)
_ZONE = """[General]
ZoneName=amk_mask_6_170
ZoneID=1
[Surface Feature Filter Area]
Classify=5
FeatureType=1
FilterType=5
LowValue=10.0
HighValue=10.0
"""


def _rec(recipe: int, area: float, x: float) -> bytes:
    import struct
    b = bytearray(152)
    struct.pack_into("<hh", b, 0, 5, 5)
    struct.pack_into("<dd", b, 24, x, 2000.0)
    struct.pack_into("<BB", b, 61, 1, recipe)
    struct.pack_into("<ddd", b, 80, area, 3.0, 9.0)
    struct.pack_into("<d", b, 104, 4.0)
    return bytes(b)


def _multi(tmp_path: Path, recs: bytes) -> Path:
    w = tmp_path / "W"
    for d in ("Zones", "Recipe2-Zones"):
        (w / d).mkdir(parents=True)
        (w / d / "amk_mask_6_170.ini").write_text(_ZONE, encoding="utf-8")
    (w / "Params_WaferInfo.ini").write_text("RefPixelSizeX=1.6\nRefPixelSizeY=1.6\n", encoding="utf-8")
    (w / "RecipesInfo.ini").write_text("[Recipe-1]\nName=BS_1\nScanMag=5\n[Recipe-2]\nName=BS_2\nScanMag=5\n",
                                       encoding="utf-8")
    (w / "ManReClassify.ini").write_text("[General]\n5=B R Surface,Defect,1,1,1\n", encoding="utf-8")
    (w / "Surface.flt").write_bytes(recs)
    (w / "Surface.flt.md").write_text(_MD, encoding="utf-8")
    return w


def test_same_zone_name_in_two_recipes_is_tuned_separately(tmp_path):
    """이름 · 값이 같은 Zone 이라도 레시피 1 · 2 는 따로 센다 — 레시피 2 값을 올려도 레시피 1 Defect 는 그대로."""
    w = _multi(tmp_path, _rec(1, 12.0, 1000.0) + _rec(1, 30.0, 1100.0) + _rec(2, 12.0, 1000.0))
    defects, note = P.load_defects(w)
    assert note == ""
    assert sorted((d.zone_dir, d.zone) for d in defects) == [("Recipe2-Zones", 1), ("Zones", 1), ("Zones", 1)]
    zones = R.read_zones(w)
    assert zones[("Zones", 1)] == zones[("Recipe2-Zones", 1)]            # 내용은 똑같다 — 그래도 키가 다르다
    ov = {("Recipe2-Zones", 1, 5, R.FEATURE_AREA): 20.0}
    assert P.count_by_bin(defects, zones, ov) == {("Zones", 1, 5): (2, 2), ("Recipe2-Zones", 1, 5): (1, 0)}


def test_recipe_tag_shown_even_when_only_recipe_1_has_defects(tmp_path):
    """Defect 가 레시피 1 에만 있어도 레시피 2 Zone 줄이 같은 이름으로 서므로, 레시피 1 줄에도 레시피를 적는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = T.load_wafer(_multi(tmp_path, _rec(1, 12.0, 1000.0)))
    assert w.note == ""
    assert w.label(("Zones", 1)) == ("amk_mask_6_170", "BS_1")
    assert w.label(("Recipe2-Zones", 1)) == ("amk_mask_6_170", "BS_2")


def test_single_recipe_folder_has_no_recipe_tag(tmp_path):
    """레시피 하나뿐인 스캔은 예전처럼 Zone 이름만(태그 없음)."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = T.load_wafer(_copy(tmp_path / "w"))
    assert all(w.label(k)[1] == "" for k in {(d.zone_dir, d.zone) for d in w.defects})


def test_zone_list_groups_by_recipe_first(styled_qapp, tmp_path):
    """Zone 목록은 레시피로 먼저 나누고 그 안에서 Zone(Defect 많은 순) — 레시피 머리줄 아래 줄에는 Zone 이름만(현택님 2026-11-23)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = _multi(tmp_path, _rec(1, 12.0, 1000.0) + _rec(2, 12.0, 1100.0) + _rec(2, 30.0, 1200.0))
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        # 레시피 2 에 Defect 가 더 많아도 레시피 1 이 먼저다
        assert dlg.zone_keys() == [("Zones", 1), ("Recipe2-Zones", 1)]
        assert {k: h.text() for k, h in dlg.recipe_heads.items()} == {
            "Zones": "Recipe 1 · BS_1", "Recipe2-Zones": "Recipe 2 · BS_2"}
        col = dlg._zone_col
        order = [col.itemAt(i).widget() for i in range(col.count())]
        assert order == [dlg.recipe_heads["Zones"], dlg.zone_btns[("Zones", 1)],
                         dlg.recipe_heads["Recipe2-Zones"], dlg.zone_btns[("Recipe2-Zones", 1)]]
        assert dlg.zone_btns[("Recipe2-Zones", 1)].text().splitlines()[0] == "amk_mask_6_170"
    finally:
        dlg.deleteLater()


def test_single_recipe_zone_list_has_no_recipe_head(styled_qapp, tmp_path):
    """레시피 하나뿐인 스캔은 머리줄 없이 예전처럼 Zone 줄만."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(_copy(tmp_path / "w")))
        assert dlg.recipe_heads == {} and dlg.zone_keys() == [("Zones", 1), ("Zones", 63)]
    finally:
        dlg.deleteLater()


def test_recipe_head_text():
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import recipe_head, recipe_no
    assert (recipe_no("Zones"), recipe_no("Recipe3-Zones")) == (1, 3)
    assert recipe_head("Recipe3-Zones", "x20") == "Recipe 3 · x20"
    assert recipe_head("Recipe2-Zones", "Recipe 2") == "Recipe 2"               # 이름이 없으면 번호만


# --------------------------------------------------------------------------- 한 레시피 결과가 없는 웨이퍼
def _drop_recipe2(w: Path) -> None:
    """레시피 2 결과가 없는 웨이퍼 — YDD-PIDS3 RE 35174144EWF6(Defect 3,000개 한도에서 끊겨 Recipe2-Zones ·
    RecipesInfo.ini 가 없다, 관측 2026-11-23)와 같은 모양."""
    shutil.rmtree(w / "Recipe2-Zones")
    (w / "RecipesInfo.ini").unlink()


def test_wafer_missing_one_recipe_merges_with_same_recipe(tmp_path):
    """같은 레시피인데 레시피 2 결과가 없는 웨이퍼도 합친다 — 레시피 1 Zone 은 둘 다, 레시피 2 Zone 은 있는 쪽만."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    full = _multi(tmp_path / "a", _rec(1, 12.0, 1000.0) + _rec(2, 12.0, 1100.0))
    cut = _multi(tmp_path / "b", _rec(1, 12.0, 1000.0) + _rec(1, 30.0, 1200.0))
    _drop_recipe2(cut)
    for order in ([cut, full], [full, cut]):                          # 어느 쪽을 먼저 골라도
        R.read_recipe_names.cache_clear()
        R.read_zones.cache_clear()
        m = T.merge_wafers([T.load_wafer(f) for f in order])
        assert m.folders == tuple(order) and m.skipped == ()
        assert set(m.zones) == {("Zones", 1), ("Recipe2-Zones", 1)}
        assert m.label(("Recipe2-Zones", 1)) == ("amk_mask_6_170", "BS_2")       # 이름은 RecipesInfo 가 있는 장에서
        assert sorted((d.zone_dir, d.wafer) for d in m.defects if d.zone_dir == "Recipe2-Zones") == [
            ("Recipe2-Zones", order.index(full))]


def test_shared_recipe_with_other_values_is_still_excluded(tmp_path):
    """둘 다 가진 레시피의 Zone 값이 하나라도 다르면 예전처럼 뺀다 — 셋째 장은 둘째 장이 들고 온 레시피 2 와도 맞아야 한다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    cut = _multi(tmp_path / "a", _rec(1, 12.0, 1000.0))
    _drop_recipe2(cut)
    full = _multi(tmp_path / "b", _rec(2, 12.0, 1100.0))
    other = _multi(tmp_path / "c", _rec(2, 12.0, 1100.0))
    ini = other / "Recipe2-Zones" / "amk_mask_6_170.ini"
    ini.write_text(ini.read_text(encoding="utf-8").replace("LowValue=10.0", "LowValue=11.0"), encoding="utf-8")
    R.read_zones.cache_clear()
    m = T.merge_wafers([T.load_wafer(f) for f in (cut, full, other)])
    assert m.folders == (cut, full) and m.skipped == (other,)


# --------------------------------------------------------------------------- 스캔 광학이 다르면 다른 레시피
_ACTIVE = ("[a5ef0cf7]\nScenarioName=ScenarioGrab_8eb8\nOpticsName=x7.5\nOpticId=737a\n"
           "[9a15d3bb]\nScenarioName=Scan2d\nOpticsName={name}\nOpticId=e8e4\n")


def _optic(w: Path, prefix: str = "", gl: str = "784.195595434267", zwafer: str = "4.50458576105711",
           name: str = "New") -> None:
    """YDD-PIDS3 RE 35174143EWB4 와 같은 모양(일부 키만) — ``ActiveScenarioOptics.ini`` Scan2d → ``OpticPreset.ini [New]``."""
    (w / f"{prefix}ActiveScenarioOptics.ini").write_text(_ACTIVE.format(name=name), encoding="utf-8")
    (w / f"{prefix}OpticPreset.ini").write_text(
        "[General]\nVersion=1\n[x7.5]\nMag=7.5\nLightSrcRef_NominalGL=10\n"
        f"[{name}]\nId=e8e48676\nZwafer={zwafer}\nMag=10\nCameraName=TDI\nLightSrcRef_NominalGL={gl}\n"
        "LightSrcRef_ColorFilter=CSI3\nAnalogGain=2\n", encoding="utf-8")


def test_read_scan_optics_follows_scan2d_scenario(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    assert R.read_scan_optics(w) == {}                                  # 파일이 없으면 모른다
    _optic(w)
    _optic(w, "Recipe2-", gl="370.617252397479", name="2D")
    o = R.read_scan_optics(w)
    assert set(o) == {"Zones", "Recipe2-Zones"}
    one = dict(o["Zones"])
    assert one["lightsrcref_nominalgl"] == 784.195595434267 and one["mag"] == 10.0     # [x7.5] 가 아니라 Scan2d 의 [New]
    assert "id" not in one and "zwafer" not in one                      # 웨이퍼 · 장비마다 바뀌는 칸은 뺀다
    assert dict(o["Recipe2-Zones"])["lightsrcref_nominalgl"] == 370.617252397479
    (w / "Recipe2-ActiveScenarioOptics.ini").write_text("[x]\nScenarioName=ScanCTS\nOpticsName=ScanCTS\n",
                                                        encoding="utf-8")
    assert set(R.read_scan_optics(w)) == {"Zones"}                      # Scan2d 가 없는 레시피(3D)는 적지 않는다


def test_merge_skips_wafer_with_other_scan_optics(tmp_path):
    """Zone · 레시피 이름이 같아도 스캔 광학(밝기)이 다르면 뺀다 — 초점 높이만 다르면 합친다 · 모르는 레시피는 안 본다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    base = _multi(tmp_path / "a", _rec(1, 12.0, 1000.0))
    focus = _multi(tmp_path / "b", _rec(1, 12.0, 1000.0))
    bright = _multi(tmp_path / "c", _rec(1, 12.0, 1000.0))
    cut = _multi(tmp_path / "d", _rec(1, 12.0, 1000.0))
    for w in (base, focus, bright, cut):
        _optic(w)
        _optic(w, "Recipe2-", gl="370.6", name="2D")
    _optic(focus, zwafer="4.61")
    _optic(bright, "Recipe2-", gl="380.0", name="2D")                   # 레시피 2 밝기만 다르다
    for f in ("Recipe2-ActiveScenarioOptics.ini", "Recipe2-OpticPreset.ini"):
        (cut / f).unlink()                                              # 레시피 2 광학을 모른다(결과가 끊긴 스캔)
    m = T.merge_wafers([T.load_wafer(f) for f in (base, focus, bright, cut)])
    assert m.folders == (base, focus, cut) and m.skipped == (bright,)
    assert not T.same_base(T.load_wafer(base), T.load_wafer(bright))   # 웨이퍼를 바꿀 때도 다른 레시피로 본다


# --------------------------------------------------------------------------- 픽셀 크기가 다른 폴더
def _px(w: Path, x: float) -> None:
    (w / "Params_WaferInfo.ini").write_text(f"RefPixelSizeX={x}\nRefPixelSizeY={x}\n", encoding="utf-8")


def test_merge_other_pixel_size_same_mag_keeps_pixels(tmp_path):
    """폴더 픽셀만 다르면(장비 보정 차이, 배율 같음) 합치고 **값은 픽셀 그대로** 센다 — 장비가 ini px 와 flt px 를 바로
    비교하기 때문(1.538 → 1.536 비로 바꾸면 14개가 빠졌다, 관측 2026-11-21).  빼지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a = _multi(tmp_path / "a", _rec(1, 12.0, 1000.0) + _rec(2, 9.0, 1100.0))
    b = _multi(tmp_path / "b", _rec(1, 10.5, 1000.0) + _rec(2, 10.0, 1100.0))
    _px(b, 1.62)
    wa, wb = T.load_wafer(a), T.load_wafer(b)
    assert wa.px != wb.px
    m = T.merge_wafers([wa, wb])
    assert m.folders == (a, b) and m.skipped == () and m.px == wa.px
    assert m.zones == wa.zones                                          # 기준값은 ini 숫자 그대로
    assert sorted(d.area for d in m.defects if d.wafer == 1) == sorted(d.area for d in wb.defects)


def test_merge_other_pixel_size_other_mag_recipe_converts_to_base_pixels(tmp_path):
    """배율이 다른 레시피(µm 로 비교되는 쪽)는 폴더 픽셀이 다르면 기준 웨이퍼 픽셀로 바꿔 합친다 — 바꾼 뒤에도 Defect 마다
    '값 ÷ 기준값' 이 그 웨이퍼 혼자일 때와 같다(= 장비와 같은 통과 · 탈락).  장비에 적을 숫자도 그대로다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.coords import pixel_size as PS
    a = _multi(tmp_path / "a", _rec(2, 60.0, 1100.0))
    b = _multi(tmp_path / "b", _rec(2, 61.0, 1100.0) + _rec(1, 11.0, 1000.0))
    for w in (a, b):
        (w / "RecipesInfo.ini").write_text("[Recipe-1]\nName=BS_1\nScanMag=5\n[Recipe-2]\nName=BS_2\nScanMag=20\n",
                                           encoding="utf-8")
        (w / "Recipe2-ProductInfo.ini").write_text("[General]\nScan2DPixelSize=0.4\n", encoding="utf-8")
    _px(b, 1.62)
    PS.scan_pixel_size_xy.cache_clear()
    PS._recipe_pixel_size_xy.cache_clear()
    R.read_recipe_names.cache_clear()
    wa, wb = T.load_wafer(a), T.load_wafer(b)
    key = ("Recipe2-Zones", 1)
    low = lambda w: w.zones[key].bundles[5][0].low                     # noqa: E731
    assert low(wa) != pytest.approx(low(wb))                            # 그대로 비교하면 다른 기준값
    m = T.merge_wafers([wa, wb])
    assert m.folders == (a, b) and m.skipped == ()
    assert low(m) == low(wa)
    own = {d.index: d.area / low(wb) for d in wb.defects if d.zone_dir == "Recipe2-Zones"}
    got = {d.index: d.area / low(m) for d in m.defects if d.wafer == 1 and d.zone_dir == "Recipe2-Zones"}
    assert got == pytest.approx(own)
    r1 = [d.area for d in m.defects if d.wafer == 1 and d.zone_dir == "Zones"]
    assert r1 == [11.0]                                                 # 레시피 1(배율 같음)은 픽셀 그대로
    assert low(m) / m.scales[key[0]][1] == pytest.approx(10.0)          # 장비 ini 숫자로 되돌리면 10
