"""시작 지연 기록 — 스플래시가 뜨기까지의 구간이 app.log 한 줄로 남는다.

사용자 PC 에서만 재현되는 시작 지연(보안 검사·DLL 로딩)을 구간별로 가리려는 것이다.
기록 실패가 앱 실행을 막으면 안 되므로, 빈 기록에서도 예외가 나지 않아야 한다."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import main as app_main  # noqa: E402


def test_log_line_has_each_stage_and_total(caplog, monkeypatch):
    monkeypatch.setattr(app_main, "_BOOT", [])
    monkeypatch.setattr(app_main, "_process_age_ms", lambda: 1234.0)
    for name in ("시작", "의존성 확인", "스플래시 표시"):
        app_main._boot_mark(name)
    with caplog.at_level(logging.INFO, logger="aoi.boot"):
        app_main._log_boot_timing("스플래시 표시")
    msg = caplog.records[-1].getMessage()
    assert "[스플래시 표시]" in msg
    assert "의존성 확인 " in msg and "스플래시 표시 " in msg
    assert "합계" in msg and "프로세스 경과 1234" in msg


def test_empty_marks_never_raise(monkeypatch):
    monkeypatch.setattr(app_main, "_BOOT", [])
    app_main._log_boot_timing("x")          # 예외 없이 지나가야 한다


def test_process_age_is_none_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert app_main._process_age_ms() is None
