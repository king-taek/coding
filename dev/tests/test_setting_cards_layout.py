"""설정 화면의 두 설정 카드 배치 — 나란히(넓을 때) / 위아래(좁을 때) + 독립 확장.

후보 배치안 5종을 비교한 끝에 '나란히' 를 채택했다.  그대로 두면 두 가지가 깨진다:

1. **좁은 창에서 가로 스크롤** — 800×600 에서 나란히 두면 카드가 최소 폭을 확보하지
   못해 69px 넘쳤다(실측).  이 앱은 가로 넘침을 금지하므로 좁으면 세로로 쌓는다.
2. **한쪽이 커지면 다른 쪽도 커짐** — QHBoxLayout 은 정렬을 주지 않으면 두 아이템에
   *행 높이 전체*를 배정하고, NeonCard 는 sizePolicy 가 Preferred(=Grow) 라 늘어난다.
   그래서 구형 모드를 켜 '매칭 설정' 이 커지면 '실행 옵션' 카드까지 부풀었다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QScrollArea                    # noqa: E402

from aoi_verification.app import i18n                      # noqa: E402
from aoi_verification.app.ui.pages import setup_page as sp   # noqa: E402


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    """테마 적용된 앱 — conftest 가 `setStyleSheet` 을 세션당 1회만 부른다."""
    return styled_qapp


def _page(qapp, w: int, h: int = 900) -> sp.SetupPage:
    page = sp.SetupPage()
    page.resize(w, h)
    page.show()
    for _ in range(8):
        qapp.processEvents()
    return page


# ---------------------------------------------------------------------------
# 1) 넓으면 나란히, 좁으면 위아래 — 어느 쪽이든 가로 스크롤 없음
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("width", [800, 900, 1024, 1280, 1512])
def test_no_horizontal_scroll_at_any_width(qapp, width):
    page = _page(qapp, width)
    try:
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0, f"{width}px 가로 넘침"
    finally:
        page.close()


@pytest.mark.parametrize("width", [800, 900, 1024, 1150, 1280, 1512])
def test_no_horizontal_scroll_with_max_val_devices(qapp, width):
    """검증 장비를 최대로 늘려도 가로 스크롤이 없다 — 줄이 늘어도 두 열↔한 열이 뒤집히지 않는다."""
    page = _page(qapp, width)
    try:
        side = page._cards_side_by_side
        for _ in range(sp.SetupPage.MAX_VALS - 1):
            page._add_val_row(r"D:\a\very\long\folder\path\for\this\machine")
        for _ in range(8):
            qapp.processEvents()
        assert page._cards_side_by_side == side, "줄을 추가하자 배치가 뒤집혔다"
        scroll = page.findChild(QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0, f"{width}px 가로 넘침"
    finally:
        page.close()


def test_wide_window_puts_cards_side_by_side(qapp, monkeypatch):
    monkeypatch.setattr(sp.SetupPage, "_TWO_COLUMN", True)  # 두 열 코드는 남아 있다(기본은 한 열)
    page = _page(qapp, 1512)
    try:
        assert page._cards_side_by_side is True
        run, eng = page._setting_cards
        # 두 카드가 폭을 나눠 갖는다 — 각각 창의 절반 근처.
        assert abs(run.width() - eng.width()) <= 2
        assert run.width() < 1512 * 0.6
    finally:
        page.close()


def test_narrow_window_stacks_cards(qapp):
    """800px 에서는 나란히 두지 않는다 — 가로 넘침을 만들기 때문."""
    page = _page(qapp, 800, 600)
    try:
        assert page._cards_side_by_side is False
        run, eng = page._setting_cards
        assert run.width() > 800 * 0.7      # 세로로 쌓이면 각자 전체 폭을 쓴다
    finally:
        page.close()


def test_orientation_follows_resize(qapp, monkeypatch):
    monkeypatch.setattr(sp.SetupPage, "_TWO_COLUMN", True)  # 두 열 코드는 남아 있다(기본은 한 열)
    """폭이 임계치를 넘나들면 배치가 따라 바뀐다."""
    page = _page(qapp, 1600)
    try:
        assert page._cards_side_by_side is True
        page.resize(800, 600)
        for _ in range(8):
            qapp.processEvents()
        assert page._cards_side_by_side is False
        page.resize(1600, 900)
        for _ in range(8):
            qapp.processEvents()
        assert page._cards_side_by_side is True
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 2) 한 카드가 커져도 다른 카드는 자기 높이를 지킨다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("width", [1280, 1512])
def test_legacy_toggle_grows_only_engine_card(qapp, width):
    page = _page(qapp, width)
    try:
        run, eng = page._setting_cards
        run_before, eng_before = run.height(), eng.height()
        page.legacy_switch.set_on(True, emit=True)
        for _ in range(8):
            qapp.processEvents()
        assert run.height() == run_before, "실행 옵션 카드가 같이 커졌다"
        assert eng.height() >= eng_before, "매칭 설정 카드는 커져야 한다"
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 2-b) 두 열 — 검증 장비를 늘려도 왼쪽(기준 장비 · 실행 옵션)은 그대로(2026-10-27)
# ---------------------------------------------------------------------------
def test_adding_val_devices_does_not_move_left_column(qapp, monkeypatch):
    monkeypatch.setattr(sp.SetupPage, "_TWO_COLUMN", True)  # 두 열 코드는 남아 있다(기본은 한 열)
    page = _page(qapp, 1600)
    try:
        assert page._cards_side_by_side is True
        run, _eng = page._setting_cards
        ref = page.ref_group
        before = (ref.height(), run.y(), run.height())
        for _ in range(4):
            page._add_val_row()
        for _ in range(8):
            qapp.processEvents()
        assert page.val_group.height() > 300, "검증 카드가 늘어나야 이 시험이 의미가 있다"
        assert (ref.height(), run.y(), run.height()) == before, \
            "검증 장비를 늘리자 왼쪽 카드가 늘어나거나 밀렸다"
        assert run.y() < page.val_group.y() + page.val_group.height()
    finally:
        page.close()


def test_two_columns_pair_ref_with_run_and_val_with_engine(qapp, monkeypatch):
    monkeypatch.setattr(sp.SetupPage, "_TWO_COLUMN", True)  # 두 열 코드는 남아 있다(기본은 한 열)
    page = _page(qapp, 1600)
    try:
        run, eng = page._setting_cards
        assert abs(page.ref_group.x() - run.x()) <= 1
        assert abs(page.val_group.x() - eng.x()) <= 1
        assert page.val_group.x() > page.ref_group.x() + page.ref_group.width() - 2
    finally:
        page.close()


def test_narrow_window_keeps_folders_before_options(qapp):
    """한 열일 때 순서 — 기준 → 검증 → 실행 옵션 → 매칭 설정."""
    page = _page(qapp, 800, 600)
    try:
        run, eng = page._setting_cards
        ys = [w.mapTo(page, w.rect().topLeft()).y()
              for w in (page.ref_group, page.val_group, run, eng)]
        assert ys == sorted(ys)
    finally:
        page.close()


# ---------------------------------------------------------------------------
# 3) 배치안 전환기는 사라졌다 (최종안 확정)
# ---------------------------------------------------------------------------
def test_layout_variant_switcher_is_gone():
    for name in ("LAYOUT_VARIANTS", "LAYOUT_DEFAULT", "_RAIL_W", "_COLUMN_W",
                 "_discard_layout"):
        assert not hasattr(sp, name), f"배치안 잔재가 남았다: {name}"
    for name in ("_build_variant_bar", "_apply_layout_variant",
                 "_reflow_card_body", "_layout_b_two_columns",
                 "_layout_c_single_column", "_layout_d_axis_aligned",
                 "_layout_e_side_rail"):
        assert not hasattr(sp.SetupPage, name), f"배치안 잔재가 남았다: {name}"
    assert not hasattr(i18n.KO, "LAYOUT_VARIANT_LABEL")


# ---------------------------------------------------------------------------
# 4) 엔진 모드 문구 (항목 6)
# ---------------------------------------------------------------------------
def test_engine_mode_labels_dropped_internals():
    """'정밀 비교'·'CPU+GPU' 같은 내부 구현 용어를 사용자에게 보이지 않는다."""
    assert i18n.KO.ENGINE_MODE_BASIC == "기본 모드"
    assert "정밀 비교" not in i18n.KO.ENGINE_MODE_BASIC
    assert "CPU" not in i18n.KO.ENGINE_MODE_EFFICIENCY
    assert "사진이 많을 때" in i18n.KO.ENGINE_MODE_EFFICIENCY
    for text in (i18n.KO.LEGACY_MODE_HINT, i18n.KO.LEGACY_SWITCH_TITLE):
        assert "정밀 비교" not in text and "CPU+GPU" not in text


# ---------------------------------------------------------------------------
# 도구 레일(2026-10-27) — 왼쪽 세로 줄이 폭을 먹으므로 두 열은 더 넓어야 선다
# ---------------------------------------------------------------------------
def test_rail_takes_width_so_1280_stacks_cards_without_overflow(qapp):
    page = _page(qapp, 1280)
    try:
        assert page._wide is True and not page.is_rail_folded()
        assert page._cards_side_by_side is False         # 레일 + 두 열은 1280 에 안 든다
        assert page._scroll.horizontalScrollBar().maximum() == 0
        rail = page._tool_rail
        from PyQt6.QtCore import QPoint
        origin = QPoint(0, 0)
        assert rail.mapTo(page, origin).x() < page.ref_group.mapTo(page, origin).x()   # 레일이 카드 왼쪽
    finally:
        page.close()


def test_rail_folds_to_icons_on_narrow_window(qapp):
    """좁은 창에서는 메뉴가 아이콘만 남는다(예전엔 카드 위 가로 줄로 누웠다) — 폭을 카드에 준다."""
    page = _page(qapp, 800, 600)
    try:
        assert page._wide is False and page.is_rail_folded()
        from PyQt6.QtCore import QPoint
        origin = QPoint(0, 0)
        assert (page._tool_rail.mapTo(page, origin).x()
                < page.ref_group.mapTo(page, origin).x())   # 여전히 왼쪽 세로 줄
        assert page._tool_rail.width() <= 80
        assert page._scroll.horizontalScrollBar().maximum() == 0
    finally:
        page.close()
