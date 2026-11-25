"""버그 회귀 가드 — 시작 부수효과 차단 · 단계 제목 · 긴 이름 말줄임.

세 가지 모두 **실제로 사용자/개발자를 물었던** 결함이라 각각 왜 그런지 함께 적는다.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                       # noqa: E402
from PyQt6.QtWidgets import QApplication                          # noqa: E402

from aoi_verification.app import config, i18n                     # noqa: E402
from aoi_verification.app.ui import main_window as mw             # noqa: E402
from aoi_verification.app.ui.pages import match_page as mp        # noqa: E402
from aoi_verification.app.ui.pages import match_review_page as mrp  # noqa: E402
from aoi_verification.app.ui.pages import select_page as sp       # noqa: E402
from aoi_verification.app.models.result import MatchResult        # noqa: E402
from aoi_verification.app.utils.prefs import EngineMode           # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# 1) 시작 부수효과 — conftest 의 autouse 가드
# ---------------------------------------------------------------------------
def test_startup_side_effects_are_blocked_for_every_test(qapp, monkeypatch):
    """네트워크 업데이트 확인·GPU 워밍업·OpenVINO 안내가 테스트에서 돌지 않는다.

    ★ 이것이 없으면 이벤트 루프를 400ms 넘게 도는 테스트가 **영구히 멈춘다** —
    업데이트 응답이 `sheets.ask` 중첩 루프를 열고 아무도 닫지 않는다(실측 375ms).
    """
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    win = mw.MainWindow()
    try:
        # conftest 가 바꿔치기한 메서드는 부르면 기록만 남긴다.
        win._check_for_update_async()
        win._warmup_accel_async()
        win._maybe_offer_openvino()
        assert win._suppressed_startup_calls == [
            "_check_for_update_async", "_warmup_accel_async",
            "_maybe_offer_openvino",
        ]
    finally:
        win.close()


# ---------------------------------------------------------------------------
# 2) 단계 제목 — 화면에 한 번, 그리고 모드에 맞게
# ---------------------------------------------------------------------------
def _visible_texts(page) -> list[str]:
    from PyQt6.QtWidgets import QLabel
    return [lb.text() for lb in page.findChildren(QLabel)]


def test_stage_title_appears_exactly_once(qapp):
    """같은 문장을 26px 와 15px 로 두 번 찍던 중복을 막는다.

    ★ 양방향 가드다 — '제목을 통째로 지운다' 로도 통과하지 않도록 **정확히 1회**를 센다.
    """
    page = sp.SelectPage()
    try:
        assert _visible_texts(page).count(i18n.KO.STAGE1_TITLE) == 1
    finally:
        page.deleteLater()


def test_stage2_title_follows_the_engine_mode(qapp):
    """좌표 모드에서 큰 제목이 '유사도 기반' 으로 굳어 있던 모순을 막는다.

    가장 눈에 띄는 26px 글자가 실제 동작과 달랐다 — 화면이 스스로를 부정했다.
    """
    coord = config.SimilarityConfig(engine=EngineMode.COORDINATE)
    legacy = config.SimilarityConfig(engine=EngineMode.BASIC)

    assert mp._stage2_title(coord) == i18n.KO.STAGE2_TITLE_COORD
    assert mp._stage2_title(legacy) == i18n.KO.STAGE2_TITLE

    # ★ 손으로 setText 하면 **정작 고친 배선을 안 태운다**(동어반복).
    #   실제 진입점인 load_state 를 부른다 — 이것이 제목을 갱신하지 않으면 실패한다.
    page = mp.MatchPage()
    page._start_precompute = lambda: None      # 무거운 사전 계산은 건너뛴다
    try:
        page.load_state([], {}, 0.5, engine_cfg=legacy)
        assert page.title.text() == i18n.KO.STAGE2_TITLE
        assert _visible_texts(page).count(i18n.KO.STAGE2_TITLE) == 1

        page.load_state([], {}, 0.5, engine_cfg=coord)
        assert page.title.text() == i18n.KO.STAGE2_TITLE_COORD, \
            "좌표 모드로 다시 부르면 큰 표제도 따라와야 한다"
        # 그리고 그 문장은 화면에 **한 번만** 있다.
        assert _visible_texts(page).count(i18n.KO.STAGE2_TITLE_COORD) == 1
        assert _visible_texts(page).count(i18n.KO.STAGE2_TITLE) == 0, \
            "옛 제목이 남아 있으면 안 된다"
    finally:
        page.deleteLater()


def test_pages_no_longer_carry_a_duplicate_phase_label(qapp):
    """부제 라벨을 되살리지 않는다 — 그것이 중복의 기계였다."""
    for page_cls in (sp.SelectPage, mp.MatchPage):
        page = page_cls()
        try:
            assert not hasattr(page, "phase_label")
        finally:
            page.deleteLater()


# ---------------------------------------------------------------------------
# 3) 긴 슬롯 이름 — 잘리되 읽을 수 있어야 한다
# ---------------------------------------------------------------------------
def _row(slot: str):
    m = MatchResult(slot=slot, ref_path=__import__("pathlib").Path("/tmp/r.jpg"),
                    val_path=__import__("pathlib").Path("/tmp/v.jpg"), score=0.9)
    return mrp._MatchRow(m, runners_up=[], thumb_px=118)


def test_long_slot_name_is_shown_in_two_lines_with_a_full_tooltip(qapp):
    """96px 열에서 웨이퍼 ID 가 **잘리던** 것을 막는다.

    ★ 가운데 생략은 앞뒤가 같은 두 웨이퍼를 똑같이 보이게 했다(W7548304XYG4 /
      W7548306XYG4) — V26 에서 전체를 두 줄(앞 8자 / 나머지)로 바꿨다.
    """
    slot = "W7548304XYG4"
    row = _row(slot)
    try:
        assert row._slot_label.text() == "W7548304\nXYG4"
        assert row._slot_label.text().replace("\n", "") == slot
        # 전체 이름은 툴팁으로도 읽을 수 있다.
        assert row._slot_label.toolTip() == slot
    finally:
        row.deleteLater()


def test_short_slot_name_is_not_touched(qapp):
    """짧은 이름까지 건드리면 안 된다 — 생략은 필요할 때만."""
    row = _row("A01")
    try:
        assert row._slot_label.text() == "A01"
    finally:
        row.deleteLater()


def test_slot_column_width_is_a_shared_constant(qapp):
    """행·헤더·예약폭이 **같은 상수**를 본다 — 따로 적으면 헤더가 한 칸 밀린다."""
    row = _row("A01")
    try:
        assert row._slot_label.parentWidget().width() == mrp._SLOT_W
        # 예약 폭에도 그 값이 들어간다(리터럴 96 을 세 곳에 흩뿌리지 않는다).
        assert row._reserved_fixed_px() >= mrp._SLOT_W + mrp._ARROW_W
    finally:
        row.deleteLater()


