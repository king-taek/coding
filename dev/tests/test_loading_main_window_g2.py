"""로딩 · 메인 창 묶음 — 화면보호기 해제(B7), 업데이트 버튼(L5), 저장·OCR·설치 [중지](L7·L15),
KLA 정보파일 백그라운드 읽기(L4), 문구(C8·C24·C53·C55·C61·L6)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QPushButton  # noqa: E402

from aoi_verification.app import i18n  # noqa: E402
from aoi_verification.app.ui import main_window as MW  # noqa: E402


class _Overlay:
    def __init__(self):
        self.hidden = 0
        self.progress: list = []

    def hide_overlay(self, *a, **k):
        self.hidden += 1

    def set_progress(self, d, t, m=""):
        self.progress.append((d, t, m))


class _Sig(QObject):
    done = pyqtSignal(str)
    failed = pyqtSignal(str)


class _Exp:
    def __init__(self):
        self.signals = _Sig()
        self.stopped = 0

    def isRunning(self):
        return True

    def stop(self):
        self.stopped += 1


# ── B7 ───────────────────────────────────────────────────────────────────
def test_scan_failure_releases_wakelock(styled_qapp, monkeypatch):
    rel: list = []
    monkeypatch.setattr(MW.wakelock, "release", lambda: rel.append(1))
    monkeypatch.setattr(MW.sheets, "warn", lambda *a, **k: None)
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._scan_token, win._scan_worker, win._stage = 1, None, "scan"
    win._loading = _Overlay()
    win._multi = lambda: False
    win._leg_message = lambda m: m
    MW.MainWindow._on_scan_failed(win, 1, "boom")
    assert rel == [1]


def test_scan_failure_in_later_leg_keeps_wakelock(styled_qapp, monkeypatch):
    rel: list = []
    monkeypatch.setattr(MW.wakelock, "release", lambda: rel.append(1))
    monkeypatch.setattr(MW.sheets, "warn", lambda *a, **k: None)
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._scan_token, win._scan_worker, win._stage = 1, None, "scan"
    win._loading = _Overlay()
    win._multi = lambda: True
    win._leg_idx = 1
    win._leg_message = lambda m: m
    win._multi_after_match_empty = lambda: None
    MW.MainWindow._on_scan_failed(win, 1, "boom")
    assert rel == []


def test_empty_queue_releases_wakelock(styled_qapp, monkeypatch):
    rel: list = []
    monkeypatch.setattr(MW.wakelock, "release", lambda: rel.append(1))
    monkeypatch.setattr(MW.sheets, "warn", lambda *a, **k: None)
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._scan = SimpleNamespace(common_slot_names=[], slots={})
    win._input = object()
    MW.MainWindow._enter_stage2_auto_all(win)
    assert rel == [1]


# ── L5 ───────────────────────────────────────────────────────────────────
def test_update_button_shows_checking_text(styled_qapp):
    btn = QPushButton("업데이트 확인")
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._setup_page = SimpleNamespace(update_btn=btn)
    MW.MainWindow._set_update_btn_busy(win, True)
    assert btn.text() == i18n.KO.UPDATE_BTN_CHECKING and not btn.isEnabled()
    MW.MainWindow._set_update_btn_busy(win, False)
    assert btn.text() == "업데이트 확인" and btn.isEnabled()


# ── L7 · L15 ─────────────────────────────────────────────────────────────
def test_extract_export_cancel_stops_worker_and_ignores_late_result(styled_qapp):
    exp = _Exp()
    got: list = []
    exp.signals.done.connect(got.append)
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._extract_exporter = exp
    win._stage = ""
    win._loading = _Overlay()
    win._select_page = object()
    win._stack = SimpleNamespace(currentWidget=lambda: win._select_page)
    win._sync_rail = lambda w: None
    MW.MainWindow._on_loading_cancel(win)
    assert exp.stopped == 1 and win._loading.hidden == 1
    exp.signals.done.emit("x.xlsx")
    assert got == []                       # 늦은 완료가 화면을 되살리지 않는다


def test_cancel_stops_ocr_and_openvino_workers(styled_qapp):
    for attr in ("_ocr_worker", "_openvino_worker"):
        w = _Exp()
        win = MW.MainWindow.__new__(MW.MainWindow)
        win._stage = ""
        win._extract_exporter = win._ocr_worker = win._openvino_worker = None
        setattr(win, attr, w)
        MW.MainWindow._on_loading_cancel(win)
        assert w.stopped == 1, attr


def test_exporter_reports_file_write_before_saving(tmp_path):
    from aoi_verification.app.workers.exporter import ExcelExporter
    ex = ExcelExporter.__new__(ExcelExporter)
    import threading
    ex._stop = threading.Event()
    ex._dst = tmp_path / "out.xlsx"
    ex._prog_done = ex._prog_total = 7
    msgs: list = []
    ex.signals = SimpleNamespace(progress=SimpleNamespace(
        emit=lambda d, t, m: msgs.append((d, t, m))))

    class _Wb:
        sheetnames: list = []

        def __getitem__(self, k):
            raise KeyError(k)

        def save(self, p):
            msgs.append("save")
            open(p, "wb").write(b"x")

    import aoi_verification.app.workers.exporter as E
    E._strip_corporate_metadata = lambda p: None
    ex._save_workbook(_Wb(), 0.0)
    assert msgs[0] == (7, 7, i18n.KO.EXPORT_WRITING) and msgs[1] == "save"
    assert not (tmp_path / "out.xlsx.part").exists()


def test_exporter_stop_during_save_leaves_no_file(tmp_path):
    import threading
    import aoi_verification.app.workers.exporter as E
    ex = E.ExcelExporter.__new__(E.ExcelExporter)
    ex._stop = threading.Event()
    ex._dst = tmp_path / "out.xlsx"
    ex._prog_done = ex._prog_total = 0
    ex.signals = SimpleNamespace(progress=SimpleNamespace(emit=lambda *a: None))

    class _Wb:
        def __getitem__(self, k):
            raise KeyError(k)

        def save(self, p):
            open(p, "wb").write(b"half")
            ex._stop.set()                     # 쓰는 도중 [중지]

    E._strip_corporate_metadata = lambda p: None
    with pytest.raises(E._Cancelled):
        ex._save_workbook(_Wb(), 0.0)
    assert list(tmp_path.iterdir()) == []      # 반쯤 쓴 파일도, 결과 파일도 없다


# ── L4 ───────────────────────────────────────────────────────────────────
def test_kla_info_worker_reports_progress_and_reads(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.workers import kla_info_read as K
    monkeypatch.setattr(K.kla_info, "read_wafer_id",
                        lambda d: "WID" + d.name if d.name != "b" else None)
    jobs = [("ref", n, tmp_path / n) for n in ("a", "b", "c")]
    w = K.KlaInfoReadWorker(jobs)
    prog: list = []
    out: list = []
    w.signals.progress.connect(lambda d, t: prog.append((d, t)))
    w.signals.done.connect(out.append)
    w.run()
    assert prog[0] == (0, 3) and prog[-1] == (3, 3)
    assert out == [{("ref", "a"): "WIDa", ("ref", "c"): "WIDc"}]


def test_read_kla_info_without_jobs_is_synchronous(styled_qapp):
    win = MW.MainWindow.__new__(MW.MainWindow)
    got: list = []
    MW.MainWindow._read_kla_info(win, [], got.append)
    assert got == [{}]


# ── 문구 ─────────────────────────────────────────────────────────────────
def test_wording():
    k = i18n.KO
    assert k.LOAD_KLA_INFO == "KLA 정보파일 읽는 중…"
    assert k.LOAD_KLA_OCR == "KLA 웨이퍼 번호 읽는 중…"
    assert k.SIZE_TIER_NOTICE_FMT.format(thumb=1, q=2) == "사진이 많아 미리보기 화질을 낮췄습니다"
    assert "pip" not in k.UPDATE_DEPS_CHANGED and "git" not in k.UPDATE_DEPS_CHANGED
    assert "관리자에게 새 배포본을 요청하세요" in k.UPDATE_DEPS_CHANGED
    assert k.UPDATE_CONNECT_FAILED == "업데이트 서버에 연결하지 못했습니다. 인터넷·프록시를 확인하세요."
    assert k.WHATS_NEW_OK == "확인" and k.WHATS_NEW_KICKER_FMT.startswith("업데이트 · ")
    assert k.OPENVINO_INSTALL_PROGRESS == "Intel GPU 가속 구성 요소 설치 중…"
