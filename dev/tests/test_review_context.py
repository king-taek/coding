"""`MatchPage.review_context()` — 검토·결과 화면에 넘기는 상태의 단일 출처.

이전에는 `main_window` 가 MatchPage 의 비공개 속성을 여덟 군데에서 `getattr` 로
찌르고, 엔진 설정을 푸는 블록이 **두 벌 복붙**돼 있었다(한쪽만 좌표 실패 개수를
셌다).  두 벌이 갈라지면 좌표 모드 허용 오차가 조용히 기본값으로 돌아가고, 검토
후보 노출 규칙(허용 오차의 3배까지 차순위 표시)이 화면마다 달라진다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from dataclasses import replace                                # noqa: E402

from aoi_verification.app import config                        # noqa: E402
from aoi_verification.app.ui import main_window as mw          # noqa: E402
from aoi_verification.app.ui.pages import match_page as mp     # noqa: E402
from aoi_verification.app.utils.prefs import EngineMode        # noqa: E402


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    return styled_qapp


@pytest.fixture
def page(qapp):
    p = mp.MatchPage()
    try:
        yield p
    finally:
        p.deleteLater()


def test_defaults_when_matching_never_ran(page):
    """매칭을 한 번도 돌리지 않고 세션이 끝나도 안전한 값이 나온다."""
    ctx = page.review_context()
    assert ctx.coord_mode is False
    assert ctx.tolerance == config.DEFAULT_SIM_CONFIG.coord_tolerance
    assert ctx.coord_failed_count == 0
    assert ctx.elapsed_s == 0.0
    assert ctx.fast_results == {}
    assert ctx.score_cache is page._score_cache


def test_coordinate_mode_reports_tolerance_and_failures(page):
    """좌표 모드면 사용자가 정한 허용 오차와 실패 개수를 그대로 싣는다."""
    page._engine_cfg = replace(config.DEFAULT_SIM_CONFIG,
                               engine=EngineMode.COORDINATE,
                               coord_tolerance=123.0)
    page._coord_failed_set = {("S1", "a.jpg"), ("S1", "b.jpg")}
    ctx = page.review_context()
    assert ctx.coord_mode is True
    assert ctx.tolerance == 123.0
    assert ctx.coord_failed_count == 2


def test_failed_set_ignored_outside_coordinate_mode(page):
    """좌표 모드가 아니면 실패 집합은 세지 않는다(직전 세션 잔재일 수 있다)."""
    page._engine_cfg = replace(config.DEFAULT_SIM_CONFIG,
                               engine=EngineMode.EFFICIENCY)
    page._coord_failed_set = {("S1", "a.jpg")}
    ctx = page.review_context()
    assert ctx.coord_mode is False
    assert ctx.coord_failed_count == 0


def test_both_screens_get_the_same_values(page):
    """★ 검토 화면과 결과 화면이 **같은 한 번의 계산**을 나눠 쓴다.

    두 호출부가 각자 엔진 설정을 풀던 시절엔 여기가 갈라질 수 있었다."""
    page._engine_cfg = replace(config.DEFAULT_SIM_CONFIG,
                               engine=EngineMode.COORDINATE,
                               coord_tolerance=250.0)
    for_review = page.review_context()
    for_result = page.review_context()
    assert (for_review.coord_mode, for_review.tolerance) == \
           (for_result.coord_mode, for_result.tolerance) == (True, 250.0)


def test_main_window_does_not_reach_into_match_page_state():
    """`main_window` 가 검토용 상태를 비공개 속성에서 직접 꺼내지 않는다.

    다시 `getattr(self._match_page, "_...")` 로 꺼내기 시작하면 단일 출처가
    깨진다 — 그때 여기서 잡는다.  (수명 관리용 `_precompute_worker` 는 검토
    데이터가 아니라 예외로 둔다.)"""
    src = inspect.getsource(mw)
    leaked = [name for name in ("_score_cache", "_fast_results", "_engine_cfg",
                                "_coord_failed_set", "_precompute_elapsed")
              if f'self._match_page, "{name}"' in src]
    assert leaked == [], f"review_context() 를 우회해 직접 읽는다: {leaked}"


def test_review_context_is_immutable(page):
    """호출부가 받은 값을 고쳐 다른 화면에 다른 상태가 가지 않게."""
    ctx = page.review_context()
    with pytest.raises(Exception):
        ctx.tolerance = 1.0
