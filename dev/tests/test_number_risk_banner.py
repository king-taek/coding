"""번호 경고 큰 배너 — 매칭·검토 화면과 Wafer map 이 실제로 빨갛게 띄우는가(2026-11-13)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app import config, i18n                     # noqa: E402
from aoi_verification.app.coords import wafer_map as wm           # noqa: E402
from aoi_verification.app.utils.prefs import EngineMode           # noqa: E402

RISK = (("P8V798-04F4", 12), ("P8V798-08D0", 3))


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    return styled_qapp


def test_match_page_banner_follows_the_worker_and_reaches_review_context(qapp):
    from aoi_verification.app.ui.pages.match_page import MatchPage
    page = MatchPage()
    try:
        assert page.risk_banner.property("role") == "dangerBanner"
        assert not page.risk_banner.isVisibleTo(page)
        page._on_number_risk(RISK)
        assert page.risk_banner.isVisibleTo(page)
        assert page.risk_banner.text().startswith(i18n.KO.NUMBER_RISK_HEAD)
        assert "P8V798-04F4 (12장)" in page.risk_banner.text()
        page._engine_cfg = replace(config.DEFAULT_SIM_CONFIG, engine=EngineMode.COORDINATE)
        assert page.review_context().number_risk == RISK
        # 유사도 모드면 의미가 없다(직전 세션 잔재일 수 있다)
        page._engine_cfg = config.DEFAULT_SIM_CONFIG
        assert page.review_context().number_risk == ()
        page._set_number_risk(())                    # 새 실행은 지우고 시작한다
        assert not page.risk_banner.isVisibleTo(page)
    finally:
        page.deleteLater()


def test_review_page_shows_the_banner_only_in_coordinate_mode(qapp):
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    try:
        page.load_state([], coord_mode=True, number_risk=RISK)
        assert page.risk_banner.isVisibleTo(page)
        assert "P8V798-08D0 (3장)" in page.risk_banner.text()
        page.load_state([], coord_mode=False, number_risk=RISK)
        assert not page.risk_banner.isVisibleTo(page)
        page.load_state([], coord_mode=True)
        assert not page.risk_banner.isVisibleTo(page)
    finally:
        page.deleteLater()


def test_wafer_map_banner_switches_to_the_red_role(qapp):
    from aoi_verification.app.ui.widgets.wafer_map_dialog import WaferMapDialog
    dlg = WaferMapDialog()
    try:
        empty = wm.MapData(None, (), ())
        dlg._show_maps(("기준", wm.MapData(None, (), (), guessed_points=4)), None)
        assert dlg.warn_banner.property("role") == "dangerBanner"
        assert dlg.warn_banner.text().startswith(i18n.KO.WAFER_MAP_DANGER_HEAD)
        # 색만이 아니라 여백도 빨간 배너 것이어야 한다(테두리가 글자에 붙지 않게)
        danger_pad = dlg.warn_banner.contentsMargins().top()
        dlg._show_maps(("기준", wm.MapData(None, (), (Path("x.jpg"),))), ("검증", empty))
        assert dlg.warn_banner.property("role") == "hintWarn"
        assert dlg.warn_banner.text().startswith(i18n.KO.WAFER_MAP_WARN_HEAD)
        assert danger_pad > dlg.warn_banner.contentsMargins().top()
    finally:
        dlg.deleteLater()


def test_scheduler_counts_risk_on_the_original_coords(monkeypatch, tmp_path):
    """워커가 보정 사본이 아니라 원래 좌표로 세고, 경고가 있을 때만 알린다."""
    from types import SimpleNamespace
    from aoi_verification.app.workers import coord_matcher as cm
    from aoi_verification.app.coords.models import NUMBER_WARN_NO_TABLE, DefectCoord

    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    cache = {a: DefectCoord(col=1, row=2, x=10.0, y=20.0, source="camtek_ini",
                            number_warn=NUMBER_WARN_NO_TABLE),
             b: DefectCoord(col=1, row=2, x=10.0, y=20.0, source="camtek_ini")}
    monkeypatch.setattr(cm, "_resolve_batch", lambda paths, progress=None: dict(cache))
    item = lambda p: SimpleNamespace(path=p, slot="S1")       # noqa: E731
    sch = cm.CoordScheduler([("S1", [item(a)], [item(b)])])
    got = []
    sch.signals.number_risk.connect(got.append)
    sch._run()
    assert sch.number_risk == (("S1", 1),)
    assert got == [(("S1", 1),)]
