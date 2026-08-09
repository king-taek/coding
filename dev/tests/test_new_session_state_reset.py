"""C-3 — [새 검증 시작] 이 옛 세션 상태를 남겨 두면 자동 저장이 그걸 되살린다.

``_new_session`` 은 맨 앞에서 ``session_mod.clear()`` 로 이어하기 파일을 지운다.
그런데 ``_input``·``_session_id`` 를 그대로 두면, 30 초마다 도는
``_autosave_timer``(``config.AppConfig.autosave_interval_s``)가 **끝난 검증의
입력 그대로 세션 파일을 다시 쓴다** — 다음 실행이 '이어하기' 로 옛 폴더를 제안한다.
지운 것을 자기가 되살리는 형태라 사용자는 원인을 찾을 수 없다.

``_autosave`` 가 읽는 필드는 ``_input``(mode·ref_root·val_root·호기·threshold)
· ``_session_id`` · ``_phase`` · ``_matches_a`` · 두 페이지의 ``get_state()`` 다.
이 중 ``_phase``·``_matches_a`` 만 정리돼 있었다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.ui import main_window as mw            # noqa: E402
from aoi_verification.app.ui.pages.setup_page import SetupInput  # noqa: E402


class _NoState:
    """페이지 대역 — ``_autosave`` 는 두 페이지의 ``get_state()`` 만 부른다."""

    def get_state(self):
        return None


def _old_input(tmp_path: Path) -> SetupInput:
    return SetupInput(
        mode="single",
        ref_root=tmp_path / "ref_old",
        val_root=tmp_path / "val_old",
        ref_machine="1호기", val_machine="2호기", threshold=0.55,
    )


def _window(monkeypatch):
    # 백엔드 import 는 무겁고 이 테스트와 무관하다(다른 파일들과 같은 처방).
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    win = mw.MainWindow()
    win._select_page = _NoState()
    win._match_page = _NoState()
    return win


def test_new_session_stops_autosave_from_rewriting_the_old_session(
        qapp, monkeypatch, tmp_path):
    win = _window(monkeypatch)
    try:
        saved: list = []
        monkeypatch.setattr(mw.session_mod, "save", lambda st: saved.append(st))

        win._input = _old_input(tmp_path)
        win._session_id = "2026-08-09T09-00-00"
        win._autosave()
        assert len(saved) == 1, "세션 중에는 그대로 저장돼야 한다(가드가 과하면 안 된다)"
        assert saved[0].ref_root == str(tmp_path / "ref_old")

        win._new_session()
        win._autosave()                 # 30 초 타이머가 부르는 바로 그 호출
        assert len(saved) == 1, \
            "새 검증을 시작했는데 자동 저장이 옛 입력으로 세션 파일을 다시 썼다"
    finally:
        win.close()


def test_new_session_drops_the_finished_session_state(qapp, monkeypatch, tmp_path):
    """스캔 결과·작업 파일·세션 id 도 함께 버린다 — 다음 [검증 시작] 이 새로 채운다."""
    win = _window(monkeypatch)
    try:
        monkeypatch.setattr(mw.session_mod, "save", lambda st: None)
        win._input = _old_input(tmp_path)
        win._scan = object()
        win._working_xlsx = tmp_path / "AOI 2호기 검증 (1호기 기준).xlsx"
        win._session_id = "2026-08-09T09-00-00"

        win._new_session()

        assert win._input is None
        assert win._scan is None, "옛 스캔 위에 다음 검증이 얹힌다"
        assert win._working_xlsx is None, "옛 결과 파일로 저장될 수 있다"
        assert win._session_id == ""
    finally:
        win.close()
