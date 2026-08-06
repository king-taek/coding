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


def test_long_slot_name_is_elided_in_the_middle_with_a_full_tooltip(qapp):
    """96px 열에서 웨이퍼 ID 가 **말줄임 없이 잘리던** 것을 막는다.

    ★ 가운데 생략이어야 한다 — 실제 ID 는 앞이 공통이라(W75483·04XYG4 / W75483·03XYC2)
    뒤를 자르면 **서로 다른 웨이퍼가 똑같이 렌더된다.**
    """
    slot = "W7548304XYG4"
    row = _row(slot)
    try:
        shown = row._slot_label.text()
        assert shown != slot, "이 폭에서는 줄어들어야 한다(테스트 전제)"
        assert "…" in shown, "말줄임 표시가 있어야 잘렸다는 것을 안다"
        # 가운데 생략 — 앞뒤가 모두 남는다.
        assert shown.startswith(slot[:3]) and shown.endswith(slot[-3:])
        # 전체 이름은 툴팁으로 반드시 읽을 수 있다.
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


def test_lot_counts_label_fills_the_real_width_and_keeps_the_full_text(qapp):
    """1단계 '슬롯별 장수' — 잘리되 **가용 폭을 실제로 채워야** 한다.

    ★ 예전 테스트는 `setFixedWidth(200)` 으로 라벨에 폭을 미리 심어 줬다.  그래서 실제
    경로의 결함(레이아웃 전 100px 로 잘라 놓고 다시 안 재는 것)을 **잡지 못했고, 오히려
    더 심하게 잘릴수록 확실히 통과**했다.  이제 앱과 같은 순서로 태운다:
    넓은 창에 넣고 → load_state → 화면에 올리고 → 폭을 실제로 채웠는지 잰다.
    """
    from pathlib import Path
    from PyQt6.QtGui import QFontMetrics
    from PyQt6.QtWidgets import QMainWindow, QStackedWidget

    win = QMainWindow()
    stack = QStackedWidget(win)
    win.setCentralWidget(stack)
    win.resize(1600, 1000)
    page = sp.SelectPage()
    stack.addWidget(page)
    win.show()
    try:
        slots = [f"W75483{i:02d}XYG4" for i in range(12)]
        queue = [sp.ImageItem(path=Path(f"/tmp/{slot}_{k}.jpg"), slot=slot, side="ref")
                 for slot in slots for k in range(3)]
        page.load_state(queue=queue)          # 앱과 같은 순서 — 표시보다 먼저
        stack.setCurrentWidget(page)
        for _ in range(10):
            qapp.processEvents()

        lb = page.lot_counts_label
        full = lb.toolTip()
        assert full.startswith(i18n.KO.LOT_COUNTS_PREFIX)
        assert all(slot in full for slot in slots), "툴팁에는 전부 있어야 한다"

        shown = lb.text()
        assert "…" in shown and len(shown) < len(full), "이 폭에서는 줄어야 한다"
        # ★ 핵심 단언 — 잘렸다는 것만으로는 부족하다.  **가용 폭을 채워야** 한다.
        #   레이아웃 전 100px 로 잘라 두면 여기서 걸린다.
        avail = lb.contentsRect().width()
        used = QFontMetrics(lb.font()).horizontalAdvance(shown)
        assert avail > 400, f"창이 넓으므로 라벨도 넓어야 한다 (실제 {avail}px)"
        assert used >= avail * 0.8, \
            f"가용 {avail}px 중 {used}px 만 씀 — 레이아웃 전 폭으로 자른 것이다"
    finally:
        win.close()
        page.deleteLater()


def test_lot_counts_re_elides_when_the_window_grows(qapp):
    """창을 키우면 다시 계산해야 한다 — 한 번 자르고 끝나면 영원히 좁은 채 남는다."""
    from pathlib import Path
    from PyQt6.QtGui import QFontMetrics
    from PyQt6.QtWidgets import QMainWindow, QStackedWidget

    win = QMainWindow()
    stack = QStackedWidget(win)
    win.setCentralWidget(stack)
    win.resize(820, 620)
    page = sp.SelectPage()
    stack.addWidget(page)
    win.show()
    try:
        slots = [f"W75483{i:02d}XYG4" for i in range(12)]
        page.load_state(queue=[
            sp.ImageItem(path=Path(f"/tmp/{s_}_{k}.jpg"), slot=s_, side="ref")
            for s_ in slots for k in range(3)])
        stack.setCurrentWidget(page)
        for _ in range(10):
            qapp.processEvents()
        narrow = QFontMetrics(page.lot_counts_label.font()).horizontalAdvance(
            page.lot_counts_label.text())

        win.resize(1700, 1000)
        for _ in range(10):
            qapp.processEvents()
        wide = QFontMetrics(page.lot_counts_label.font()).horizontalAdvance(
            page.lot_counts_label.text())
        assert wide > narrow, f"넓어졌는데 글자가 그대로다 ({narrow} → {wide})"
    finally:
        win.close()
        page.deleteLater()
