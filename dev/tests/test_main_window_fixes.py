"""메인 창 확정 버그 수정 회귀 가드 (H1 B방향 · M2 · M3 · M4 · L15).

창 전체를 만들지 않고 ``MainWindow.__new__`` 위에 필요한 속성만 얹는다
(`test_cross_check.py` 와 같은 처방).  M5 는 `test_new_session_state_reset.py`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

import aoi_verification.app.ui.main_window as MW                     # noqa: E402
from aoi_verification.app.models.result import MatchResult         # noqa: E402


def _bare():
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._reset_cross_state()
    return win


# ---------------------------------------------------------------------------
# H1 — B 방향도 '검토했지만 비었음' 을 '검토 안 함' 으로 읽지 않는다
# ---------------------------------------------------------------------------
def test_reenter_review_b_keeps_empty_reviewed_result(styled_qapp):
    """B 에서 매치를 전부 버린 뒤 결과에서 검토로 돌아가도 자동 매치가 되살아나지 않는다."""
    win = _bare()
    auto = MatchResult(slot="S1", ref_path=Path("/r/1.jpg"),
                       val_path=Path("/v/1.jpg"), score=0.9)
    win._cross_stage = "B"
    win._matches_b = [auto]                  # B 자동 매치(큐 방향)
    win._b_reviewed = True                   # 검토를 거쳤고
    win._b_all, win._b_kept = [], []         # 남은 것이 없다
    win._b_unmatched_keys = set()
    loaded: dict = {}
    win._match_review_page = SimpleNamespace(
        set_reverse=lambda on: None,
        load_state=lambda base, **kw: loaded.update(base=list(base)))
    win._match_page = SimpleNamespace(
        review_context=lambda: SimpleNamespace(
            score_cache=None, review_scores=None, coord_mode=False,
            tolerance=0.0, coord_failed_count=0),
        get_state=lambda: None,
        build_candidates_by_ref=lambda base: None)
    win._notify_ready_for_review = lambda: None
    win._show_page = lambda w, **kw: None
    MW.MainWindow._reenter_review(win)
    assert loaded["base"] == [], "버린 B 자동 매치가 검토 화면에 되살아났다"

    win._b_reviewed = False                  # 검토 전이라면 자동 매치가 기반이다
    MW.MainWindow._reenter_review(win)
    assert loaded["base"] == [auto]


# ---------------------------------------------------------------------------
# M2 — on_done 은 정확히 한 번, 예외는 기록
# ---------------------------------------------------------------------------
def test_resolve_kla_runs_on_done_once_when_impl_raises_after_calling_it(caplog):
    win = _bare()

    def impl(sr, side, on_done):
        on_done()
        raise RuntimeError("after done")

    win._kla_resolve_impl = impl
    calls: list = []
    with caplog.at_level(logging.ERROR, logger="aoi.ui"):
        MW.MainWindow._resolve_and_merge_kla(win, None, "ref",
                                             lambda: calls.append(1))
    assert calls == [1], "예외 뒤 폴백이 다음 단계를 두 번 돌렸다"
    assert any(r.levelno >= logging.ERROR for r in caplog.records), "예외를 기록해야 한다"


def test_resolve_kla_falls_back_to_on_done_when_impl_raises_early():
    win = _bare()

    def impl(sr, side, on_done):
        raise RuntimeError("early")

    win._kla_resolve_impl = impl
    calls: list = []
    MW.MainWindow._resolve_and_merge_kla(win, None, "ref", lambda: calls.append(1))
    assert calls == [1]


# ---------------------------------------------------------------------------
# M3 — OCR 완료 슬롯에서 병합이 죽어도 예외가 밖으로 안 나가고 finalize 는 돈다
# ---------------------------------------------------------------------------
def test_ocr_done_slot_survives_merge_error(styled_qapp, monkeypatch, caplog):
    import aoi_verification.app.workers.wafer_id_ocr as ocr_mod

    class _Sig:
        def __init__(self):
            self.cb = []

        def connect(self, f):
            self.cb.append(f)

        def emit(self, *a):
            for f in self.cb:
                f(*a)

    class _FakeWorker:
        def __init__(self, jobs, parent=None):
            self.signals = SimpleNamespace(progress=_Sig(), done=_Sig(), failed=_Sig())

        def start(self):                      # 스레드 없이 곧바로 '끝났다' 를 알린다
            self.signals.done.emit({"K1": "WID1"}, {})

    n = {"merge": 0}

    def merge(sr, a, b):
        n["merge"] += 1
        if n["merge"] >= 2:                   # 1회는 정보파일 단계, 2회가 OCR 슬롯 안
            raise RuntimeError("merge boom")

    monkeypatch.setattr(ocr_mod, "WaferIdOcrWorker", _FakeWorker)
    monkeypatch.setattr(MW, "merge_unmatched_by_wafer_id", merge)
    monkeypatch.setattr(MW.wafer_id, "ocr_available", lambda: True)
    monkeypatch.setattr(MW.kla_info, "read_wafer_id", lambda d: None)

    slot = SimpleNamespace(ref_images=[SimpleNamespace(path=Path("/r/k.jpg"))],
                           val_images=[], ref_dir=Path("/r"), val_dir=None)
    sr = SimpleNamespace(slots={"K1": slot}, ref_only=["K1"], val_only=[])
    win = _bare()
    win._loading = SimpleNamespace(show_overlay=lambda *a, **k: None,
                                   set_progress=lambda *a, **k: None)
    win._journey_steps = lambda: None
    win._kla_broken = []
    done: list = []
    with caplog.at_level(logging.ERROR, logger="aoi.ui"):
        MW.MainWindow._kla_resolve_impl(win, sr, "ref", lambda: done.append(1))
    assert n["merge"] == 2
    assert done == [1], "병합이 죽어도 다음 단계(finalize→on_done)는 이어져야 한다"
    assert any("병합" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# M4 — 매칭 중지는 세션을 버린다 / 끝난 세션은 자동 저장하지 않는다
# ---------------------------------------------------------------------------
def test_match_cancel_clears_session_and_stops_autosave(styled_qapp, monkeypatch):
    win = _bare()
    cleared: list = []
    saved: list = []
    monkeypatch.setattr(MW.session_mod, "clear", lambda: cleared.append(1))
    monkeypatch.setattr(MW.session_mod, "save", lambda st: saved.append(st))
    monkeypatch.setattr(MW.wakelock, "release", lambda: None)
    win._phase = MW.PHASE_A_MATCH
    win._matches_a = [MatchResult(slot="S", ref_path=Path("/r/a.jpg"),
                                  val_path=Path("/v/a.jpg"), score=1.0)]
    win._skipped_a = {"S": [object()]}
    win._setup_page = object()
    shown: list = []
    win._show_page = lambda p, **kw: shown.append(p)
    win._input = SimpleNamespace(mode="single", ref_root=Path("/r"), val_root=Path("/v"),
                                 ref_machine="1", val_machine="2", threshold=0.5)
    win._session_id = "x"
    win._select_page = SimpleNamespace(get_state=lambda: None)
    win._match_page = SimpleNamespace(get_state=lambda: None)
    win._is_extract = lambda: False

    MW.MainWindow._on_match_cancelled(win)

    assert cleared == [1], "세션 파일을 지우지 않으면 다음 실행이 접은 실행을 이어하자고 한다"
    assert win._phase == MW.PHASE_NONE
    assert win._matches_a == [] and not any(win._skipped_a.values())
    assert shown == [win._setup_page]
    MW.MainWindow._autosave(win)             # 30 초 타이머가 부르는 바로 그 호출
    assert saved == [], "접은 세션을 자동 저장이 다시 썼다"


def test_autosave_skips_when_phase_none_but_saves_in_a_stage(monkeypatch):
    win = _bare()
    saved: list = []
    monkeypatch.setattr(MW.session_mod, "save", lambda st: saved.append(st))
    win._input = SimpleNamespace(mode="single", ref_root=Path("/r"), val_root=Path("/v"),
                                 ref_machine="1", val_machine="2", threshold=0.5)
    win._session_id = "x"
    win._matches_a = []
    win._select_page = SimpleNamespace(get_state=lambda: None)
    win._match_page = SimpleNamespace(get_state=lambda: None)
    win._is_extract = lambda: False
    win._phase = MW.PHASE_NONE
    MW.MainWindow._autosave(win)
    assert saved == []
    win._phase = MW.PHASE_A_MATCH
    MW.MainWindow._autosave(win)
    assert [s.stage for s in saved] == [MW.PHASE_A_MATCH]


# ---------------------------------------------------------------------------
# L15 — 종료 중 워커 정리 실패는 삼키지 않고 기록한다
# ---------------------------------------------------------------------------
def test_close_event_logs_worker_stop_failure(styled_qapp, monkeypatch, caplog):
    class _BadWorker:
        def isRunning(self):
            return True

        def stop(self):
            raise RuntimeError("stop boom")

        def wait(self, ms):
            pass

    monkeypatch.setattr(MW.MainWindow, "_start_backend_import_async", lambda self: None)
    win = MW.MainWindow()
    try:
        win._ocr_worker = _BadWorker()
        with caplog.at_level(logging.WARNING, logger="aoi.ui"):
            win.close()
        assert any("KLA OCR" in r.getMessage() for r in caplog.records)
    finally:
        win._ocr_worker = None
