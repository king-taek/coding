"""개발자 벤치마크 다이얼로그 — 구성/게이트 스모크 테스트."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from aoi_verification.app.dev import recipes as rx


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_dev_mode_gate_env(monkeypatch):
    from aoi_verification.app.ui.widgets import dev_benchmark_dialog as d
    monkeypatch.setenv("AOI_DEV_MODE", "1")
    assert d.dev_mode_enabled() is True
    monkeypatch.setenv("AOI_DEV_MODE", "0")
    assert d.dev_mode_enabled() is False
    monkeypatch.delenv("AOI_DEV_MODE", raising=False)
    assert d.dev_mode_enabled() is False


def test_dialog_defaults_to_quick_preset(app):
    from aoi_verification.app.ui.widgets.dev_benchmark_dialog import \
        DevBenchmarkDialog
    dlg = DevBenchmarkDialog(default_ref="/tmp/x")
    try:
        # 개별 체크박스 = core 13 + 빠른 프리셋의 fast-rerank 추가분.
        core = set(rx.all_keys())
        quick_extra = {k for k in rx.QUICK_KEYS if k not in core}
        assert set(dlg._recipe_checks.keys()) == core | quick_extra
        # 기본 선택은 '빠른'(핵심 소수) — 전체가 아니다.
        assert dlg._selected_keys() == list(rx.QUICK_KEYS)
        assert dlg.table.columnCount() == 8
        assert dlg.self_test.isChecked() is True
    finally:
        dlg.deleteLater()


def test_dialog_presets_switch_selection(app):
    from aoi_verification.app.ui.widgets.dev_benchmark_dialog import \
        DevBenchmarkDialog
    dlg = DevBenchmarkDialog(default_ref="/tmp/x")
    try:
        dlg._apply_preset("core")
        assert set(dlg._selected_keys()) == set(rx.all_keys())
        dlg._apply_preset("all")
        sel = set(dlg._selected_keys())
        # 전체는 확장 그룹(예: npu-sweep)까지 포함한다.
        assert {r.key for r in rx.group("npu-sweep")} <= sel
        dlg._apply_preset("quick")
        assert dlg._selected_keys() == list(rx.QUICK_KEYS)
    finally:
        dlg.deleteLater()


def test_setup_page_shows_dev_button_when_enabled(app, monkeypatch):
    monkeypatch.setenv("AOI_DEV_MODE", "1")
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    page = SetupPage()
    try:
        assert hasattr(page, "dev_bench_btn")
    finally:
        page.deleteLater()
