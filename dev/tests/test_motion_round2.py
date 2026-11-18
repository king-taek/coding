"""모션 개선안 2차(F1A·P1A·P2B·P3A·P9A·P11A·P12A·P14B·P15A·P19A·P24A) — 켜졌을 때 그 일을 하고,
끝나면 흔적(유령 라벨·덮개·이펙트)을 남기지 않는지 본다.  Wafer map(P6A·P18A)은 `test_wafer_map.py`.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer, QPoint, Qt                # noqa: E402
from PyQt6.QtGui import QColor, QPixmap                            # noqa: E402
from PyQt6.QtTest import QTest                                     # noqa: E402
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget           # noqa: E402

from aoi_verification.app.ui import motion                         # noqa: E402


@pytest.fixture
def motion_on(monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)


def _spin(qapp, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


def _ghosts(parent: QWidget) -> list:
    return [c for c in parent.findChildren(QLabel) if c.objectName() == "" and c.pixmap()
            and not c.pixmap().isNull()]


# ── 공통: 겹쳐 바꾸기 유령은 끝나면 사라진다 ─────────────────────────────
def test_crossfade_over_ghost_is_removed(styled_qapp, motion_on):
    host = QWidget()
    lay = QVBoxLayout(host)
    target = QLabel("새 모습", host)
    lay.addWidget(target)
    host.resize(200, 80)
    host.show()
    _spin(styled_qapp, 30)
    pm = motion.grab_in_place(target)
    assert not pm.isNull() and pm.size() == target.size()
    before = len(_ghosts(host))
    motion.crossfade_over(target, pm, duration=60)
    assert len(_ghosts(host)) == before + 1
    _spin(styled_qapp, 250)
    QTest.qWait(1)
    styled_qapp.sendPostedEvents(None, 0)
    assert len(_ghosts(host)) == before
    host.deleteLater()


def test_sheet_close_no_longer_shrinks():
    assert motion.SHEET_CLOSE_SCALE == 1.0          # P24A — 내려가며 흐려짐만


# ── P1A: 세그먼트 선택 면 미끄러짐 ────────────────────────────────────
def _group(role):
    from aoi_verification.app.ui.widgets.option_group import OptionGroup
    g = OptionGroup([("a", "가"), ("b", "나")], current="a", role=role, fixed_cols=2)
    g.resize(300, 40)
    g.show()
    return g


def test_segment_slide_runs_and_deletes_itself(styled_qapp, motion_on):
    g = _group("segment")
    _spin(styled_qapp, 30)
    g.set_current_key("b")
    slide = g._slide
    assert slide is not None and slide.isVisible()
    assert g.current_key() == "b"                    # 값은 즉시 바뀐다
    _spin(styled_qapp, motion.DUR_KNOB + 120)
    assert g._slide is None
    assert not g._buttons["a"].isChecked() and g._buttons["b"].isChecked()
    g.deleteLater()


def test_segment_rapid_toggle_keeps_one_overlay(styled_qapp, motion_on):
    from aoi_verification.app.ui.widgets.option_group import _SegmentSlide
    g = _group("segment")
    _spin(styled_qapp, 30)
    for k in ("b", "a", "b", "a"):
        g.set_current_key(k)
        _spin(styled_qapp, 15)
    live = [s for s in g.findChildren(_SegmentSlide) if s.isVisible()]
    assert len(live) <= 1
    _spin(styled_qapp, motion.DUR_KNOB + 150)
    assert g._slide is None and g.current_key() == "a"
    g.deleteLater()


def test_option_role_has_no_slide(styled_qapp, motion_on):
    g = _group("option")
    _spin(styled_qapp, 30)
    g.set_current_key("b")
    assert g._slide is None
    g.deleteLater()


# ── P11A: 대량 선택 타일 배지 ─────────────────────────────────────────
def _tile(tmp_path):
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.widgets.bulk_select_dialog import _SelectTile
    p = tmp_path / "a.png"
    pm = QPixmap(20, 20)
    pm.fill(QColor("#888"))
    pm.save(str(p))
    t = _SelectTile(ImageItem("S1", p, "ref"))
    t.show()
    return t


def test_bulk_tile_click_animates_badge(styled_qapp, motion_on, tmp_path):
    t = _tile(tmp_path)
    _spin(styled_qapp, 20)
    QTest.mouseClick(t, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert t._selected
    assert t._badge._anim.state() == t._badge._anim.State.Running
    _spin(styled_qapp, 400)
    assert t._badge._t == pytest.approx(1.0)
    t.deleteLater()


def test_bulk_tile_set_selected_is_instant(styled_qapp, motion_on, tmp_path):
    t = _tile(tmp_path)
    t.set_selected(True)                              # 전체 선택·끌어 고르기 경로
    assert t._badge._t == 1.0
    assert t._badge._anim.state() != t._badge._anim.State.Running
    t.set_selected(False)
    assert t._badge._t == 0.0
    t.deleteLater()


# ── P15A: 검증 장비 줄 추가·삭제 ──────────────────────────────────────
@pytest.fixture
def setup_page(styled_qapp):
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    page = SetupPage()
    page.resize(1280, 900)
    page.show()
    _spin(styled_qapp, 30)
    yield page
    page.deleteLater()


def test_val_row_add_opens_and_remove_collapses(styled_qapp, motion_on, setup_page):
    page = setup_page
    page._add_val_row()
    rec = page._val_rows[-1]
    host = rec["host"]
    assert getattr(host, "_motionHeightAnim", None) is not None
    _spin(styled_qapp, motion.DUR_ROW_RESIZE + 150)
    assert host.height() > 0 and host.graphicsEffect() is None

    page._remove_val_row(rec)
    page._remove_val_row(rec)                         # 접히는 중 ✕ 연타 — 무시
    assert rec not in page._val_rows
    _spin(styled_qapp, motion.DUR_ROW_SHRINK + 150)
    styled_qapp.sendPostedEvents(None, 52)            # DeferredDelete
    from PyQt6 import sip
    assert sip.isdeleted(host) or host.isHidden()


def test_val_row_remove_without_motion_is_immediate(styled_qapp, setup_page):
    page = setup_page
    page._add_val_row()
    rec = page._val_rows[-1]
    page._remove_val_row(rec)
    assert rec["host"].parent() is None


# ── P3A: 같은 상태면 아무 모션도 없다 ─────────────────────────────────
def test_field_state_motion_only_on_change(styled_qapp, motion_on, setup_page, monkeypatch):
    calls = []
    monkeypatch.setattr(motion, "crossfade_over",
                        lambda w, pm, **kw: calls.append(w))
    edit = setup_page.ref_path_edit
    setup_page._set_field_state(edit, "missing")
    n = len(calls)
    assert n >= 1
    setup_page._set_field_state(edit, "missing")      # 같은 상태 — 겹쳐 바꾸기 없음
    assert len(calls) == n
    setup_page._set_field_state(edit, "")             # 고침 — 입력란 + 옛 문구
    assert len(calls) == n + 2


# ── P14B: 바뀐 칸만 물듦 ──────────────────────────────────────────────
def test_tally_pulse_marks_only_changed_segments(styled_qapp, motion_on, monkeypatch):
    from aoi_verification.app.ui.pages import match_review_page as mrp
    page = mrp.MatchReviewPage()
    page.resize(1280, 800)
    page.show()
    _spin(styled_qapp, 20)
    got = {}
    monkeypatch.setattr(mrp, "_rich_spans",
                        lambda lbl, parts, sep, changed: got.setdefault("c", changed) or [])
    parts, sep = ["a", "b"], " "
    page._pulse_changed_tally([("ok", 3), ("over", 1)], parts, sep)   # 처음 — 없음
    assert "c" not in got
    page._pulse_changed_tally([("ok", 3), ("over", 1)], parts, sep)   # 같은 값 — 없음
    assert "c" not in got
    page._pulse_changed_tally([("ok", 4), ("over", 1)], parts, sep)
    assert got["c"] == {0}
    page.deleteLater()


# ── F1A: 스냅샷 준비가 행 올라오기를 소모한다 ─────────────────────────
def test_prepare_snapshot_consumes_entrance(styled_qapp, tmp_path):
    from aoi_verification.app.ui.pages.match_review_page import MatchReviewPage
    page = MatchReviewPage()
    dummy = QWidget()
    dummy.hide()
    page._rows = [dummy]
    page._pending_rows = []
    page._entrance_pending = True
    page.prepare_snapshot()
    assert page._entrance_pending is False

    page._entrance_pending = True
    page._pending_rows = [object()]                   # 나눠 만드는 중 — 올라오기 유지
    page.prepare_snapshot()
    assert page._entrance_pending is True
    page._rows = []
    page._pending_rows = []
    dummy.deleteLater()
    page.deleteLater()
