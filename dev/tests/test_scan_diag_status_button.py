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
    monkeypatch.setattr(scan_diag, "collect", lambda p, out_root=None: seen.setdefault("p", p) and real(p, out))
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


def test_pick_uses_open_overkill_folders(qapp, monkeypatch, tmp_path):
    """Overkill 튜닝에 연 LOT 이 있으면 폴더 고르기 없이 그 폴더로 바로 모은다."""
    win = _window(monkeypatch)
    ran = []
    monkeypatch.setattr(win, "_overkill_folders", lambda: [tmp_path / "LOT"])
    monkeypatch.setattr(win, "_run_scan_diag_many", lambda roots: ran.append(roots))
    win._pick_scan_diag_folder()
    assert ran == [[tmp_path / "LOT"]] and win._scan_diag_picker is None
