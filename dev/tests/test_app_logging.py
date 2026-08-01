"""앱 로그가 **실제로 파일에 남는지** — 안 남으면 로그 코드가 전부 무의미하다.

배경: 이 앱에는 `logging.getLogger("aoi.…")` 호출이 여기저기 있었지만 **핸들러를
붙이는 코드가 어디에도 없었다.**  핸들러가 없으면 파이썬은 최후 수단(stderr,
WARNING 이상)으로 떨어지는데, 배포본은 콘솔 없는 ``pythonw`` 로 뜨므로 그 stderr
마저 사라진다 — 즉 사용자 PC 에서 무슨 일이 있었는지 물어볼 방법이 없었다.

여기서 고정하는 것:
1. `_setup_logging()` 이 캐시 폴더에 로그 파일을 만든다.
2. 기존 `aoi.*` 로거의 기록이 그 파일에 실제로 들어간다(자식 로거 전파).
3. 두 번 불러도 핸들러가 겹치지 않는다(같은 줄이 두 번 찍히지 않게).
4. 설정이 실패해도 예외를 밖으로 내지 않는다 — 진단 보조가 앱 실행을 막으면 안 된다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import main as entry
from aoi_verification.app.utils import paths


@pytest.fixture(autouse=True)
def _clean_aoi_logger():
    """테스트가 붙인 핸들러를 원상복구 — 다른 테스트로 새지 않게."""
    log = logging.getLogger("aoi")
    before, level, prop = list(log.handlers), log.level, log.propagate
    yield
    for h in list(log.handlers):
        if h not in before:
            log.removeHandler(h)
            h.close()
    log.setLevel(level)
    log.propagate = prop


def test_setup_logging_creates_a_log_file(isolated_cache):
    entry._setup_logging()
    log_file = paths.cache_root() / "app.log"
    logging.getLogger("aoi.ui").warning("테스트 기록")
    for h in logging.getLogger("aoi").handlers:
        h.flush()
    assert log_file.exists(), "로그 파일이 만들어지지 않았다 — 로그가 아무 데도 안 남는다"
    assert "테스트 기록" in log_file.read_text(encoding="utf-8")


def test_child_loggers_reach_the_file(isolated_cache):
    """기존 aoi.coords·aoi.openvino 기록도 같은 파일로 모여야 한다."""
    entry._setup_logging()
    logging.getLogger("aoi.coords").info("좌표 쪽 기록")
    logging.getLogger("aoi.openvino").debug("가속 쪽 기록")
    for h in logging.getLogger("aoi").handlers:
        h.flush()
    text = (paths.cache_root() / "app.log").read_text(encoding="utf-8")
    assert "좌표 쪽 기록" in text and "가속 쪽 기록" in text


def test_setup_logging_is_idempotent(isolated_cache):
    """두 번 불러도 핸들러가 겹치지 않는다 — 겹치면 같은 줄이 두 번 찍힌다."""
    entry._setup_logging()
    n = len(logging.getLogger("aoi").handlers)
    entry._setup_logging()
    assert len(logging.getLogger("aoi").handlers) == n


def test_setup_logging_never_raises(isolated_cache, monkeypatch):
    """로그 폴더를 못 만들어도 앱 실행을 막지 않는다."""
    monkeypatch.setattr(paths, "cache_root",
                        lambda: (_ for _ in ()).throw(OSError("디스크 없음")))
    entry._setup_logging()          # 예외가 새어 나오면 실패


def test_autosave_failure_is_logged_not_swallowed():
    """자동 저장 실패는 조용히 사라지면 안 된다 — 이어하기가 통째로 죽는 자리다."""
    src = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
           / "main_window.py").read_text(encoding="utf-8")
    i = src.index("session_mod.save(state)")
    tail = src[i:i + 400]
    assert "except Exception:\n            pass" not in tail, \
        "자동 저장 실패를 다시 조용히 삼킨다"
    assert "_LOG.warning" in tail, "자동 저장 실패가 WARNING 으로 남지 않는다"
