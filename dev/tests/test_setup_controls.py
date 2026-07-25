"""셋업 화면 컨트롤 — 빌더 seam 계약 · 액션바 인덱스 · (이후 단계에서 모드 스위치 추가)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication                        # noqa: E402

from aoi_verification.app.ui.pages import setup_page as sp      # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# 배치안(서브클래스)이 갈아끼워도 공유 로직이 동작하려면 이 속성들이 있어야 한다.
CONTRACT_ATTRS = (
    "ref_path_edit", "val_path_edit", "ref_machine_edit", "val_machine_edit",
    "coord_tol_spin", "slider", "threshold_label",
    "update_btn", "start_btn", "_action_bar",
    "_tol_row", "_threshold_row",
    "dev_bench_btn", "dev_label_btn",
)


def test_builder_seam_exists():
    """본문이 작은 빌더로 쪼개져 있어야 배치안을 값싸게 만들 수 있다."""
    src = inspect.getsource(sp.SetupPage)
    for name in ("_build_body", "_build_title", "_build_view_options",
                 "_build_howto", "_build_automation_card", "_build_device_row",
                 "_build_scope_row", "_build_engine_card", "_build_action_bar",
                 "_build_credit"):
        assert f"def {name}" in src, f"{name} 빌더 누락"


def test_page_satisfies_attr_contract(qapp):
    page = sp.SetupPage()
    try:
        missing = [a for a in CONTRACT_ATTRS if not hasattr(page, a)]
        assert not missing, f"속성 계약 위반: {missing}"
    finally:
        page.deleteLater()


def test_action_bar_index_contract(qapp, monkeypatch):
    """[0]=update_btn, 개발자 모드에서 [1..2]=개발자 버튼.

    ``_refresh_dev_buttons`` 가 insertWidget(1/2, …) 를 가정하므로, 새 위젯은 반드시
    stretch 뒤에 붙어야 한다.  이 계약이 깨지면 개발자 버튼이 엉뚱한 자리에 들어간다."""
    page = sp.SetupPage()
    try:
        bar = page._action_bar
        assert bar.itemAt(0).widget() is page.update_btn
        # 개발자 모드 on → 인덱스 1·2 에 개발자 버튼이 삽입된다.
        monkeypatch.setattr(page, "_dev_mode_enabled", lambda: True)
        page._refresh_dev_buttons()
        assert page.dev_bench_btn is not None and page.dev_label_btn is not None
        assert bar.itemAt(0).widget() is page.update_btn
        assert bar.itemAt(1).widget() is page.dev_bench_btn
        assert bar.itemAt(2).widget() is page.dev_label_btn
        # 마지막 위젯은 여전히 주 액션이어야 한다(stretch 뒤).
        last = bar.itemAt(bar.count() - 1).widget()
        assert last is page.start_btn
    finally:
        page.deleteLater()


def test_help_tooltip_comes_from_i18n():
    """사용자 노출 문자열 하드코딩 금지(CLAUDE.md) — '?' 툴팁도 i18n 에서."""
    src = inspect.getsource(sp)
    assert "설명 보기/숨기기" not in src
    assert "HELP_TOGGLE_TOOLTIP" in src


def test_no_horizontal_scroll_at_800x600(qapp):
    from PyQt6.QtWidgets import QScrollArea
    page = sp.SetupPage()
    try:
        page.resize(800, 600)
        page.show()
        for _ in range(8):
            qapp.processEvents()
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.horizontalScrollBar().maximum() == 0
    finally:
        page.deleteLater()
