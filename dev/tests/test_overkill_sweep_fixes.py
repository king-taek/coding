"""Overkill 튜닝 — 받은 자료 전수 검사(2026-11-23)에서 나온 세 가지.

현택님(2026-11-23): "지금까지 준 자료들 하나하나 다 검사해".  수집 웨이퍼 356장 · Defect 539,710개를 앱 규칙으로
돌려 장비 분류와 대조했다.  틀린 곳 셋(관측):
  1. 레시피 N 기준을 flt 픽셀로 옮길 때 폴더 정밀값(RefPixelSizeX)을 써서 기준이 0.02% 커졌다 — 기준과 같은 Defect 27개가
     '레시피 값인데 빠짐'.  장비는 표기값끼리(``ProductInfo`` ↔ ``RecipeN-ProductInfo``) 나눈다.
  2. 잴 수 없는 Polarity 로 갈리는 두 묶음(29 ≥ 60 · 28 ≤ 40)을 둘 다 통과로 봐서, 28 기준을 올려도 B R Other 736개가 안 빠졌다.
  3. 구형 EAGLE 120 바이트 flt 의 orgReclassify 칸은 class 번호가 아니다(23504 ~ 27526).
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

ROOT = Path(__file__).resolve().parents[2] / "dev" / "Threshold 확인"
X10 = ROOT / "TB500_RDL2 Multi x10" / "S3M5S044XMC5"          # 4F-AOI-03 TB500_RDL2 - Multi HYP, 레시피 2 = x10
POL = ROOT / "Kendall A0_PI4 Polarity" / "00NVD325XYE7"       # AOI-21 R_Kendall A0_PI4 CUL-PIDS7
OLD = ROOT / "KENDALL MEOL 구형 flt" / "GX57527840"           # AOI-1 2D@RE-KENDALL_MEOL REVERSEETCH 2DG-1113S


def _load(folder):
    ds, _g, note = P.load_all(folder)
    assert note == ""
    return ds, R.zones_in_flt_px(folder, R.read_zones(folder))


def test_recipe_scale_divides_nominal_pixel_sizes():
    lin, area = R.recipe_scale(X10, "Recipe2-Zones")
    assert lin == pytest.approx(0.7698 / 0.3852, rel=1e-12) and area == pytest.approx(lin * lin, rel=1e-12)
    ds, zones = _load(X10)
    rec2 = [d for d in ds if d.zone_dir == "Recipe2-Zones"]
    assert len(rec2) == 81
    # 레시피 2 Defect 면적은 레시피 2 픽셀 수(정수)다 — 표기값 비로 나누면 81/81 정수, 정밀값 비로는 아니다(경쟁 가설 배제).
    assert all(abs(d.area / area - round(d.area / area)) < 1e-6 for d in rec2)
    ref = (0.7698 / 0.3851656) ** 2
    assert sum(abs(d.area / ref - round(d.area / ref)) < 1e-6 for d in rec2) == 0
    assert P.recipe_misses(ds, zones) == []          # DarkArea 35 px 와 같은 Defect 20개 · DarkDiameter 와 같은 1개 포함


def test_float_noise_is_same_value_but_not_infinity():
    assert R.same_value(139.7820163818286, 139.78201638182864)
    assert not R.same_value(139.78, 139.7820163818286)
    assert not R.same_value(5.0, math.inf) and R.same_value(math.inf, math.inf)
    f = R.Filter(R.FEATURE_AREA, 5, 139.78201638182864, 139.78201638182864)
    assert f.passes(139.7820163818286)
    strict = R.Filter(R.FEATURE_AREA, 4, 30.0, 30.0)
    assert not strict.passes(30.0) and strict.passes(math.nextafter(30.0, math.inf))   # 엄격 비교는 그대로


def test_polarity_keeps_other_bundle_out():
    ds, zones = _load(POL)
    key = ("Zones", 2)
    z = zones[key]
    assert {f.feature: (f.ftype, f.low) for f in z.bundles[29] if f.feature == 14} == {14: (5, 60.0)}
    assert {f.feature: (f.ftype, f.low) for f in z.bundles[28] if f.feature == 14} == {14: (7, 40.0)}
    other = [d for d in ds if (d.zone_dir, d.zone) == key and d.bin == 28]
    assert len(other) == 503
    assert P.recipe_misses(ds, zones) == []
    ov = {(*key, 28, R.FEATURE_AREA): 1e12}
    # 28 Area 를 끝까지 올리면 B R Other 는 전부 빠진다 — Polarity ≤ 40 인 Defect 는 29(≥ 60)로 못 남는다
    assert all(P.why_dropped(d, zones, ov) == (28, R.FEATURE_AREA) for d in other)
    dark = [d for d in ds if (d.zone_dir, d.zone) == key and d.bin == 29]
    assert dark and all(P.is_kept(d, zones, ov) for d in dark)


@pytest.mark.parametrize("a, b, apart", [
    ((5, 60.0, 60.0), (7, 40.0, 40.0), True),
    ((5, 40.0, 40.0), (7, 40.0, 40.0), False),      # 40 에서 만난다(둘 다 포함)
    ((4, 40.0, 40.0), (7, 40.0, 40.0), True),       # > 40 과 ≤ 40
    ((1, 10.0, 20.0), (5, 15.0, 15.0), False),
    ((2, 10.0, 20.0), (5, 15.0, 15.0), False),      # 범위 밖은 한 구간이 아니라 모른다
])
def test_span_overlap(a, b, apart):
    fa, fb = R.Filter(14, *a), R.Filter(14, *b)
    assert P._apart(P._span(fa), P._span(fb)) is apart
    z = R.Zone(1, "Z", {29: (fa,), 28: (fb,)})
    assert (P._ruled_out_by_own(z, 28, 29) is not None) is apart


def test_old_eagle_org_is_not_a_class():
    ds, zones = _load(OLD)
    classes = R.read_classes(OLD)
    assert len(ds) == 440 and all(abs(d.bin) in classes for d in ds)
    assert {d.bin for d in ds if d.zone == 1} == {33}              # Zone OP [Surface] Classify=33
    assert P.recipe_misses(ds, zones) == []


BS2 = ROOT / "T254 BS CUP BS_2" / "GX57010487"               # AOI-3 2D@R5-AS-T254-A01_0856927AY-0B BS CUP 6324 CMG-RE MIs AOI-16


def test_bs2_dark_and_bright_both_get_surface_classify():
    """BS_2 Scan Area: ``[Surface] Classify=33`` 하나에 Dark · Bright 하한이 같이 있다 — 장비는 Long dark · Long bright
    둘 다 33(B R Surface)으로 냈다(변환표라면 29 · 54).  Export Defects.csv 1,032줄도 전부 B R Surface(관측 2026-11-23)."""
    import csv
    ds, zones = _load(BS2)
    key = ("Recipe2-Zones", 63)
    mine = [d for d in ds if (d.zone_dir, d.zone) == key]
    assert {(d.stage, d.rtype) for d in mine} == {("dark", 1024), ("bright", 2048)}
    assert {d.bin for d in ds} == {33}
    rows = list(csv.DictReader((BS2 / "Defects.csv").open(encoding="utf-8", errors="replace")))
    assert len(rows) == len(ds) == 1032 and {r["Classify"] for r in rows} == {"B R Surface"}
    assert P.recipe_misses(ds, zones) == []
    # 극성마다 따로 거른다 — Bright Length 를 올려도 Dark Defect 는 안 빠진다
    ov = {(*key, "bright", "length"): 1e9}
    gone = {d.index for d in mine if not P.is_kept(d, zones, ov)}
    assert gone == {d.index for d in mine if d.stage == "bright"} and len(gone) == 16


def test_dialog_shows_dark_and_bright_as_separate_rows(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.i18n import ko as _KO
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(BS2, rich=False))
        dlg._set_zone(("Recipe2-Zones", 63))
        from PyQt6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)    # 앞 Zone 줄 위젯을 치운다
        texts = [lab.text() for lab in dlg.rules.findChildren(T.QLabel)]
        assert _KO.THRESH_PREVIEW_DETECT_STAGE_HEAD.format(pol="Dark") in texts
        assert _KO.THRESH_PREVIEW_DETECT_STAGE_HEAD.format(pol="Bright") in texts
        assert texts.count("→ B R Surface") == 2
        assert {k for k in dlg.sliders} >= {("dark", "length"), ("bright", "length")}
    finally:
        dlg.deleteLater()
