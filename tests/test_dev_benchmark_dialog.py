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
        # 개별 체크박스 = 메인 옵션(앵커 + TOP5) + 최종 프리셋의 고전 워밍업.
        assert set(dlg._recipe_checks.keys()) == set(rx.MAIN_KEYS) | set(rx.FINAL_KEYS)
        # 기본 선택은 앵커 + TOP5(=MAIN).
        assert set(dlg._selected_keys()) == set(rx.MAIN_KEYS)
        assert dlg.table.columnCount() == 8
        assert dlg.self_test.isChecked() is True
    finally:
        dlg.deleteLater()


def test_dialog_presets_switch_selection(app):
    from aoi_verification.app.ui.widgets.dev_benchmark_dialog import \
        DevBenchmarkDialog
    dlg = DevBenchmarkDialog(default_ref="/tmp/x")
    try:
        dlg._apply_preset("top5")             # 앵커 + TOP5
        assert set(dlg._selected_keys()) == set(rx.MAIN_KEYS)
        dlg._apply_preset("final")            # 고전 2회(워밍업→정식) + 현행 + TOP5
        assert set(dlg._selected_keys()) == set(rx.FINAL_KEYS)
        # 그룹 토글은 옵션에서 사라졌다(실험 종료).
        assert dlg._group_checks == {}
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
