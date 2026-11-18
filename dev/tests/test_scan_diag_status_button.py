"""개발자 모드 상태바 [Scan 진단 수집] — 모드를 따라 보이고, 고른 폴더로 zip 을 만든다."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui import main_window as mw   # noqa: E402
from aoi_verification.app.utils import prefs           # noqa: E402


def _window(monkeypatch):
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_open_dev_mode", lambda self: None)
    return mw.MainWindow()


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
    real = scan_diag.collect
    seen = {}
    monkeypatch.setattr(scan_diag, "collect", lambda p: seen.setdefault("p", p) and real(p, out))
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
    assert seen["p"].name == "1.2.c.5.2.jpeg"
    assert got[0][0].endswith(".zip") and (out / got[0][0].split("/")[-1]).is_file()
