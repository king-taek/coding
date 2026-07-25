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


def test_b_pins_action_bar_outside_scroll(qapp):
    """B안은 액션바를 스크롤 밖에 고정한다 — 긴 창에서도 시작 버튼이 손에 닿게."""
    a = sl.LAYOUTS["a"]()
    b = sl.LAYOUTS["b"]()
    try:
        assert a._pinned_action_bar() is False
        assert b._pinned_action_bar() is True
        scroll = b.findChild(QScrollArea)
        # 고정된 액션바는 스크롤 위젯의 자손이 아니어야 한다.
        assert not b.start_btn.isAncestorOf(scroll)
        assert scroll is not None and not scroll.isAncestorOf(b.start_btn)
    finally:
        a.deleteLater()
        b.deleteLater()


def test_c_summary_tracks_settings(qapp):
    """C안 요약 줄이 현재 설정을 따라간다."""
    page = sl.LAYOUTS["c"]()
    try:
        assert "좌표" in page._summary_label.text()
        page.legacy_switch.set_on(True, emit=True)
        assert "구형" in page._summary_label.text()
    finally:
        page.deleteLater()
