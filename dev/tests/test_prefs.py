"""utils.prefs — 라운드트립 / patch / 기본값."""

from aoi_verification.app.utils import prefs


def test_defaults():
    p = prefs.UiPrefs()
    assert 0.0 <= p.threshold <= 1.0
    assert p.image_long_edge_select >= 300
    # 신규 사용자는 좌표 매칭으로 시작한다(구형 모드로 떠밀리던 버그 고정).
    assert p.engine_mode == prefs.EngineMode.COORDINATE
    assert p.legacy_enabled is None            # '아직 설정 안 됨'
    assert p.legacy_engine == ""
    assert p.color_mode == "light"


# ── 구형 모드 상태 유도 (순수 함수 — Qt 불필요) ─────────────────────────────
def test_resolve_legacy_fresh_prefs_is_off():
    on, sub = prefs.resolve_legacy_state(prefs.UiPrefs())
    assert on is False and sub == prefs.EngineMode.BASIC


def test_resolve_legacy_old_basic_falls_back_to_coordinate():
    """옛 기본값 'basic' 은 '직접 고름'과 구분 불가 → 좌표로 되돌린다(하위 선택은 기억)."""
    p = prefs.UiPrefs(engine_mode=prefs.EngineMode.BASIC)   # legacy_enabled 미설정
    on, sub = prefs.resolve_legacy_state(p)
    assert on is False
    assert sub == prefs.EngineMode.BASIC


def test_resolve_legacy_old_efficiency_is_deliberate():
    """'efficiency' 는 라디오를 직접 골라야 저장된다 → 의도적 선택으로 인정."""
    p = prefs.UiPrefs(engine_mode=prefs.EngineMode.EFFICIENCY)
    on, sub = prefs.resolve_legacy_state(p)
    assert on is True
    assert sub == prefs.EngineMode.EFFICIENCY


def test_resolve_legacy_explicit_flag_wins():
    """스위치가 한 번 설정되면 그 값이 engine_mode 보다 우선한다."""
    off = prefs.UiPrefs(legacy_enabled=False,
                        engine_mode=prefs.EngineMode.EFFICIENCY)
    assert prefs.resolve_legacy_state(off)[0] is False
    on = prefs.UiPrefs(legacy_enabled=True, legacy_engine=prefs.EngineMode.BASIC,
                       engine_mode=prefs.EngineMode.COORDINATE)
    assert prefs.resolve_legacy_state(on) == (True, prefs.EngineMode.BASIC)


def test_resolve_legacy_engine_remembers_sub_choice():
    """engine_mode 가 coordinate 여도 legacy_engine 이 하위 선택을 기억한다."""
    p = prefs.UiPrefs(legacy_enabled=False,
                      legacy_engine=prefs.EngineMode.EFFICIENCY,
                      engine_mode=prefs.EngineMode.COORDINATE)
    on, sub = prefs.resolve_legacy_state(p)
    assert on is False and sub == prefs.EngineMode.EFFICIENCY


def test_new_fields_round_trip(isolated_cache):
    prefs.save(prefs.UiPrefs(legacy_enabled=True,
                             legacy_engine=prefs.EngineMode.EFFICIENCY,
                             color_mode="dark"))
    loaded = prefs.load()
    assert loaded.legacy_enabled is True          # None 과 구분되어 왕복
    assert loaded.legacy_engine == prefs.EngineMode.EFFICIENCY
    assert loaded.color_mode == "dark"


def test_prefs_json_without_switch_keys_yields_unset(isolated_cache):
    """스위치 도입 전 JSON 은 legacy_enabled 가 None 으로 로드되어 유도 대상이 된다."""
    p = prefs.UiPrefs.from_dict({"engine_mode": "basic", "threshold": 0.6})
    assert p.legacy_enabled is None
    assert prefs.resolve_legacy_state(p)[0] is False


def test_legacy_json_with_removed_field_loads(isolated_cache):
    """제거된 옛 키(ui_variant)가 남은 JSON 도 무시하고 로드(하위호환)."""
    # reduce_motion 도 같은 이유로 제거된 키다(모션은 항상 켜진다).
    p = prefs.UiPrefs.from_dict({"threshold": 0.6, "ui_variant": "instrument",
                                 "reduce_motion": True})
    assert p.threshold == 0.6
    assert not hasattr(p, "ui_variant")
    assert not hasattr(p, "reduce_motion")


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
