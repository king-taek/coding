"""Threshold 미리보기 코어(`app.threshold`) — Zone ini 묶음 · class 이름 · Defect 세기."""

from __future__ import annotations

import csv
import struct
from pathlib import Path

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

ROOT = Path(__file__).resolve().parents[2]
FS_UBM = ROOT / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"

# 실물 Surface.flt.md 와 같은 이름 · 형(152 B 배치 중 필요한 칸만)
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

_RDL = """[General]
ZoneName=RDL
ZoneID=2
[Surface Feature Filter Width]
Enable=1
Classify=29
FeatureType=2
FilterType=5
LowValue=2.0
HighValue=2.0
[Surface Feature Filter Area]
Classify=29
FeatureType=1
FilterType=5
LowValue=60.0
HighValue=60.0
[Surface Feature Filter Contrast Average]
Classify=29
FeatureType=12
FilterType=5
LowValue=24.0
HighValue=24.0
[Surface Feature Filter Contrast Average_4]
Classify=28
FeatureType=12
FilterType=5
LowValue=34.0
HighValue=34.0
[Surface Feature Filter Area_5]
Classify=28
FeatureType=1
FilterType=5
LowValue=10.0
HighValue=10.0
[Surface Feature Filter Polarity]
Enable=0
Classify=28
FeatureType=14
FilterType=3
LowValue=1
HighValue=1
[GeoMetrology]
Classify=-1
"""

_CLASSES = """[General]
0=Accept,Good,0,2928128,32
20=EDC Good,Good,120,718119,0
28=B R Other,Defect,100,255,-2
29=B R Surface Dark,Defect,110,4227200,-2
[Customers]
20=Cust
"""


def _rec(cls, org, zone, recipe, area, width, contrast):
    b = bytearray(152)
    struct.pack_into("<hh", b, 0, cls, org)
    struct.pack_into("<dd", b, 24, 1000.0, 2000.0)
    struct.pack_into("<BB", b, 61, zone, recipe)
    struct.pack_into("<ddd", b, 80, area, width, 9.0)
    struct.pack_into("<d", b, 104, 9.5)
    struct.pack_into("<d", b, 136, contrast)
    return bytes(b)


def _wafer(tmp_path: Path, recs) -> Path:
    w = tmp_path / "W1"
    (w / "Zones").mkdir(parents=True)
    (w / "Zones" / "RDL.ini").write_text(_RDL)
    (w / "ManReClassify.ini").write_text(_CLASSES)
    (w / "Surface.flt").write_bytes(b"".join(recs))
    (w / "Surface.flt.md").write_text(_MD)
    return w


def test_zone_bundles_are_grouped_by_classify(tmp_path):
    """같은 Classify 끼리 한 묶음, Enable=0 · Classify<0 섹션은 뺀다."""
    w = _wafer(tmp_path, [])
    z = R.read_zones(w)[("Zones", 2)]
    assert z.name == "RDL" and sorted(z.bundles) == [28, 29]
    assert [f.feature for f in z.bundles[29]] == [2, 1, 12]
    assert [f.feature for f in z.bundles[28]] == [12, 1]          # Polarity(Enable=0) 빠짐


def test_classes_read_only_general_section(tmp_path):
    w = _wafer(tmp_path, [])
    c = R.read_classes(w)
    assert c[20].name == "EDC Good" and c[20].good and c[0].good and not c[29].good   # [Customers] 20=Cust 아님


def test_defects_bundles_or_and_counts(tmp_path):
    """묶음끼리 OR — 29 를 못 넘어도 28 을 넘으면 남는다.  EDC Good(첫 분류 20)은 안 세고 Accept 는 센다."""
    w = _wafer(tmp_path, [
        _rec(29, 29, 2, 1, 100, 3.0, 25),     # 29 통과
        _rec(0, 29, 2, 1, 100, 3.0, 30),      # Accept 로 바뀜 — 그래도 Defect, 첫 분류 29
        _rec(28, 28, 2, 1, 12, 1.0, 40),      # 29 는 폭 · 면적 미달, 28 통과
        _rec(20, 0, 2, 1, 100, 3.0, 25),      # EDC Good — 안 셈
        _rec(29, 29, 2, 1, 100, 3.0, 0),      # Contrast 0 = 안 잼 → Contrast 기준은 통과로 본다
    ])
    D, note = P.load_defects(w)
    Z = R.read_zones(w)
    assert note == "" and len(D) == 4 and [d.bin for d in D] == [29, 29, 28, 29]
    assert P.recipe_misses(D, Z) == []
    assert P.count_by_bin(D, Z) == {("Zones", 2, 29): (3, 3), ("Zones", 2, 28): (1, 1)}
    up = {("Zones", 2, 29, 12): 28.0}                                  # 29 묶음 Contrast 24 → 28
    assert P.count_by_bin(D, Z, up)[("Zones", 2, 29)] == (3, 2)        # 25 는 빠지고 30 · 0(안 잼)은 남음
    assert P.count_by_bin(D, Z, {("Zones", 2, 28, 12): 41.0})[("Zones", 2, 28)] == (1, 0)
    assert P.contrast_known(D, "Zones", 2)


def test_no_sidecar_gives_reason(tmp_path):
    w = _wafer(tmp_path, [_rec(29, 29, 2, 1, 100, 3.0, 25)])
    (w / "Surface.flt.md").unlink()
    assert P.load_defects(w) == ((), "Surface.flt.md 없음 또는 못 읽음")


def test_recipe_dir_for_multi_recipe():
    assert R.zone_dir_for(0) == R.zone_dir_for(1) == R.zone_dir_for(None) == "Zones"
    assert R.zone_dir_for(2) == "Recipe2-Zones"


def test_real_fs_ubm_wafer_matches_export():
    """실물 T254 FS UBM 27125535EWA5: Defect 수 = 장비 Export 14, 레시피 값이면 전부 남는다.
    장비 RTP.txt 기준(µm) 면적 30 · 폭 2 · Contrast 45 가 Zone ini 에서는 픽셀 값(면적 10.4997 px²)이다."""
    D, note = P.load_defects(FS_UBM)
    Z = R.read_zones(FS_UBM)
    rows = list(csv.DictReader(open(FS_UBM / "Defects.csv", encoding="utf-8-sig")))
    assert note == "" and len(D) == len(rows) == 14
    assert P.recipe_misses(D, Z) == []
    z = Z[("Zones", 1)]
    assert z.name == "amk_mask_6_110" and list(z.bundles) == [14]
    area = next(f for f in z.bundles[14] if f.feature == R.FEATURE_AREA)
    assert abs(area.low * 1.6903343 ** 2 - 30.0) < 0.01               # px² → µm² (Params_WaferInfo RefPixelSizeX)
    # Export Accept 인 Defect 도 첫 분류는 14 · 54 — 전부 센다
    assert sum(1 for r in rows if r["Classify"] == "Accept") == sum(1 for d in D if d.cls == 0)
