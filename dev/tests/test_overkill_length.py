"""Overkill 튜닝 — Zone ini Feature Filter 의 Length 기준(FeatureType 3).

현택님(2026-11-23): Length 기준이 파라미터에 있으면 넣고, 무의미하게 적혀 있으면 뺀다.
실물: Test_TB500_RDL2 - Multi x5(4F-AOI-04) 32256E25EWB4 — 레시피 2 Scan Area 묶음 두 개
(29 B R Surface Dark: Length ≥ 11.70 px · 28 B R Other: Length ≥ 4.55 px, 레시피 2 픽셀 1.538 µm).
Length 는 flt ``BlobLength`` 다 — 같은 레시피 42장 Event Defect 232/232 가 이 값으로 장비 분류와 맞았다(관측).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

ROOT = Path(__file__).resolve().parents[2]
RDL2 = ROOT / "dev" / "Threshold 확인" / "TB500_RDL2 Multi x5" / "32256E25EWB4"
KEY = ("Recipe2-Zones", 63)
LIN = 1.538 / 0.3851656          # Recipe2-ProductInfo Scan2DPixelSize ÷ Params_WaferInfo RefPixelSizeX


def _load(folder=RDL2):
    ds, _goods, note = P.load_all(folder)
    return ds, R.zones_in_flt_px(folder, R.read_zones(folder)), note


def _bundles_passed(d, z):
    """묶음마다 AND(값 없음 · Contrast 0 은 안 봄) — 장비가 붙일 수 있는 분류들."""
    out = set()
    for cls, fs in z.bundles.items():
        ok = True
        for f in fs:
            v = P._value(d, f.feature)
            if v is None or (f.feature == R.FEATURE_CONTRAST and v == 0):
                continue
            ok &= f.passes(v)
        if ok:
            out.add(cls)
    return out


def test_length_filters_are_read_in_flt_pixels():
    raw = R.read_zones(RDL2)[KEY]
    lens = {c: next(f.low for f in fs if f.feature == R.FEATURE_LENGTH) for c, fs in raw.bundles.items()}
    assert lens == pytest.approx({29: 11.7032744, 28: 4.5512734})
    scaled = R.zones_in_flt_px(RDL2, R.read_zones(RDL2))[KEY]
    got = {c: next(f.low for f in fs if f.feature == R.FEATURE_LENGTH) for c, fs in scaled.bundles.items()}
    assert got == pytest.approx({c: v * LIN for c, v in lens.items()}, rel=1e-6)


def test_equipment_class_is_the_only_bundle_passed_with_bloblength():
    """Event Defect 마다 통과하는 묶음이 장비 첫 분류 하나뿐 — Length 를 BlobLength 로 볼 때."""
    ds, zones, note = _load()
    assert note == ""
    mine = [d for d in ds if (d.zone_dir, d.zone) == KEY and d.by_filter]
    assert len(mine) == 10 and {d.bin for d in mine} == {28, 29}
    assert all(d.blob_length is not None for d in ds)
    assert all(_bundles_passed(d, zones[KEY]) == {d.bin} for d in mine)
    assert P.recipe_misses(mine, zones) == []


def test_raising_length_drops_only_shorter_defects():
    ds, zones, _ = _load()
    mine = [d for d in ds if (d.zone_dir, d.zone) == KEY and d.by_filter and d.bin == 28]
    cut = sorted(d.blob_length for d in mine)[len(mine) // 2]
    ov = {(*KEY, 28, R.FEATURE_LENGTH): cut}
    base = {d.index for d in ds if not P.is_kept(d, zones)}       # 레시피 값에서 이미 빠지는 것(다른 Zone 검출 단계 2개)
    dropped = {d.index for d in ds if not P.is_kept(d, zones, ov)} - base
    assert dropped == {d.index for d in mine if d.blob_length < cut}
    assert dropped and all(P.why_dropped(d, zones, ov) == (28, R.FEATURE_LENGTH) for d in mine if d.index in dropped)


_ZONE = """[General]
ZoneName=Z
ZoneID=7
{filters}
"""
_FILTER = """[Surface Feature Filter {n}]
Enable=1
AlgorithmName=Surface Feature Filter Length
Classify=28
FeatureType={ft}
FilterType={typ}
LowValue={lo}
HighValue={hi}
"""


@pytest.mark.parametrize("typ, lo, hi, kept", [
    (5, 0, 0, False),                  # ≥ 0 — 늘 통과
    (5, -1, -1, False),
    (4, -0.5, -0.5, False),            # > 음수
    (7, 9999999, 9999999, False),      # ≤ 끈 값
    (6, 9999999, 9999999, False),
    (1, 0, 9999999, False),            # 범위가 전부
    (5, 4.55, 4.55, True),
    (4, 0, 0, True),                   # > 0 은 0 인 것을 거른다
    (7, 40, 40, True),
    (1, 2, 9999999, True),
    (3, 0, 0, True),                   # = — 거의 다 거른다
])
def test_meaningless_length_is_not_offered(tmp_path, typ, lo, hi, kept):
    z = tmp_path / "Zones"
    z.mkdir()
    filters = _FILTER.format(n="Length", ft=3, typ=typ, lo=lo, hi=hi) + _FILTER.format(n="Area", ft=1, typ=5, lo=0, hi=0)
    (z / "z.ini").write_text(_ZONE.format(filters=filters), encoding="utf-8")
    fs = R.read_zones(tmp_path)[("Zones", 7)].bundles[28]
    assert any(f.feature == R.FEATURE_LENGTH for f in fs) is kept
    assert any(f.feature == R.FEATURE_AREA for f in fs)      # 다른 항목은 그대로(이번 규칙은 Length 만)


def test_old_sidecar_without_bloblength_ignores_length(tmp_path):
    """BlobLength 가 없는 사이드카 — Length 기준은 판정에서 빠지고(늘 통과) 나머지는 그대로."""
    w = tmp_path / RDL2.name
    shutil.copytree(RDL2, w)
    md = w / "Surface.flt.md"
    md.write_text(re.sub(r'<Field Name="BlobLength"[^>]*/>', "", md.read_text(encoding="utf-8")), encoding="utf-8")
    ds, zones, note = _load(w)
    assert note == "" and ds and all(d.blob_length is None for d in ds)
    ov = {(*KEY, 28, R.FEATURE_LENGTH): 1e9}
    assert all(P.is_kept(d, zones, ov) for d in ds if (d.zone_dir, d.zone) == KEY and d.by_filter)


def test_dialog_offers_length_slider(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        w = T.load_wafer(RDL2, rich=False)
        dlg.set_wafer(w)
        dlg._set_zone(KEY)
        assert (28, R.FEATURE_LENGTH) in dlg.sliders and (29, R.FEATURE_LENGTH) in dlg.sliders
        # 화면 값 = µm (레시피 2 픽셀 × ini px) — 4.5512734 px × 1.538 = 7.0 µm
        assert w.to_um(R.FEATURE_LENGTH, w.zones[KEY].bundles[28][1].low) == pytest.approx(7.0, abs=0.01)
        before = dlg.drop_value.text()
        dlg.set_value(28, R.FEATURE_LENGTH, 1000.0)
        assert dlg.drop_value.text() != before
        dlg._set_zone(("Zones", 63))                     # 레시피 1 Scan Area 에는 Length 기준이 없다
        assert not any(k[1] == R.FEATURE_LENGTH for k in dlg.sliders)
    finally:
        dlg.deleteLater()


def test_bundle_with_only_meaningless_length_still_passes_everything(tmp_path):
    """Length ≥ 0 하나뿐인 묶음은 사라지지 않고 '늘 통과' 묶음으로 남는다 — 장비는 묶음끼리 OR 다."""
    z = tmp_path / "Zones"
    z.mkdir()
    filters = (_FILTER.format(n="Length", ft=3, typ=5, lo=0, hi=0)
               + _FILTER.format(n="Area", ft=1, typ=5, lo=50, hi=50).replace("Classify=28", "Classify=29"))
    (z / "z.ini").write_text(_ZONE.format(filters=filters), encoding="utf-8")
    zone = R.read_zones(tmp_path)[("Zones", 7)]
    assert zone.bundles[28] == () and len(zone.bundles[29]) == 1
    d = P.Defect(0, "Zones", 7, 29, 29, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, blob_length=1.0)
    assert P.is_kept(d, {("Zones", 7): zone}, {("Zones", 7, 29, R.FEATURE_AREA): 1e9})
