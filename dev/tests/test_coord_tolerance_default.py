"""좌표 허용 오차 기본값 — 단일 출처와 1회 이전.

★ 예전에는 리터럴 `500.0` 이 20곳 넘게 흩어져 있었다(진짜 기본값 3곳 + '값이 없을 때의
  폴백' 12곳+).  그러면 기본값을 바꿔도 폴백 경로에서 옛 값이 조용히 되살아난다.
  여기서 **모두가 같은 상수를 본다**는 것을 못 박는다.
"""

import pytest

from aoi_verification.app import config
from aoi_verification.app.utils import prefs as _prefs


def test_every_default_reads_the_single_source():
    """세 곳의 '진짜 기본값' 이 전부 `config.DEFAULT_COORD_TOLERANCE` 다."""
    dflt = config.DEFAULT_COORD_TOLERANCE
    assert config.DEFAULT_SIM_CONFIG.coord_tolerance == dflt
    assert _prefs.UiPrefs().coord_tolerance == dflt

    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    assert SetupInput.__dataclass_fields__["coord_tolerance"].default == dflt


def test_display_fallbacks_read_the_single_source():
    """'값이 안 들어왔을 때' 쓰는 폴백도 같은 상수를 본다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui import score_fmt
    from aoi_verification.app.ui.pages import match_review_page, result_page
    from aoi_verification.app.ui.widgets import matches_review, unmatched_review_dialog

    dflt = config.DEFAULT_COORD_TOLERANCE
    for mod in (score_fmt, match_review_page, result_page,
                matches_review, unmatched_review_dialog):
        assert mod._DFLT_TOL == dflt, mod.__name__


def test_matcher_falls_back_to_the_single_source():
    """cfg 가 없거나 tol 이 0 이어도 옛 500 이 되살아나지 않는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.workers import coord_matcher as cm

    class _Cfg:
        coord_tolerance = 0.0

    assert cm.CoordScheduler([], cfg=None)._tolerance == \
        config.DEFAULT_COORD_TOLERANCE
    assert cm.CoordScheduler([], cfg=_Cfg())._tolerance == \
        config.DEFAULT_COORD_TOLERANCE


# ---------------------------------------------------------------------------
# 1회 이전 — 저장된 파일이 있는 사용자
# ---------------------------------------------------------------------------
def test_new_user_gets_the_new_default():
    """설정 파일이 없으면 그냥 새 기본값이다(이전할 것이 없다)."""
    p = _prefs.UiPrefs()
    assert p.coord_tolerance == config.DEFAULT_COORD_TOLERANCE


def test_old_default_is_moved_once():
    """옛 기본값 500 을 그대로 쓰던 파일은 새 기본값으로 옮긴다.

    ★ 기본값만 바꾸면 기존 사용자는 **영원히 500 을 쓴다** — 셋업이 [검증 시작] 마다
    `coord_tolerance` 를 patch 하므로 한 번이라도 검증을 돌린 사람의 파일에는 500 이
    박혀 있다.
    """
    old = _prefs.UiPrefs(coord_tolerance=500.0, prefs_version=0)
    moved = _prefs.migrate(old)
    assert moved.coord_tolerance == config.DEFAULT_COORD_TOLERANCE
    assert moved.prefs_version == _prefs.PREFS_VERSION


def test_a_value_the_user_picked_is_left_alone():
    """직접 고른 값은 건드리지 않는다 — 사용자의 의도다."""
    chosen = _prefs.UiPrefs(coord_tolerance=350.0, prefs_version=0)
    assert _prefs.migrate(chosen).coord_tolerance == 350.0


def test_migration_runs_only_once():
    """이전이 끝난 파일은 다시 손대지 않는다.

    ★ 이것이 없으면 사용자가 나중에 **일부러 500 으로 올려도** 다음 실행에 200 으로
    되돌아간다 — 설정이 저절로 바뀌는 최악의 동작이다.
    """
    already = _prefs.UiPrefs(coord_tolerance=500.0,
                             prefs_version=_prefs.PREFS_VERSION)
    assert _prefs.migrate(already).coord_tolerance == 500.0


def test_migration_is_pure(tmp_path, monkeypatch):
    """순수 함수 — 파일을 쓰지 않는다."""
    monkeypatch.setenv("HOME", str(tmp_path))
    before = list(tmp_path.rglob("*.json"))
    _prefs.migrate(_prefs.UiPrefs(coord_tolerance=500.0, prefs_version=0))
    assert list(tmp_path.rglob("*.json")) == before
