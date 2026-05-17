"""Stage 1 SelectPage — ‘선택 종료’ 버튼 동작 검증 (#1).

큐에 남은 미결정 사진을 모두 excluded 로 옮기고 finished 시그널을 발생.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox          # noqa: E402

from aoi_verification.app.models.slot import ImageItem          # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _items(slot: str, n: int) -> list[ImageItem]:
    return [ImageItem(slot=slot, path=Path(f"/tmp/{slot}_{i}.jpg"), side="ref")
            for i in range(n)]


def test_end_selection_moves_queue_to_excluded(qapp, isolated_cache, monkeypatch):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    queue = _items("S1", 3) + _items("S2", 2)
    sp.load_state(queue=queue)

    # 첫 사진이 _current 로 빠지므로 큐엔 4 개만 남는 게 정상.
    initial_remaining = len(sp._state.queue)
    # 모달은 자동 Yes.
    monkeypatch.setattr(QMessageBox, "question",
                         lambda *a, **kw: QMessageBox.StandardButton.Yes)

    finished_emits = []
    sp.finished.connect(lambda: finished_emits.append(True))

    sp._end_selection_now()

    # 큐가 비어야 하고, excluded 에 모든 항목이 슬롯별로 들어가야.
    assert sp._state.queue == []
    excluded_total = sum(len(v) for v in sp._state.excluded.values())
    # _current 1 장 + 큐 4 장 = 5 장.  단, _current 는 큐에서 빠진 뒤 다시
    # 큐 head 가 되지 않으므로 _end_selection_now 는 큐에 남은 것만 처리.
    # _advance_to_next 가 큐 비었음을 보고 finished 발사.
    assert excluded_total == initial_remaining
    assert len(finished_emits) == 1


def test_end_selection_no_op_when_queue_empty(qapp, isolated_cache, monkeypatch):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    sp.load_state(queue=[])

    finished_emits = []
    sp.finished.connect(lambda: finished_emits.append(True))

    # 모달이 뜨면 안 됨 (큐 비었음 가드).
    called = []
    monkeypatch.setattr(QMessageBox, "question",
                         lambda *a, **kw: called.append(True) or
                                          QMessageBox.StandardButton.Yes)

    sp._end_selection_now()
    assert called == [], "큐 비었으면 확인 모달도 띄우지 않아야 함"


def test_end_selection_cancel_does_nothing(qapp, isolated_cache, monkeypatch):
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()
    queue = _items("S1", 2)
    sp.load_state(queue=queue)

    pre_queue_len = len(sp._state.queue)
    pre_excluded = sum(len(v) for v in sp._state.excluded.values())

    monkeypatch.setattr(QMessageBox, "question",
                         lambda *a, **kw: QMessageBox.StandardButton.No)
    sp._end_selection_now()

    assert len(sp._state.queue) == pre_queue_len, "취소 시 큐 변경 없어야"
    assert sum(len(v) for v in sp._state.excluded.values()) == pre_excluded


def test_end_selection_button_enabled_state(qapp, isolated_cache):
    """버튼 활성/비활성 — 큐에 사진 있으면 활성, 없으면 비활성."""
    from aoi_verification.app.ui.pages.select_page import SelectPage
    sp = SelectPage()

    # 큐 비어있을 때 — 비활성.
    sp.load_state(queue=[])
    assert sp.btn_end_selection.isEnabled() is False

    # 큐에 사진 있을 때 — 활성.
    sp.load_state(queue=_items("S1", 3))
    assert sp.btn_end_selection.isEnabled() is True
