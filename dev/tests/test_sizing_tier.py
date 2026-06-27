"""config.SIZING_TIERS / pick_tier — 단위 테스트."""

from aoi_verification.app import config


def test_default_tier_for_small_dataset():
    # 화질 향상 (#3) — 작은 세션은 thumb 240/Q90, mid 800/Q88 로 한 단계 키움.
    t = config.pick_tier(50)
    assert t.thumb_px == 240 and t.thumb_q == 90
    assert t.mid_px == 800 and t.mid_q == 88


def test_mid_tier_for_201_500():
    assert config.pick_tier(201).thumb_px == 200
    assert config.pick_tier(500).thumb_px == 200


def test_third_tier_for_501_1000():
    assert config.pick_tier(501).thumb_px == 160
    assert config.pick_tier(1000).thumb_px == 160


def test_largest_tier_for_more_than_1000():
    t = config.pick_tier(5000)
    assert t.thumb_px == 140 and t.thumb_q == 65
    assert t.mid_px == 560 and t.mid_q == 75


def test_speed_mode_forces_lowest_tier():
    t = config.pick_tier(10, speed_mode=True)
    assert t.thumb_px == 140 and t.thumb_q == 65


def test_pixmap_cache_constants_present():
    assert config.PIXMAP_CACHE_MAX_BYTES > 0
    assert config.MEMORY_PRESSURE_BYTES > config.PIXMAP_CACHE_MAX_BYTES
