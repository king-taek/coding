"""Overkill 튜닝 자동 추천 — 고른 Defect 는 전부 남고, 그 안에서 가장 많이 빠지는 하한인지."""

from __future__ import annotations

import itertools

from aoi_verification.app.threshold import preview as P
from aoi_verification.app.threshold import rules as R

F = R.Filter


def _zone(bundles):
    return {("Zones", 2): R.Zone(2, "RDL", bundles)}


def _d(i, b, width, area, contrast=0.0):
    return P.Defect(i, "Zones", 2, b, b, area, width, 9.0, contrast, 0.0, 0.0)


def test_raises_each_rule_to_smallest_picked_value():
    """묶음 하나(Width ≥ 2 AND Area ≥ 10): 고른 둘의 최솟값까지 올리고, 나머지는 그 아래라 빠진다."""
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 2.0, 2.0), F(R.FEATURE_AREA, 5, 10.0, 10.0))})
    ds = [_d(0, 29, 5.0, 40.0), _d(1, 29, 4.0, 60.0), _d(2, 29, 3.0, 80.0), _d(3, 29, 6.0, 20.0)]
    ov, missed = P.recommend([ds[0], ds[1]], zones)
    assert missed == []
    assert ov == {("Zones", 2, 29, R.FEATURE_WIDTH): 4.0, ("Zones", 2, 29, R.FEATURE_AREA): 40.0}
    assert [P.is_kept(d, zones, ov) for d in ds] == [True, True, False, False]


def test_is_optimal_against_brute_force():
    """작은 예에서 가능한 하한 조합을 전부 대 보아도, 고른 것을 다 남기면서 더 많이 빼는 조합은 없다."""
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 1.0, 1.0), F(R.FEATURE_AREA, 5, 5.0, 5.0)),
                   28: (F(R.FEATURE_AREA, 5, 5.0, 5.0),)})
    ds = [_d(0, 29, 3.0, 30.0), _d(1, 29, 2.0, 50.0), _d(2, 28, 1.0, 25.0), _d(3, 28, 1.0, 9.0),
          _d(4, 29, 5.0, 10.0), _d(5, 29, 1.5, 70.0)]
    picked = [ds[0], ds[2]]
    ov, missed = P.recommend(picked, zones)
    assert missed == []
    best = sum(not P.is_kept(d, zones, ov) for d in ds)
    widths = sorted({d.width for d in ds} | {1.0})
    areas = sorted({d.area for d in ds} | {5.0})
    for w29, a29, a28 in itertools.product(widths, areas, areas):
        trial = {("Zones", 2, 29, R.FEATURE_WIDTH): w29, ("Zones", 2, 29, R.FEATURE_AREA): a29,
                 ("Zones", 2, 28, R.FEATURE_AREA): a28}
        if all(P.is_kept(d, zones, trial) for d in picked):
            assert sum(not P.is_kept(d, zones, trial) for d in ds) <= best


def test_unpicked_bundle_and_zone_stay_at_recipe():
    """고른 게 없는 묶음 · Zone 은 건드리지 않는다.  레시피보다 내리지도 않는다."""
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 2.0, 2.0),), 28: (F(R.FEATURE_AREA, 5, 10.0, 10.0),)})
    zones[("Zones", 7)] = R.Zone(7, "Pad", {14: (F(R.FEATURE_AREA, 5, 1.0, 1.0),)})
    ov, _ = P.recommend([_d(0, 29, 2.0, 99.0)], zones)          # Width 2 = 레시피 → 올릴 것 없음
    assert ov == {}
    ov, _ = P.recommend([_d(0, 29, 3.0, 99.0)], zones)
    assert list(ov) == [("Zones", 2, 29, R.FEATURE_WIDTH)]


def test_unmeasured_contrast_is_ignored_and_strict_filter_stays_below():
    """Contrast 0 은 안 잼이라 최솟값에서 빼고, '>'(FilterType 4) 는 값보다 아래로 둔다."""
    zones = _zone({29: (F(R.FEATURE_CONTRAST, 5, 20.0, 20.0), F(R.FEATURE_AREA, 4, 10.0, 10.0))})
    picked = [_d(0, 29, 1.0, 30.0, 0.0), _d(1, 29, 1.0, 50.0, 60.0)]
    ov, missed = P.recommend(picked, zones)
    assert missed == []
    assert ov[("Zones", 2, 29, R.FEATURE_CONTRAST)] == 60.0
    assert ov[("Zones", 2, 29, R.FEATURE_AREA)] < 30.0
    assert all(P.is_kept(d, zones, ov) for d in picked)


def test_snap_rounds_down_for_display():
    """화면 자리로 내린 값을 쓴다 — 화면 숫자를 장비에 그대로 적어도 고른 것이 남는다."""
    zones = _zone({29: (F(R.FEATURE_AREA, 5, 10.0, 10.0),)})
    ov, _ = P.recommend([_d(0, 29, 1.0, 37.98)], zones, snap=lambda _f, v, _s: int(v * 10) / 10)
    assert ov == {("Zones", 2, 29, R.FEATURE_AREA): 37.9}


def test_bin_without_bundle_is_guarded_by_every_passing_bundle():
    """첫 분류가 묶음에 없으면 레시피에서 통과하는 묶음 전부가 지킨다(어느 묶음이 잡았는지 모른다)."""
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 2.0, 2.0),), 28: (F(R.FEATURE_AREA, 5, 10.0, 10.0),)})
    d = _d(0, 5, 3.0, 40.0)
    ov, missed = P.recommend([d], zones)
    assert missed == [] and ov == {("Zones", 2, 29, R.FEATURE_WIDTH): 3.0, ("Zones", 2, 28, R.FEATURE_AREA): 40.0}


def test_picked_defect_already_dropped_at_recipe_is_reported():
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 2.0, 2.0),)})
    d = _d(0, 29, 1.0, 40.0)
    assert P.recommend([d], zones)[1] == [d]


def test_nothing_picked_returns_nothing():
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 2.0, 2.0),)})
    assert P.recommend([], zones) == ({}, [])


def test_all_contrast_unmeasured_leaves_contrast_at_recipe():
    zones = _zone({29: (F(R.FEATURE_CONTRAST, 5, 20.0, 20.0), F(R.FEATURE_WIDTH, 5, 1.0, 1.0))})
    ov, missed = P.recommend([_d(0, 29, 3.0, 9.0, 0.0)], zones)
    assert missed == [] and ov == {("Zones", 2, 29, R.FEATURE_WIDTH): 3.0}


def test_own_bundle_that_does_not_pass_is_not_trusted():
    """첫 분류 묶음을 레시피에서 통과하지 못하면(다른 묶음이 잡았다) 통과하는 묶음이 지킨다."""
    zones = _zone({29: (F(R.FEATURE_WIDTH, 5, 2.0, 2.0),), 28: (F(R.FEATURE_AREA, 5, 10.0, 10.0),)})
    d = _d(0, 29, 1.0, 40.0)                     # 29(Width ≥ 2)는 못 넘고 28(Area ≥ 10)이 잡았다
    ov, missed = P.recommend([d], zones)
    assert missed == [] and ov == {("Zones", 2, 28, R.FEATURE_AREA): 40.0}


def test_bad_snap_withdraws_that_bundle():
    """내림이 틀려 고른 것이 빠질 값이 나오면 그 묶음 추천을 거두고 레시피 값으로 둔다."""
    zones = _zone({29: (F(R.FEATURE_AREA, 5, 10.0, 10.0),)})
    ov, missed = P.recommend([_d(0, 29, 1.0, 37.98)], zones, snap=lambda _f, v, _s: v + 1)
    assert ov == {} and missed == []
