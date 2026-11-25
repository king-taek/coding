"""고르기 창을 여는 화면들 — 곧바로 돌아오고, 취소하면 아무것도 바뀌지 않고, 고르면 그 일을 한다.

배경(2026-11-24, 현택님): '탐색기를 열고 찾다가 닫자 앱이 멈추고 버튼이 안 눌림'.  사진
정보 [사진 열기] · GL 열기/비교/저장 · 결과 저장(세션 없음) · Overkill 내보내기가 아직 정적
``QFileDialog.get*``(= exec, Windows 에서 Explorer 를 UI 스레드에서 돌림)였다.  전부
`ui/folder_picker.py` 의 비동기 창으로 옮겼다.  여기서는 화면마다
1. 버튼 처리기가 **곧바로 돌아온다**(창은 열린 채 — exec 였다면 테스트가 멈춘다),
2. 취소하면 로딩 덮개 · 저장 · 상태가 그대로다,
3. 고르면 원래 하던 일(사진 열기 · 저장)이 일어난다,
4. 설정 화면 [폴더 선택] 은 칸 용도별로 시작 위치를 정한다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QDialog            # noqa: E402

from aoi_verification.app.ui import folder_picker as fp      # noqa: E402
from aoi_verification.app.utils import prefs                 # noqa: E402


def _pump(n: int = 3) -> None:
    for _ in range(n):
        QApplication.processEvents()


def _choose(picker, value) -> None:
    """사용자가 OS 창에서 고른 것 — 네이티브 창이 내는 시그널을 흉내 낸다."""
    dlg = picker._dialog
    if isinstance(value, list):
        dlg.filesSelected.emit(value)
    else:
        dlg.fileSelected.emit(value)
    dlg.done(QDialog.DialogCode.Accepted)
    _pump()


def _cancel(picker) -> None:
    picker._dialog.reject()
    _pump()


# ---------------------------------------------------------------------------
def test_image_info_pick_is_async(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets.image_info_dialog import ImageInfoDialog
    shown: list = []
    monkeypatch.setattr(ImageInfoDialog, "open_many", lambda self, p: shown.append(p))   # 여러 장(넘겨 보기)
    d = ImageInfoDialog()
    d._on_pick()                                   # exec 였다면 여기서 멈춘다
    assert d._picker.is_open and d._picker.purpose == "image_info"
    _cancel(d._picker)
    assert shown == [] and not d._loading.isVisible()
    d._on_pick()
    _choose(d._picker, [str(tmp_path / "a.jpg")])
    assert shown == [[str(tmp_path / "a.jpg")]]
    assert fp.last_dir("image_info") == str(tmp_path)
    d.deleteLater()


def test_gl_viewer_open_compare_save_are_async(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.gl import loader
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    d = GlViewerDialog(None)
    opened, saved = [], []
    monkeypatch.setattr(d, "open_path", lambda p, **k: opened.append(p))
    monkeypatch.setattr(d, "open_path_b", opened.append)
    monkeypatch.setattr(d, "save_xlsx", saved.append)
    monkeypatch.setattr(d, "save_png", lambda p, **k: saved.append(p))

    d._on_pick()
    _cancel(d._open_picker)
    assert opened == []
    d._on_pick()
    _choose(d._open_picker, [str(tmp_path / "a.tif")])
    assert opened == [tmp_path / "a.tif"]

    d.show_view(loader.ViewLoad(loader.GlImage(
        np.zeros((20, 20), np.uint8), 8, False, str(tmp_path / "a.tif"), [])))
    _pump()
    d._on_compare()
    assert d._cmp_picker.is_open
    _choose(d._cmp_picker, [str(tmp_path / "b.tif")])
    assert opened[-1] == tmp_path / "b.tif"

    d._on_save_xlsx()
    assert d._xlsx_picker.is_open
    _cancel(d._xlsx_picker)
    assert saved == []
    d._on_save_xlsx()
    _choose(d._xlsx_picker, str(tmp_path / "out.xlsx"))
    d._on_save_png()
    _choose(d._png_picker, str(tmp_path / "out.png"))
    assert saved == [tmp_path / "out.xlsx", tmp_path / "out.png"]
    d.deleteLater()


def test_result_save_without_session_is_async(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.pages import result_page as rp

    page = rp.ResultPage()

    class _R:
        ref_machine, val_machine = "A", "B"

    page._result = _R()
    page._target_path = None
    started: list = []

    class _Exp:
        def __init__(self, result, dst, **k):
            started.append(dst)

            class _S:
                def __getattr__(self, n):
                    return type("Sig", (), {"connect": lambda *a, **k: None})()
            self.signals = _S()

        def start(self):
            pass

    monkeypatch.setattr(rp, "ExcelExporter", _Exp)
    page._on_export()                                 # 곧바로 돌아온다
    assert page._save_picker.is_open
    _cancel(page._save_picker)
    assert started == [] and page._save_path is None
    assert not page._loading.isVisible(), "취소했는데 로딩 덮개가 떴다"

    page._on_export()
    _choose(page._save_picker, str(tmp_path / "r.xlsx"))
    assert started == [tmp_path / "r.xlsx"] and page._save_path == tmp_path / "r.xlsx"
    page._loading.hide_overlay()
    page.deleteLater()


def test_threshold_export_uses_the_shared_save_picker(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    d = T.ThresholdPreviewDialog()
    assert isinstance(d._save_picker, fp.SavePicker)
    assert d._save_picker.purpose == "threshold_export"
    got: list = []
    monkeypatch.setattr(d, "export_html", lambda p, *a: got.append(p))
    monkeypatch.setattr(T.QDesktopServices, "openUrl", lambda *_a: True)
    d._save_picker.open("저장", str(tmp_path / "x.html"), "HTML (*.html)", "html")
    _choose(d._save_picker, str(tmp_path / "x.html"))
    assert got == [tmp_path / "x.html"]
    assert fp.last_dir("threshold_export") == str(tmp_path)
    d.deleteLater()


# ---------------------------------------------------------------------------
def _setup_page():
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    return SetupPage()


def test_setup_browse_start_folder_per_field(styled_qapp, tmp_path):
    """입력란에 경로가 있으면 그 상위, 없으면 그 칸 용도로 마지막에 고른 곳."""
    page = _setup_page()
    picker = page._folder_picker
    lot = tmp_path / "NAS" / "LOT1"
    lot.mkdir(parents=True)

    page.ref_path_edit.setText(str(lot))
    page._browse(page.ref_path_edit)
    assert picker.purpose == "setup_ref"
    assert picker._dialog.directory().absolutePath() == str(lot.parent)
    _choose(picker, str(lot))
    assert page.ref_path_edit.text() == str(lot)
    assert fp.last_dir("setup_ref") == str(lot.parent)
    assert fp.last_dir("setup_val") == ""

    page.ref_path_edit.clear()                      # 비어 있으면 기억한 곳
    page._browse(page.ref_path_edit)
    assert picker._dialog.directory().absolutePath() == str(lot.parent)
    _cancel(picker)
    assert page.ref_path_edit.text() == ""          # 취소는 칸을 건드리지 않는다
    assert page._folder_dialog_open is False

    page._browse(page.val_path_edit)                # 검증 칸은 따로
    assert picker.purpose == "setup_val"
    _cancel(picker)
    page.deleteLater()


def test_setup_browse_falls_back_to_last_run_root(styled_qapp, tmp_path):
    """기억이 없으면 마지막 [검증 시작] 의 폴더(`last_ref_root` — 쓰기만 하고 안 읽던 값)의 상위."""
    prev = tmp_path / "share" / "LOT_OLD"
    prev.mkdir(parents=True)
    prefs.patch(last_ref_root=str(prev))
    page = _setup_page()
    page._browse(page.ref_path_edit)
    assert page._folder_picker._dialog.directory().absolutePath() == str(prev.parent)
    _cancel(page._folder_picker)
    page.deleteLater()


def test_setup_stall_log_still_names_its_workers(styled_qapp, caplog):
    """정지 줄은 계속 설정 화면이 쓴다 — 페이지 워커(die 스캔 · 폴더 확인)를 덧붙이려고."""
    import logging
    page = _setup_page()
    assert page._folder_picker._on_stall == page._log_stall
    page._die_scanning_for = "/x"
    with caplog.at_level(logging.WARNING, logger="aoi.ui"):
        page._folder_picker._on_stall(2.5, "")
    assert "die 안내 스캔 진행 중" in "\n".join(r.getMessage() for r in caplog.records)
    page._die_scanning_for = None
    page.deleteLater()
