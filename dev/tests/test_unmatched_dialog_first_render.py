"""매치 실패 검토 팝업 — 창이 **먼저 뜨고** 그 다음에 점수 계산이 돈다.

증상: '매치 실패 사진 검토' 를 처음 누르면 팝업이 안 뜨고 OS 로딩 커서만 돌았다.
원인은 생성자 마지막 줄이 `_render_current()` 를 동기로 불렀던 것 — 그 안에서 원본
풀 디코드 + 후보 점수 계산(캐시 miss 면 최대 299쌍의 extract+score)을 GUI 스레드에서
수행하는데, 생성자는 `sheets.run(dlg)` 이 위젯을 show() 하기 **전에** 끝나야 한다.
그래서 그려질 화면이 없어 안에서 부르는 LoadingOverlay 도 무용지물이었다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication                     # noqa: E402

from aoi_verification.app.models.result import MissEntry      # noqa: E402
from aoi_verification.app.models.slot import ImageItem        # noqa: E402
from aoi_verification.app.ui import theme                     # noqa: E402
from aoi_verification.app.ui.widgets import (                 # noqa: E402
    unmatched_review_dialog as ur)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply_to_app(app)
    return app


@pytest.fixture
def spy_render(monkeypatch):
    """`_render_current` 를 기록만 하는 스텁으로 — 실제 디코드/채점은 돌리지 않는다."""
    calls: list[str] = []
    monkeypatch.setattr(ur.UnmatchedReviewDialog, "_render_current",
                        lambda self: calls.append("render"), raising=True)
    return calls


def _dialog() -> ur.UnmatchedReviewDialog:
    miss = [MissEntry(slot="A", side="ref", path=Path("/tmp/m.png"))]
    pool = {"A": [ImageItem("A", Path("/tmp/v1.png"), "val")]}
    return ur.UnmatchedReviewDialog(
        unmatched=miss, val_pool=pool, already_used_vals=set(),
        score_cache=None, fast_results=None, parent=None,
        coord_mode=False, tolerance=500.0,
    )


def test_constructor_does_not_render(qapp, spy_render):
    """생성자는 무거운 첫 렌더를 하지 않는다 — 그래야 팝업이 먼저 뜬다."""
    dlg = _dialog()
    try:
        assert spy_render == [], "생성자에서 렌더가 돌면 팝업이 뜨기 전에 멈춘다"
    finally:
        dlg.close()


def test_first_render_runs_after_show(qapp, spy_render):
    """표시된 뒤 한 틱 안에 첫 렌더가 돈다(로딩 오버레이가 보이는 상태에서)."""
    dlg = _dialog()
    try:
        dlg.show()
        assert spy_render == []            # show 직후에도 아직 — 한 틱 뒤로 미룸
        qapp.processEvents()
        assert spy_render == ["render"]
    finally:
        dlg.close()


def test_first_render_happens_once(qapp, spy_render):
    """여러 번 show 돼도 첫 렌더는 한 번뿐(사진 이동은 별도 경로)."""
    dlg = _dialog()
    try:
        dlg.show()
        qapp.processEvents()
        dlg.hide()
        dlg.show()
        qapp.processEvents()
        assert spy_render == ["render"]
    finally:
        dlg.close()


def test_deferred_render_skipped_when_closed_early(qapp, spy_render):
    """지연 렌더가 도착하기 전에 닫히면 조용히 포기한다(죽은 객체 호출 방지)."""
    dlg = _dialog()
    dlg.show()
    dlg.close()
    qapp.processEvents()
    assert spy_render == []


def test_loading_overlay_exists_before_render(qapp, spy_render):
    """오버레이는 생성자에서 준비돼 있어야 첫 렌더가 곧바로 쓸 수 있다."""
    dlg = _dialog()
    try:
        assert dlg._loading is not None
    finally:
        dlg.close()
