"""개발자 모드 상태바 [Scan 진단 수집] — 모드를 따라 보이고, 고른 폴더로 zip 을 만든다."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui import main_window as mw   # noqa: E402
from aoi_verification.app.utils import prefs           # noqa: E402


_WINDOWS: list = []


@pytest.fixture(autouse=True)
def _delete_windows(qapp):
    """테스트마다 만든 MainWindow 를 바로 지운다 — 남겨 두면 다음 창을 만드는 중 GC 가 옛 창을 치우다
    프로세스가 죽었다(병렬 실행에서 세 번째 창 생성 중 abort, 2026-11-21)."""
    yield
    while _WINDOWS:
        w = _WINDOWS.pop()
        w.close()
        w.deleteLater()
    qapp.processEvents()


def _window(monkeypatch):
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_open_dev_mode", lambda self: None)
    w = mw.MainWindow()
    _WINDOWS.append(w)
    return w


def test_button_follows_dev_mode(qapp, monkeypatch):
    prefs.patch(dev_mode=False)
    win = _window(monkeypatch)
    assert win._scan_diag_btn.isHidden()
    win._toggle_dev_mode()
    assert not win._scan_diag_btn.isHidden()
    win._toggle_dev_mode()
    assert win._scan_diag_btn.isHidden()


def test_run_writes_zip_for_picked_folder(qapp, monkeypatch, tmp_path):
    from aoi_verification.app.coords import scan_diag
    folder = tmp_path / "W01"
    folder.mkdir()
    (folder / "1.2.c.5.2.jpeg").write_bytes(b"x")
    out = tmp_path / "out"
    real = scan_diag.collect_many
    seen = {}
    monkeypatch.setattr(scan_diag, "collect_many",
                        lambda fs, out_root=None, progress=None: seen.setdefault("p", fs) and real(fs, out, progress))
    win = _window(monkeypatch)
    got = []
    win._scan_diag_done.disconnect()
    win._scan_diag_done.connect(lambda path, err: got.append((path, err)))
    win._run_scan_diag(str(folder))
    import time
    for _ in range(200):
        qapp.processEvents()
        if got:
            break
        time.sleep(0.02)
    assert got and got[0][1] == ""
    assert [f.name for f in seen["p"]] == ["W01"]
    assert got[0][0].endswith(".zip") and (out / got[0][0].split("/")[-1]).is_file()


def test_pick_uses_open_overkill_folders(qapp, monkeypatch, tmp_path):
    """Overkill 튜닝에 연 LOT 이 있으면 폴더 고르기 없이 그 폴더로 바로 모은다."""
    win = _window(monkeypatch)
    ran = []
    monkeypatch.setattr(win, "_overkill_folders", lambda: [tmp_path / "LOT"])
    monkeypatch.setattr(win, "_run_scan_diag_many", lambda roots: ran.append(roots))
    win._pick_scan_diag_folder()
    assert ran == [[tmp_path / "LOT"]] and win._scan_diag_picker is None


def test_overkill_folders_found_anywhere_and_remembered(qapp, monkeypatch, tmp_path):
    """Overkill 튜닝 화면이 최상위 창(전체화면)에 있어도 찾고, 닫힌 뒤에도 마지막 폴더로 모은다."""
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import ThresholdPreviewDialog
    win = _window(monkeypatch)
    dlg = ThresholdPreviewDialog(None)                  # main window 밖(최상위)
    dlg._roots = [tmp_path / "LOT"]
    dlg.show()                                          # 보이는 화면이 먼저 — 다른 테스트가 남긴 숨은 화면에 밀리지 않게
    assert win._overkill_folders() == [tmp_path / "LOT"]
    dlg.hide()
    dlg.deleteLater()
    qapp.processEvents()
    dlg = None
    import gc
    gc.collect()
    assert win._overkill_folders() == [tmp_path / "LOT"]
