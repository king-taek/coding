"""후보 선별을 **한 장도 고르지 않고** 끝냈을 때 되돌릴 기회를 준다.

신고(UI 관련 PDF ⑦): "후보 선별 단계에서 고른 사진이 0장일 때에는 설정 화면으로
돌아갈 거냐고 물어보도록 해줘."

★ **막지는 않는다.**  '전부 제외' 도 유효한 결정일 수 있다(그 폴더에 검증할 것이
없다는 결론).  다만 그대로 넘어가면 매칭할 사진이 0장이라 다음 화면이 통째로 비므로,
그 사실을 말하고 한 번 묻는다.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QMessageBox                          # noqa: E402

from aoi_verification.app import i18n                            # noqa: E402
from aoi_verification.app.models.slot import ImageItem           # noqa: E402
from aoi_verification.app.ui import main_window as mw            # noqa: E402


class _State:
    """`SelectPage.get_state()` 대역 — `_on_select_finished` 는 이 둘만 읽는다."""

    def __init__(self, targets, excluded):
        self.targets = targets
        self.excluded = excluded


class _Page:
    def __init__(self, targets, excluded):
        self._state = _State(targets, excluded)

    def get_state(self):
        return self._state


def _window(monkeypatch, targets, excluded):
    # 백엔드 import 는 무겁고 이 테스트와 무관하다(다른 파일들과 같은 처방).
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    win = mw.MainWindow()
    win._select_page = _Page(targets, excluded)
    win._phase = mw.PHASE_A_SELECT
    return win


def _item(slot, i):
    from pathlib import Path
    return ImageItem(slot=slot, path=Path(f"/tmp/{slot}_{i}.jpg"), side="ref")


def _wire(monkeypatch, answer):
    """물음의 답을 정해 두고, 다음 단계로 갔는지/설정으로 돌아갔는지 기록한다."""
    asked: list = []
    went: list = []
    monkeypatch.setattr(mw.sheets, "ask",
                        lambda *a, **k: (asked.append(a), answer)[1])
    monkeypatch.setattr(mw.MainWindow, "_enter_stage2_phase_a",
                        lambda self: went.append("stage2"))
    monkeypatch.setattr(mw.MainWindow, "_on_select_cancelled",
                        lambda self: went.append("setup"))
    return asked, went


def test_zero_chosen_offers_to_go_back(qapp, monkeypatch):
    """0장이면 묻고, [예] 면 설정 화면으로 돌아간다."""
    win = _window(monkeypatch, targets={}, excluded={"S1": [_item("S1", 0)]})
    try:
        asked, went = _wire(monkeypatch, QMessageBox.StandardButton.Yes)
        win._on_select_finished()
        assert asked, "0장인데 아무것도 묻지 않았다"
        assert i18n.KO.SELECT_NONE_CHOSEN_TITLE in asked[0]
        assert went == ["setup"], f"설정으로 돌아가지 않았다: {went}"
    finally:
        win.close()


def test_zero_chosen_can_still_proceed(qapp, monkeypatch):
    """★ 막지 않는다 — [아니오] 면 그대로 다음 단계로 간다."""
    win = _window(monkeypatch, targets={}, excluded={"S1": [_item("S1", 0)]})
    try:
        asked, went = _wire(monkeypatch, QMessageBox.StandardButton.No)
        win._on_select_finished()
        assert asked, "묻지 않았다"
        assert went == ["stage2"], f"진행을 막았다: {went}"
    finally:
        win.close()


def test_something_chosen_asks_nothing(qapp, monkeypatch):
    """한 장이라도 골랐으면 묻지 않는다 — 정상 흐름에 클릭을 더하지 않는다."""
    win = _window(monkeypatch,
                  targets={"S1": [_item("S1", 0)]}, excluded={})
    try:
        asked, went = _wire(monkeypatch, QMessageBox.StandardButton.Yes)
        win._on_select_finished()
        assert asked == [], "고른 사진이 있는데 물었다"
        assert went == ["stage2"]
    finally:
        win.close()


def test_empty_slot_lists_count_as_zero(qapp, monkeypatch):
    """슬롯 키만 있고 목록이 빈 경우도 '0장' 이다 — `{'S1': []}` 은 참인 dict 다."""
    win = _window(monkeypatch, targets={"S1": [], "S2": []}, excluded={})
    try:
        asked, went = _wire(monkeypatch, QMessageBox.StandardButton.Yes)
        win._on_select_finished()
        assert asked, "빈 목록만 있는데 0장으로 보지 않았다"
        assert went == ["setup"]
    finally:
        win.close()


def test_the_empty_choice_is_not_remembered_as_a_reusable_selection(
        qapp, monkeypatch):
    """되돌아갈 때 빈 선택이 '다음에 재사용할 기준' 으로 남으면 안 된다.

    기록(`_save_ref_selection`)은 물음 **뒤에** 와야 한다."""
    win = _window(monkeypatch, targets={}, excluded={})
    try:
        saved: list = []
        monkeypatch.setattr(mw.MainWindow, "_save_ref_selection",
                            lambda self, t: saved.append(t))
        _asked, _went = _wire(monkeypatch, QMessageBox.StandardButton.Yes)
        win._on_select_finished()
        assert saved == [], "설정으로 돌아가는데 빈 선택을 기록했다"
    finally:
        win.close()
