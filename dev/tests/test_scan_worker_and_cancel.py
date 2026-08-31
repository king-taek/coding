"""폴더 스캔을 워커로 (U-05) + [중지] 가 단계마다 옳게 동작하게 (P-09).

U-05 배경: `slot.scan` 은 폴더를 하나씩 열어 사진을 열거하므로 NAS 에서는 폴더
수에 비례해 초 단위로 걸린다.  그동안 GUI 스레드에서 돌면서 진행을 보여 주려고
콜백마다 `processEvents` 를 불렀는데, 그 사이 사용자의 클릭이 **스캔 도중에
재진입**할 수 있었다.  게다가 스캔이 예외로 죽으면 오버레이가 **켜진 채** 남아
앱이 잠긴 것처럼 보였다(폴더가 그 사이 사라진 경우 등).

P-09 배경: [중지] 는 단계와 무관하게 썸네일 풀만 멈췄다.  스캔 단계에는 아예
버튼이 없어(cancelable=False) 느린 NAS 를 만나면 기다리는 것 말고 할 수 있는 게
없었다.  스캔은 이후 모든 단계의 입력이라 '건너뛰고 진행' 이 성립하지 않으므로
세션을 접고 설정으로 돌아간다.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.models.slot import scan                    # noqa: E402
import aoi_verification.app.ui.main_window as MW                     # noqa: E402


def _tree(tmp_path) -> tuple[Path, Path]:
    ref = tmp_path / "ref"
    val = tmp_path / "val"
    for root, names in ((ref, ["S1", "S2", "R9"]), (val, ["S1", "S2", "V9"])):
        for n in names:
            d = root / n
            d.mkdir(parents=True)
            (d / f"{n}_0.jpg").write_bytes(b"")
    return ref, val


# ---------------------------------------------------------------------------
# U-05 — 워커 스캔이 동기 스캔과 같은 결과를 낸다
# ---------------------------------------------------------------------------
def test_worker_scan_equals_the_direct_scan(styled_qapp, tmp_path):
    ref, val = _tree(tmp_path)
    direct = scan(ref, val)

    got: list = []
    w = MW._FolderScan(1, ref, val)
    w.signals.done.connect(lambda tk, sr: got.append(sr))
    w.run()                              # 스레드를 띄우지 않고 본문만 그대로 실행

    assert got, "워커가 결과를 내지 않았다"
    sr = got[0]
    assert sorted(sr.slots) == sorted(direct.slots)
    assert sr.ref_only == direct.ref_only
    assert sr.val_only == direct.val_only
    assert sr.common_slot_names == direct.common_slot_names


def test_worker_reports_progress_as_two_numbers(styled_qapp, tmp_path):
    """진행 콜백의 (done, total) 계약은 그대로다 — 마지막은 반드시 (total, total)."""
    ref, val = _tree(tmp_path)
    seen: list = []
    w = MW._FolderScan(7, ref, val)
    w.signals.progress.connect(lambda tk, d, t: seen.append((tk, d, t)))
    w.run()

    assert seen, "진행 보고가 하나도 없다"
    assert all(tk == 7 for tk, _, _ in seen), "세대 번호가 실려 있지 않다"
    last_done, last_total = seen[-1][1], seen[-1][2]
    assert last_done == last_total, f"마지막 보고가 완료가 아니다: {seen[-1]}"


# ---------------------------------------------------------------------------
# ★ 진행이 **끝나기 전에도** 흐른다
#
# 실제 신고: "폴더 스캔 로딩에서 진척도(몇 개 중 몇 개)가 안 보임 — 그냥 폴더 스캔만
# 진행 중이라서 얼마나 걸리는지도 모르고 몇 개 되었는지도 확인이 안 되는 상태."
# 원인은 `done % 25 == 0` 스로틀이었다.  `scan` 은 슬롯마다 done 을 1부터 올리므로
# **슬롯이 25개인 자재에서는 done=25(=total) 한 번만** 참이 된다 — 스캔이 끝나는
# 순간에야 처음 보고했고, 그때까지 오버레이는 busy 그대로였다.
# ---------------------------------------------------------------------------
def test_a_25_slot_lot_reports_before_it_finishes(styled_qapp, tmp_path):
    """25슬롯에서 첫 보고가 마지막 보고여서는 안 된다(그게 바로 그 버그다)."""
    ref = tmp_path / "ref"
    val = tmp_path / "val"
    for i in range(25):
        for root in (ref, val):
            d = root / f"Slot_{i + 1:02d}"
            d.mkdir(parents=True)
            (d / "a.jpg").write_bytes(b"")

    seen: list = []
    w = MW._FolderScan(1, ref, val)
    w.signals.progress.connect(lambda _tk, d, t: seen.append((d, t)))
    w.run()

    assert len(seen) >= 2, f"보고가 {len(seen)}회뿐 — 끝날 때 한 번만 나간다: {seen}"
    assert seen[0][0] < seen[0][1], f"첫 보고가 이미 완료다: {seen[0]}"
    assert seen[-1] == (25, 25), f"마지막 보고가 완료가 아니다: {seen[-1]}"
    assert all(t == 25 for _, t in seen), "total 이 흔들린다"
    # 단조 증가 — 뒤로 가는 진행은 바를 되감는다.
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_progress_is_throttled_by_time_not_by_count(styled_qapp, tmp_path,
                                                    monkeypatch):
    """느린 폴더는 슬롯마다, 빠른 폴더는 솎아서 — 개수 기준으로 되돌리면 실패한다."""
    ref = tmp_path / "ref"
    val = tmp_path / "val"
    for i in range(6):
        for root in (ref, val):
            d = root / f"Slot_{i + 1:02d}"
            d.mkdir(parents=True)
            (d / "a.jpg").write_bytes(b"")

    # 슬롯 하나에 최소 간격보다 오래 걸리게 만든다 → 전부 보고돼야 한다.
    real = MW.scan

    def slow(ref_root, val_root, progress=None):
        def _slow(done, total):
            time.sleep((MW._FolderScan._PROGRESS_MIN_MS + 10) / 1000.0)
            if progress is not None:
                progress(done, total)
        return real(ref_root, val_root, progress=_slow)

    monkeypatch.setattr(MW, "scan", slow)
    seen: list = []
    w = MW._FolderScan(1, ref, val)
    w.signals.progress.connect(lambda _tk, d, t: seen.append(d))
    w.run()
    assert seen == [1, 2, 3, 4, 5, 6], f"느린 스캔인데 솎였다: {seen}"


def test_scan_failure_is_reported_not_swallowed(styled_qapp, tmp_path,
                                                monkeypatch):
    """예전에는 예외가 나면 오버레이가 켜진 채 남아 앱이 잠긴 것처럼 보였다."""
    def _boom(*a, **k):
        raise OSError("폴더가 사라졌습니다")

    monkeypatch.setattr(MW, "scan", _boom)
    fails: list = []
    w = MW._FolderScan(3, tmp_path, tmp_path)
    w.signals.failed.connect(lambda tk, msg: fails.append((tk, msg)))
    w.run()

    assert len(fails) == 1, "실패가 보고되지 않았다(오버레이가 영원히 남는다)"
    assert fails[0][0] == 3
    assert "폴더가 사라졌습니다" in fails[0][1]


def test_stop_discards_partial_results(styled_qapp, tmp_path):
    """취소한 스캔은 **아무것도 보고하지 않는다** — 부분 결과가 세션에 얹히면 안 된다."""
    ref, val = _tree(tmp_path)
    done: list = []
    fails: list = []
    w = MW._FolderScan(1, ref, val)
    w.signals.done.connect(lambda tk, sr: done.append(sr))
    w.signals.failed.connect(lambda tk, m: fails.append(m))
    w.stop()                             # 시작 전에 이미 중지 요청
    w.run()

    assert done == [], "취소했는데 결과를 보고했다"
    assert fails == [], "취소를 실패로 보고했다"


# ---------------------------------------------------------------------------
# P-09 — [중지] 가 단계마다 다른 뜻을 갖는다
# ---------------------------------------------------------------------------
class _FakeOverlay:
    def __init__(self):
        self.hidden = 0
    def hide_overlay(self):
        self.hidden += 1


class _FakeWorker:
    def __init__(self):
        self.stopped = 0
    def stop(self):
        self.stopped += 1


def _win_stub():
    """`MainWindow` 를 통째로 띄우지 않고 취소 분기만 떼어 시험한다."""
    win = MW.MainWindow.__new__(MW.MainWindow)
    win._stage = ""
    win._scan_token = 0
    win._scan_worker = None
    win._thumb_pool = None
    win._thumbs_handled = False
    win._scan = object()
    win._loading = _FakeOverlay()
    win._setup_page = object()
    win._shown: list = []
    win._show_page = lambda w, **k: win._shown.append(w)
    return win


def test_cancel_during_scan_stops_the_worker_and_returns_to_setup(styled_qapp,
                                                                  monkeypatch):
    monkeypatch.setattr(MW.wakelock, "release", lambda: None)
    win = _win_stub()
    worker = _FakeWorker()
    win._stage = "scan"
    win._scan_worker = worker

    MW.MainWindow._on_loading_cancel(win)

    assert worker.stopped == 1, "스캔 워커를 멈추지 않았다"
    assert win._scan is None, "부분 스캔 결과가 남았다"
    assert win._scan_token == 1, "세대를 올리지 않아 늦은 결과가 되살아난다"
    assert win._loading.hidden == 1, "오버레이가 켜진 채 남았다"
    assert win._shown == [win._setup_page], "설정 화면으로 돌아가지 않았다"


def test_cancel_during_thumbnails_keeps_the_old_skip_behaviour(styled_qapp):
    """썸네일은 비필수라 예전처럼 '건너뛰고 진행' 이다 — 세션을 접지 않는다."""
    win = _win_stub()
    pool = _FakeWorker()
    win._thumb_pool = pool
    win._stage = ""                      # 썸네일 단계
    win._ready: list = []
    win._on_thumbs_ready = lambda: win._ready.append(True)

    MW.MainWindow._on_loading_cancel(win)

    assert pool.stopped == 1
    assert win._ready == [True], "다음 단계로 진행하지 않았다"
    assert win._shown == [], "썸네일 취소인데 설정으로 돌아갔다"


def test_late_scan_result_is_discarded_after_cancel(styled_qapp, monkeypatch):
    """취소 뒤 뒤늦게 도착한 done 은 세션을 되살리면 안 된다."""
    monkeypatch.setattr(MW.wakelock, "release", lambda: None)
    win = _win_stub()
    win._stage = "scan"
    win._scan_worker = _FakeWorker()
    stale_token = win._scan_token

    MW.MainWindow._on_loading_cancel(win)
    win._input = object()
    MW.MainWindow._on_scan_done(win, stale_token, object())

    assert win._scan is None, "옛 세대 스캔 결과가 반영됐다"
