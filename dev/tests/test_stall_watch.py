"""UI 정지 계측 — '몇 초 멈춘다' 는 신고에 근거를 남긴다.

배경: 폴더 선택 렉을 세 번 고쳤는데(die 안내 스캔·폴더 확인을 워커로, 표본을 슬롯
하나로) 신고가 남았다.  그 경로에는 UI 스레드가 건드리는 파일이 하나도 없다는 것이
계측으로 확인됐으므로(`test_setup_die_hint`
`test_picking_a_folder_touches_no_files_on_the_ui_thread`), 남은 후보는 우리 코드
밖이다 — OS 파일 브라우저 자신, 또는 무거운 import·GPU 워밍업의 GIL 점유.
어느 쪽인지 `app.log` 가 말하게 한다.
"""

from __future__ import annotations

import logging
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget                                  # noqa: E402

from aoi_verification.app.ui import stall_watch                      # noqa: E402


def _pump(app, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()


def test_a_blocked_ui_thread_is_reported(qapp):
    """이벤트 루프가 막히면 그 길이가 호출부로 올라온다."""
    host = QWidget()
    seen: list[tuple[float, str]] = []
    stall_watch.watch(host, lambda s, j: seen.append((s, j)),
                      seconds=0.9, threshold_ms=200.0)
    qapp.processEvents()
    time.sleep(0.6)                           # UI 스레드가 통째로 막힌 상황
    _pump(qapp, 0.3)

    assert seen, "UI 가 0.6초 멈췄는데 아무것도 올라오지 않았다"
    assert seen[0][0] >= 0.4, f"정지시간이 너무 짧게 잡혔다: {seen[0][0]:.2f}초"
    host.deleteLater()


def test_a_responsive_ui_says_nothing(qapp):
    """정상일 때는 아무 일도 일어나지 않는다 — 로그를 채우지 않는다."""
    host = QWidget()
    seen: list[tuple[float, str]] = []
    stall_watch.watch(host, lambda s, j: seen.append((s, j)),
                      seconds=0.6, threshold_ms=200.0)
    _pump(qapp, 0.8)

    assert seen == [], f"멈추지 않았는데 보고했다: {seen}"
    host.deleteLater()


def test_running_jobs_lists_only_known_background_work(qapp):
    """앱이 지어 준 이름만 센다 — pytest·Qt 내부 스레드까지 적지 않는다.

    무거운 import(cv2·openvino)와 GPU 워밍업은 C 확장을 올리는 동안 GIL 을 길게
    쥔다.  UI 가 멈춘 순간 이것들이 살아 있었는지가 곧 단서다."""
    import threading

    done = threading.Event()
    t = threading.Thread(target=done.wait, name="accel-warmup", daemon=True)
    t.start()
    try:
        assert stall_watch.running_jobs() == "accel-warmup"
    finally:
        done.set()
        t.join(2)


def test_a_broken_reporter_does_not_kill_the_app(qapp):
    """진단이 앱을 멈추게 하지 않는다 — 콜백이 터져도 감시는 계속 돈다."""
    host = QWidget()

    def boom(_s, _j):
        raise RuntimeError("보고가 터졌다")

    stall_watch.watch(host, boom, seconds=0.9, threshold_ms=200.0)
    qapp.processEvents()
    time.sleep(0.6)
    _pump(qapp, 0.3)                          # 예외가 새면 여기서 터진다
    host.deleteLater()


def test_watch_stops_by_itself(qapp):
    """감시는 구간 한정 — 창이 사는 내내 도는 타이머를 하나 더 만들지 않는다."""
    host = QWidget()
    timer = stall_watch.watch(host, lambda _s, _j: None, seconds=0.3)
    _pump(qapp, 0.7)
    assert not timer.isActive(), "감시 타이머가 스스로 멈추지 않았다"
    host.deleteLater()


def test_rearming_reuses_the_same_timer(qapp):
    """조작마다 새 타이머를 만들지 않는다 — 멈춘 타이머가 페이지에 쌓인다.

    ⚠ 예전에는 다 쓴 타이머가 `deleteLater` 로 자기를 지웠는데, 부모도 지워지는
    순간 **이중 삭제로 세그폴트**가 났다(실측).  수명은 부모 하나만 쥔다."""
    host = QWidget()
    first = stall_watch.watch(host, lambda _s, _j: None, seconds=0.2)
    _pump(qapp, 0.4)
    second = stall_watch.watch(host, lambda _s, _j: None, seconds=0.2)
    assert second is first, "감시할 때마다 타이머가 새로 생긴다"
    assert second.isActive(), "다시 켜지지 않았다"
    _pump(qapp, 0.4)
    host.deleteLater()
    _pump(qapp, 0.2)                      # 부모가 지워질 때 죽는지 — 여기서 터지면 실패


# ---------------------------------------------------------------------------
# 설정 화면이 실제로 무엇을 남기는가
# ---------------------------------------------------------------------------
def test_setup_page_records_the_stall_with_its_own_workers(qapp, caplog):
    """QThread 는 `threading.enumerate` 에 안 잡힌다 — 페이지가 덧붙여야 한다."""
    from aoi_verification.app.ui.pages.setup_page import SetupPage

    page = SetupPage()
    page._die_scanning_for = "/어딘가/기준폴더"
    with caplog.at_level(logging.WARNING, logger="aoi.ui"):
        page._log_stall(3.25, "backend-import")

    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "3.25초" in msg, f"정지시간이 안 남았다: {msg}"
    assert "backend-import" in msg, f"그때 돌던 스레드가 안 남았다: {msg}"
    assert "die 안내 스캔 진행 중" in msg, f"페이지의 워커가 안 남았다: {msg}"
    page.deleteLater()
