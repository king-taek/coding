"""KLA 장비 확인 — 그림 카드 넷 + 왼쪽 아래 [취소] (현택님 2026-11-24, K1 A안).

- 넷(기준/검증/둘 다/KLA 아님)을 모두 유지한다(CLAUDE.md 규칙).  대등하다 — 강조·기본값 없음.
- 카드를 누르면 바로 확정.  Enter 는 아무것도 누르지 않는다.
- [취소]·Esc 는 **검증 시작을 멈춘다** — 'KLA 아님'(None, 그냥 진행)과 다르다.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt                                          # noqa: E402
from PyQt6.QtTest import QTest                                       # noqa: E402
from PyQt6.QtWidgets import QDialog, QLabel                          # noqa: E402

from aoi_verification.app import i18n                                # noqa: E402
from aoi_verification.app.ui.widgets import kla_side_dialog as kd    # noqa: E402


@pytest.fixture
def dlg(styled_qapp):
    d = kd.KlaSideDialog()
    d.resize(680, 260)
    d.show()
    for _ in range(3):
        styled_qapp.processEvents()
    yield d
    d.hide()
    d.deleteLater()


def test_four_equal_picture_cards(dlg):
    K = i18n.KO
    assert list(dlg.cards) == ["ref", "val", "both", None]
    assert [c.label() for c in dlg.cards.values()] == \
        [K.KLA_SIDE_REF, K.KLA_SIDE_VAL, K.KLA_SIDE_BOTH, K.KLA_SIDE_NONE]
    for c in dlg.cards.values():
        assert c.property("role") == "option"
        assert not c.isDefault() and not c.autoDefault()
        assert c.accessibleName() == c.label()
        assert c.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_no_explanation_text(dlg):
    texts = [lb.text() for lb in dlg.findChildren(QLabel) if lb.text()]
    assert texts == [i18n.KO.KLA_ASK_TITLE]


@pytest.mark.parametrize("key", ["ref", "val", "both", None])
def test_one_click_confirms(dlg, key):
    dlg.cards[key].click()
    assert dlg.answer == key
    assert dlg.result() == QDialog.DialogCode.Accepted


@pytest.mark.parametrize("how", ["button", "esc"])
def test_cancel_and_escape_mean_stop(dlg, how):
    if how == "button":
        dlg.cancel_btn.click()
    else:
        QTest.keyClick(dlg, Qt.Key.Key_Escape)
    assert dlg.answer == kd.CANCEL
    assert dlg.answer is not None, "취소가 'KLA 아님' 과 같아졌다"
    assert dlg.result() == QDialog.DialogCode.Rejected


def test_enter_presses_nothing(dlg):
    dlg.cards["ref"].setFocus()
    QTest.keyClick(dlg.cards["ref"], Qt.Key.Key_Return)
    QTest.keyClick(dlg, Qt.Key.Key_Enter)
    assert dlg.isVisible() and dlg.answer == kd.CANCEL


def test_cancel_is_bottom_left(dlg):
    cancel = dlg.cancel_btn
    first = dlg.cards["ref"]
    assert cancel.x() <= first.x() + 2, "[취소] 가 왼쪽에 있지 않다"
    assert cancel.y() > first.y() + first.height(), "[취소] 가 카드 아래가 아니다"


# ── 메인 창: [취소] 는 시작을 멈추고, KLA 아님은 진행한다 ─────────────────────
def _scan_done_with(answer):
    from aoi_verification.app.ui import main_window as mw
    calls = []
    sr = SimpleNamespace(slots={}, ref_only=["a"], val_only=["b"])
    me = SimpleNamespace(
        _scan_token=1, _scan_worker=None, _stage="scan", _scan=None,
        _stage_clock=lambda *a, **k: None,
        _input=SimpleNamespace(), _is_extract=lambda: False,
        _kla_machine_side=lambda inp: None,
        _ask_kla_side=lambda: answer,
        _cancel_scan=lambda: calls.append("cancel"),
        _resolve_and_merge_kla=lambda *a, **k: calls.append("kla"),
        _after_slot_resolved=lambda s: calls.append("next"))
    mw.MainWindow._on_scan_done(me, 1, sr)
    return calls


@pytest.mark.parametrize("answer,want", [
    (kd.CANCEL, ["cancel"]),
    (None, ["next"]),
    ("ref", ["kla"]),
])
def test_main_window_cancel_stops_but_not_kla_goes_on(styled_qapp, answer, want):
    assert _scan_done_with(answer) == want


def test_main_window_asks_with_the_card_dialog(styled_qapp, monkeypatch):
    from aoi_verification.app.ui import main_window as mw
    seen = []

    def fake_run(d, **_k):
        seen.append(type(d).__name__)
        d.cards["val"].click()
        return d.result()

    monkeypatch.setattr(mw.sheets, "run", fake_run)
    assert mw.MainWindow._ask_kla_side(None) == "val"
    assert seen == ["KlaSideDialog"]
