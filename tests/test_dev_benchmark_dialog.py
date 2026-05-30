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


def test_dialog_builds_with_all_recipes(app):
    from aoi_verification.app.ui.widgets.dev_benchmark_dialog import \
        DevBenchmarkDialog
    dlg = DevBenchmarkDialog(default_ref="/tmp/x")
    try:
        assert set(dlg._recipe_checks.keys()) == set(rx.all_keys())
        assert dlg._selected_keys() == rx.all_keys()
        assert dlg.table.columnCount() == 8
        assert dlg.self_test.isChecked() is True
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
