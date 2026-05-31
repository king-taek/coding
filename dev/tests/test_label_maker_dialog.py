"""정답 라벨 만들기 다이얼로그 + 개발자 모드 토글 — 구성 스모크 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from aoi_verification.app.models.slot import ImageItem


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _tasks():
    refs = [ImageItem(slot="S1", path=Path("/r/S1/a.jpg"), side="ref")]
    vals = [ImageItem(slot="S1", path=Path("/v/S1/x.jpg"), side="val"),
            ImageItem(slot="S1", path=Path("/v/S1/y.jpg"), side="val")]
    return [("S1", refs, vals)]


def test_label_dialog_builds_and_labels_via_model(app):
    from aoi_verification.app.ui.widgets.label_maker_dialog import LabelMakerDialog
    from aoi_verification.app.dev import labels as lab
    dlg = LabelMakerDialog()
    try:
        # 모델을 주입해 라벨링 로직(저장 경로 없이) 동작을 확인.
        dlg._model = lab.LabelMakerModel(_tasks())
        dlg._set_labeling_enabled(True)
        dlg._refresh()
        assert len(dlg._cand_buttons) == 2          # 후보 2개 버튼
        dlg._on_toggle("/v/S1/x.jpg")
        assert dlg._model.selected() == {"/v/S1/x.jpg"}
        dlg._on_none()
        assert dlg._model.selected() == set()
        assert dlg._model.to_labels() == {"S1": {"/r/S1/a.jpg": []}}
    finally:
        dlg._model = None                            # closeEvent 가드 우회
        dlg.deleteLater()


def test_setup_page_dev_toggle(app, monkeypatch):
    """Ctrl+Shift+D 토글 시 개발자 버튼이 즉시 나타났다 사라지는지."""
    from types import SimpleNamespace

    monkeypatch.delenv("AOI_DEV_MODE", raising=False)
    from aoi_verification.app.ui.pages import setup_page as sp
    from aoi_verification.app.ui.widgets import dev_benchmark_dialog as dbd

    # 페이지는 실제 prefs 로 정상 빌드(개발자 모드 off) → 버튼 없음.
    page = sp.SetupPage()
    try:
        assert page.dev_bench_btn is None
        # 빌드 이후에만 prefs/게이트를 메모리 플래그로 가로챈다(_build 영향 없음).
        state = {"dev": False}
        monkeypatch.setattr(sp._prefs, "load",
                            lambda: SimpleNamespace(dev_mode=state["dev"]))
        monkeypatch.setattr(sp._prefs, "patch",
                            lambda **kw: state.update(dev=kw.get("dev_mode", state["dev"])))
        monkeypatch.setattr(dbd, "dev_mode_enabled", lambda: state["dev"])
        monkeypatch.setattr(sp.QMessageBox, "information",
                            staticmethod(lambda *a, **k: None))

        page._toggle_dev_mode()                      # 켜기
        assert state["dev"] is True
        assert page.dev_bench_btn is not None and page.dev_label_btn is not None
        page._toggle_dev_mode()                      # 끄기
        assert state["dev"] is False
        assert page.dev_bench_btn is None and page.dev_label_btn is None
    finally:
        page.deleteLater()
