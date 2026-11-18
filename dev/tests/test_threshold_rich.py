"""RichSurface 로 기준 내리기 — 레시피 값이면 장비 Defect 와 같고, 내리면 새로 생기는 후보를 센다.

표본: TB500 4F-AOI-04 YDD-No Image 20234838EWG1 ``s_RichSurface.dat`` 의 86,600 번째부터 1,000 레코드
(Defect 가 가장 많이 몰린 구간) + 그 웨이퍼 ``Zones/PI_Opening.ini`` · ``.md`` 사이드카(실물).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from aoi_verification.app.threshold import rich as RS
from aoi_verification.app.threshold import rules as R

ROOT = Path(__file__).resolve().parents[2]
PI = ROOT / "dev" / "Threshold 확인" / "TB500 PI_Opening" / "20234838EWG1"
KEY = ("Zones", 1)


def test_recipe_values_reproduce_equipment_defects():
    """레시피 값 그대로면 통과 후보 = 장비가 Defect 로 남긴 것(reclassify 29), 레코드마다 같다(489/489)."""
    cand = RS.load_rich(PI)
    zone = R.read_zones(PI)[KEY]
    assert list(cand) == [KEY] and RS.supports(zone)
    got = RS.passes(cand[KEY], zone, KEY, {})
    rc = np.fromfile(PI / RS.RICH_FILE, dtype=np.dtype({"names": ["rc"], "formats": ["<i2"], "offsets": [0],
                                                       "itemsize": 456}))["rc"]
    assert int(got.sum()) == 489 and np.array_equal(got, rc == 29)


def test_lowering_counts_new_candidates_only():
    """Width 2.0 px · Area 15 px² · Contrast 70 으로 내리면 5개가 새로 생긴다(494 − 489, 올리면 0).  값은 Zone ini 와 같은 픽셀."""
    cand = RS.load_rich(PI)[KEY]
    zone = R.read_zones(PI)[KEY]
    low = {(*KEY, 29, R.FEATURE_WIDTH): 2.0, (*KEY, 29, R.FEATURE_AREA): 15.0,
           (*KEY, 29, R.FEATURE_CONTRAST): 70.0}
    assert RS.count_new(cand, zone, KEY, low) == 5
    assert RS.count_new(cand, zone, KEY, {(*KEY, 29, R.FEATURE_CONTRAST): 120.0}) == 0


def test_missing_or_broken_sidecar_gives_nothing(tmp_path):
    """사이드카가 없거나 열이 모자라면 내리기 없음 — 다른 값으로 메우지 않는다."""
    (tmp_path / RS.RICH_FILE).write_bytes(b"\0" * 456)
    assert RS.load_rich(tmp_path) == {}
    (tmp_path / (RS.RICH_FILE + ".md")).write_text('<root><RecordSize Size="456"/><Fields>'
                                                    '<Field Name="zone" Id="1" Offset="61" Vartype="17"/></Fields></root>')
    assert RS.load_rich(tmp_path) == {}
    assert not RS.supports(R.Zone(1, "x", {29: (R.Filter(14, 5, 1.0, 1.0),)}))      # Polarity 기준은 계산 못 함
