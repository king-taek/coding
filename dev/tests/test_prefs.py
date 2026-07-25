"""utils.prefs — 라운드트립 / patch / 기본값."""

from aoi_verification.app.utils import prefs


def test_defaults():
    p = prefs.UiPrefs()
    assert 0.0 <= p.threshold <= 1.0
    assert p.image_long_edge_select >= 300
    assert p.reduce_motion is False


def test_reduce_motion_round_trip(isolated_cache):
    prefs.save(prefs.UiPrefs(reduce_motion=True))
    assert prefs.load().reduce_motion is True


def test_legacy_json_with_removed_field_loads(isolated_cache):
    """제거된 옛 키(ui_variant)가 남은 JSON 도 무시하고 로드(하위호환)."""
    p = prefs.UiPrefs.from_dict({"threshold": 0.6, "ui_variant": "instrument"})
    assert p.threshold == 0.6
    assert not hasattr(p, "ui_variant")
    assert p.reduce_motion is False


def test_round_trip(tmp_path, isolated_cache):
    p = prefs.UiPrefs(
        threshold=0.55,
        image_long_edge_select=900,
        image_long_edge_match=1000,
        last_ref_machine="2호기",
    )
    prefs.save(p)
    loaded = prefs.load()
    assert loaded.threshold == 0.55
    assert loaded.image_long_edge_select == 900
    assert loaded.image_long_edge_match == 1000
    assert loaded.last_ref_machine == "2호기"


def test_patch_updates_in_place(isolated_cache):
    prefs.save(prefs.UiPrefs(threshold=0.5))
    out = prefs.patch(threshold=0.8, last_val_machine="4호기")
    assert out.threshold == 0.8
    assert out.last_val_machine == "4호기"
    # 디스크에도 반영
    again = prefs.load()
    assert again.threshold == 0.8
    assert again.last_val_machine == "4호기"


def test_corrupt_file_falls_back_to_default(isolated_cache):
    file = prefs._file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("not-json", encoding="utf-8")
    p = prefs.load()
    # 교차 호기 친화 기본값 — 절대 값보다 ‘0.5 근처’ 라는 의도 검증.
    assert 0.40 <= p.threshold <= 0.70


def test_window_and_splitter_keys_round_trip(isolated_cache):
    """창 크기 / 최대화 / splitter 상태 / 사용 방법 펼침 / 빠른 모드."""
    p = prefs.UiPrefs(
        window_width=1600,
        window_height=900,
        window_maximized=True,
        splitter_state_select_h="QlpoOTFBWQ==",
        splitter_state_select_v="YWJjZA==",
        splitter_state_match_h="ZHVtbXk=",
        howto_expanded=True,
        speed_mode=True,
    )
    prefs.save(p)
    loaded = prefs.load()
    assert loaded.window_width == 1600
    assert loaded.window_height == 900
    assert loaded.window_maximized is True
    assert loaded.splitter_state_select_h == "QlpoOTFBWQ=="
    assert loaded.splitter_state_select_v == "YWJjZA=="
    assert loaded.splitter_state_match_h == "ZHVtbXk="
    assert loaded.howto_expanded is True
    assert loaded.speed_mode is True


def test_window_keys_default_to_unset(isolated_cache):
    """기본값은 ‘미설정’ 의미로 0/False."""
    p = prefs.UiPrefs()
    assert p.window_width == 0
    assert p.window_height == 0
    assert p.window_maximized is False
    assert p.howto_expanded is False
    assert p.speed_mode is False
