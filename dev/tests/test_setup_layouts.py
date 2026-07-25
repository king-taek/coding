"""셋업 배치안 3종 — 배치만 다르고 동작은 같다(비교 가능성 보장).

※ 배치안이 확정되면 이 테스트와 setup_layouts.py, 상단 스위처를 함께 제거한다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QScrollArea          # noqa: E402

from aoi_verification.app.ui.pages import setup_layouts as sl   # noqa: E402
from aoi_verification.app.utils import prefs                    # noqa: E402
from aoi_verification.app.utils.prefs import EngineMode          # noqa: E402

# 배치안이 갈아끼워져도 공유 로직이 돌아가려면 이 속성들이 있어야 한다.
CONTRACT_ATTRS = (
    "ref_path_edit", "val_path_edit", "ref_machine_edit", "val_machine_edit",
    "auto_group", "scope_group", "legacy_switch", "legacy_group",
    "coord_tol_spin", "slider", "threshold_label",
    "update_btn", "start_btn", "_action_bar", "_tol_row", "_threshold_row",
    "dev_bench_btn", "dev_label_btn", "layout_group",
)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_three_layouts_registered():
    assert set(sl.LAYOUTS) == {"a", "b", "c"}
    assert set(sl.layout_keys()) == {"a", "b", "c"}
    assert all(k in sl.LAYOUT_LABELS for k in sl.LAYOUTS)


@pytest.mark.parametrize("key", ["a", "b", "c"])
def test_layout_satisfies_attr_contract(qapp, key):
    page = sl.LAYOUTS[key]()
    try:
        missing = [a for a in CONTRACT_ATTRS if not hasattr(page, a)]
        assert not missing, f"{key}: 속성 계약 위반 {missing}"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("key", ["a", "b", "c"])
def test_engine_logic_identical_across_layouts(qapp, key):
    """세 배치 모두 스위치가 엔진을 똑같이 구동한다(비교가 배치 판단이 되게)."""
    page = sl.LAYOUTS[key]()
    try:
        assert page._current_engine_mode() == EngineMode.COORDINATE
        page.legacy_switch.set_on(True, emit=True)
        assert page._current_engine_mode() == EngineMode.BASIC
        page.legacy_group.set_current_key(EngineMode.EFFICIENCY, emit=True)
        assert page._current_engine_mode() == EngineMode.EFFICIENCY
        page.legacy_switch.set_on(False, emit=True)
        assert page._current_engine_mode() == EngineMode.COORDINATE
    finally:
        page.deleteLater()


@pytest.mark.parametrize("key", ["a", "b", "c"])
@pytest.mark.parametrize("size", [(1512, 982), (800, 600)])
def test_no_horizontal_scroll(qapp, key, size):
    """800×600 을 포함해 가로 스크롤이 생기지 않아야 한다."""
    page = sl.LAYOUTS[key]()
    try:
        page.resize(*size)
        page.show()
        for _ in range(10):
            qapp.processEvents()
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0, \
            f"{key}@{size}: 가로 스크롤 발생"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("key", ["a", "b", "c"])
def test_switcher_reflects_the_page_not_prefs(qapp, key):
    """스위처는 prefs 가 아니라 **자기 페이지**를 표시한다(어긋남 방지)."""
    page = sl.LAYOUTS[key]()
    try:
        assert page.LAYOUT_KEY == key
        assert page.layout_group.current_key() == key
        assert page.layout_group._last_cols == len(sl.layout_keys())  # 한 줄 고정
    finally:
        page.deleteLater()


def test_current_layout_key_falls_back(isolated_cache):
    prefs.save(prefs.UiPrefs(setup_layout="zzz"))
    assert sl.current_layout_key() == sl.DEFAULT_LAYOUT
    prefs.save(prefs.UiPrefs(setup_layout="c"))
    assert sl.current_layout_key() == "c"


def test_make_setup_page_uses_saved_layout(qapp, isolated_cache):
    prefs.save(prefs.UiPrefs(setup_layout="b"))
    page = sl.make_setup_page()
    try:
        assert page.LAYOUT_KEY == "b"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("key", ["a", "b", "c"])
def test_action_bar_pinned_outside_scroll(qapp, key):
    """★ 세 배치 **모두** 액션바를 스크롤 밖에 고정한다.

    이전엔 A/C 가 스크롤 안에 뒀는데 800×600 에서 [검증 시작]이 화면 밖으로 밀려났다
    (실측).  '주요 액션을 찾으려면 스크롤해야 한다'는 어느 배치에서도 결함이므로
    배치안의 차이로 남기지 않는다."""
    page = sl.LAYOUTS[key]()
    try:
        assert page._pinned_action_bar() is True
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert not scroll.isAncestorOf(page.start_btn), \
            f"{key}: 시작 버튼이 스크롤 안에 있다"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("key", ["a", "b", "c"])
@pytest.mark.parametrize("size", [(1512, 982), (800, 600)])
def test_start_button_visible_without_scrolling(qapp, key, size):
    """어떤 크기에서도 [검증 시작]이 페이지 안에 실제로 보여야 한다."""
    page = sl.LAYOUTS[key]()
    try:
        page.resize(*size)
        page.show()
        for _ in range(12):
            qapp.processEvents()
        top_left = page.start_btn.mapTo(page, page.start_btn.rect().topLeft())
        bottom = top_left.y() + page.start_btn.height()
        assert page.start_btn.isVisible(), f"{key}@{size}: 시작 버튼 비가시"
        assert bottom <= page.height(), \
            f"{key}@{size}: 시작 버튼 하단 {bottom} > 페이지 높이 {page.height()}"
    finally:
        page.deleteLater()


@pytest.mark.parametrize("key", ["a", "c"])
def test_path_fields_stay_readable_at_800(qapp, key):
    """800×600 에서 경로 입력란이 폴더 이름을 읽을 수 없을 만큼 짜부라지지 않아야 한다.

    이전엔 두 장비 카드를 늘 나란히 둬서 입력란이 ~85px 로 줄었다.  좁으면 세로로 쌓는다."""
    page = sl.LAYOUTS[key]()
    try:
        page.resize(800, 600)
        page.show()
        for _ in range(12):
            qapp.processEvents()
        assert page.ref_path_edit.width() >= 240, \
            f"{key}: 경로 입력란 {page.ref_path_edit.width()}px"
    finally:
        page.deleteLater()


def test_c_summary_is_not_the_faintest_text(qapp):
    """C안 요약은 **안전 장치**다 — 가장 작고 옅은 글자여선 안 된다."""
    page = sl.LAYOUTS["c"]()
    try:
        assert page._summary_label.property("role") == "summaryValue"
        assert page._summary_label.property("role") != "muted"
    finally:
        page.deleteLater()


def test_c_summary_tracks_settings(qapp):
    """C안 요약 줄이 현재 설정을 따라간다."""
    page = sl.LAYOUTS["c"]()
    try:
        assert "좌표" in page._summary_label.text()
        page.legacy_switch.set_on(True, emit=True)
        assert "구형" in page._summary_label.text()
    finally:
        page.deleteLater()
