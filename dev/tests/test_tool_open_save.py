"""도구 화면 [열기] · [저장] 한 꼴 — 현택님 2026-11-24 결정(B1 안 B · B2 안 A).

- 빈 화면: 화면 가운데 큰 파랑 [사진 선택]/[폴더 선택] 하나(설명 문장 없음, 짧은 끌어놓기 한 줄만).
  불러온 뒤: 같은 이름의 회색 버튼이 위 왼쪽.
- 저장: [엑셀로 저장] / [그림으로 저장] / [HTML로 저장] — 모두 파랑 · 오른쪽 위.
  저장한 뒤 '저장 완료 HH:MM' + [폴더 열기] 한 줄.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")
np = pytest.importorskip("numpy")

from PyQt6.QtWidgets import QApplication                  # noqa: E402

from aoi_verification.app import i18n                     # noqa: E402


def _pump():
    for _ in range(5):
        QApplication.processEvents()


def _show(dlg):
    dlg.resize(1280, 800)
    dlg.show()
    _pump()
    return dlg


def _check_empty(dlg, text: str) -> None:
    eo = dlg._empty_open
    assert eo.isVisibleTo(dlg) and eo.geometry() == dlg.rect()
    assert eo.btn.text() == text and eo.btn.property("role") == "primary"
    c = eo.btn.mapTo(dlg, eo.btn.rect().center())
    assert abs(c.x() - dlg.width() / 2) <= 2 and abs(c.y() - dlg.height() / 2) <= 40
    assert eo.btn.width() >= 240 and eo.btn.height() >= 56
    assert eo.hint.text() == i18n.KO.EMPTY_DROP_HINT
    if not _no_top_pick(dlg):
        pick = _pick(dlg)
        assert pick.text() == text and pick.property("role") == "ghost"


def _no_top_pick(dlg) -> bool:
    """Defect 정보 는 위 왼쪽 [사진 선택] 을 뺐다(사용자 결정 2026-11-25) — 빈 화면 큰 버튼만."""
    from aoi_verification.app.ui.widgets.image_info_dialog import ImageInfoDialog
    return isinstance(dlg, ImageInfoDialog)


def _pick(dlg):
    """위 왼쪽 열기 버튼 — GL 만 이름이 ``open_btn``."""
    return getattr(dlg, "pick_btn", None) or getattr(dlg, "open_btn", None)


def _check_loaded(dlg) -> None:
    assert not dlg._empty_open.isVisibleTo(dlg)
    pick = _pick(dlg)
    if pick is None:
        return
    tl = pick.mapTo(dlg, pick.rect().topLeft())
    assert pick.isVisibleTo(dlg) and tl.y() < 80 and tl.x() < dlg.width() // 4, tl


def _check_save(dlg, btn, text: str) -> None:
    assert btn.text() == text and btn.property("role") == "primary"
    tl = btn.mapTo(dlg, btn.rect().topLeft())
    assert tl.y() < 80 and tl.x() > dlg.width() // 2, tl


# ── B1 빈 화면 · 위 왼쪽 ─────────────────────────────────────────────────────
def test_button_names_follow_one_pattern():
    K = i18n.KO
    assert K.IMAGE_INFO_PICK == K.GL_OPEN == "사진 선택"
    assert K.WAFER_MAP_PICK_FOLDER == K.OVERKILL_ADD_FOLDER == K.ZO_ADD_FOLDER == "폴더 선택"
    assert not hasattr(K, "IMAGE_INFO_PICK_ANOTHER") and not hasattr(K, "WAFER_MAP_PICK_FOLDER_ANOTHER")


def test_defect_info_empty_then_no_top_left(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.image_info_dialog import ImageInfoDialog
    dlg = _show(ImageInfoDialog())
    try:
        _check_empty(dlg, "사진 선택")
        img = tmp_path / "x.jpg"
        img.write_bytes(b"x")
        dlg.show_image(img)
        _pump()
        assert not dlg._empty_open.isVisibleTo(dlg)
        assert not hasattr(dlg, "pick_btn")                 # 위 왼쪽 [사진 선택] 없음(2026-11-25)
    finally:
        dlg.deleteLater()


def test_gl_empty_then_top_left(styled_qapp):
    from aoi_verification.app.gl import loader
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    dlg = _show(GlViewerDialog(None))
    try:
        _check_empty(dlg, "사진 선택")
        rng = np.random.default_rng(1)
        dlg.show_view(loader.ViewLoad(loader.GlImage(rng.integers(0, 255, (40, 60)).astype(np.uint8),
                                                     8, False, "y.png", [])))
        _pump()
        _check_loaded(dlg)
    finally:
        dlg.close()


def test_defect_map_empty_then_top_left(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import wafer_map_dialog as W
    monkeypatch.setattr(W.WaferMapDialog, "_start_build", lambda self, job, done, msg: None)
    dlg = _show(W.WaferMapDialog())
    try:
        _check_empty(dlg, "폴더 선택")
        dlg.show_folder(tmp_path)
        _pump()
        _check_loaded(dlg)
        assert dlg.pick_btn.text() == "폴더 선택"
    finally:
        dlg.deleteLater()


def test_overkill_empty_then_top_left(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import ThresholdPreviewDialog
    dlg = _show(ThresholdPreviewDialog())
    try:
        _check_empty(dlg, "폴더 선택")
        dlg._roots = [tmp_path]
        dlg._sync_empty()
        _pump()
        _check_loaded(dlg)
    finally:
        dlg.deleteLater()


def test_zone_empty_then_top_left(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    dlg = _show(ZoneOriginDialog())
    try:
        _check_empty(dlg, "폴더 선택")
        dlg._roots = [tmp_path]
        dlg._render_head()
        _pump()
        _check_loaded(dlg)
    finally:
        dlg.deleteLater()


# ── B2 저장 ─────────────────────────────────────────────────────────────────
def test_save_buttons_are_named_blue_and_top_right(styled_qapp, tmp_path):
    from aoi_verification.app.gl import loader
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import ThresholdPreviewDialog
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    gl = _show(GlViewerDialog(None))
    ov = _show(ThresholdPreviewDialog())
    zo = _show(ZoneOriginDialog())
    try:
        rng = np.random.default_rng(1)
        gl.show_view(loader.ViewLoad(loader.GlImage(rng.integers(0, 255, (40, 60)).astype(np.uint8),
                                                    8, False, "y.png", [])))
        ov._roots = [tmp_path]
        ov._sync_empty()
        zo._roots = [tmp_path]
        zo._render_head()
        _pump()
        _check_save(gl, gl.png_btn, "그림으로 저장")
        _check_save(gl, gl.xlsx_btn, "엑셀로 저장")
        _check_save(ov, ov.export_btn, "HTML로 저장")
        _check_save(zo, zo.excel_btn, "엑셀로 저장")
    finally:
        gl.close()
        ov.deleteLater()
        zo.deleteLater()


def test_saved_line_shows_time_and_opens_the_folder(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import tool_open_save as T
    opened = []
    monkeypatch.setattr(T.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    line = T.SavedLine()
    assert not line.isVisibleTo(line.parentWidget() or line) or line.isHidden()
    line.show_saved(tmp_path / "a.xlsx", when=datetime(2026, 11, 24, 15, 30))
    assert line.label.text() == "저장 완료 15:30" and line.open_btn.text() == "폴더 열기"
    assert not line.isHidden()
    line.open_btn.click()
    assert Path(opened[0]) == tmp_path
    line.deleteLater()


def test_gl_save_shows_the_saved_line(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.gl import loader
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    dlg = _show(GlViewerDialog(None))
    try:
        rng = np.random.default_rng(1)
        dlg.show_view(loader.ViewLoad(loader.GlImage(rng.integers(0, 255, (40, 60)).astype(np.uint8),
                                                     8, False, "y.png", [])))
        monkeypatch.setattr(dlg, "save_xlsx", lambda p: None)
        assert dlg.saved_line.isHidden()
        dlg._on_xlsx_picked(str(tmp_path / "g.xlsx"))
        assert not dlg.saved_line.isHidden() and dlg.saved_line.saved_path() == tmp_path / "g.xlsx"
        assert dlg.saved_line.label.text().startswith("저장 완료 ")
    finally:
        dlg.close()


def test_overkill_export_shows_the_saved_line(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        monkeypatch.setattr(dlg, "export_html", lambda p, *a: None)
        monkeypatch.setattr(T.QDesktopServices, "openUrl", lambda url: True)
        dlg._export_to(str(tmp_path / "o.html"))
        assert not dlg.saved_line.isHidden() and dlg.saved_line.saved_path() == tmp_path / "o.html"
    finally:
        dlg.deleteLater()


def test_zone_excel_done_shows_the_saved_line(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import zone_origin_dialog as Z
    dlg = Z.ZoneOriginDialog()
    try:
        monkeypatch.setattr(Z.QTimer, "singleShot", lambda *a, **k: None)
        dlg._on_excel_done(str(tmp_path / "z.xlsx"))
        assert not dlg.saved_line.isHidden() and dlg.saved_line.saved_path() == tmp_path / "z.xlsx"
    finally:
        dlg.deleteLater()


def test_result_page_keeps_its_bottom_right_save():
    """결과 화면은 단계 화면이라 그대로 — 저장 버튼은 아래 오른쪽(현택님 2026-11-24)."""
    import inspect
    from aoi_verification.app.ui.pages import result_page
    src = inspect.getsource(result_page.ResultPage)
    assert "SavedLine" not in src and "tool_open_save" not in src
