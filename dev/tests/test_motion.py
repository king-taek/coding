"""모션 시스템 — 헤드리스/모션줄이기 시 즉시 적용(결정론) + 로딩 오버레이 진리표."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QScrollBar          # noqa: E402

from aoi_verification.app.ui import motion                    # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_enabled_false_when_offscreen(qapp):
    # 헤드리스(offscreen)에서는 항상 즉시 적용.
    assert motion.enabled() is False


def test_animate_scroll_instant_when_disabled(qapp):
    bar = QScrollBar()
    bar.setRange(0, 1000)
    motion.animate_scroll(bar, 500)
    assert bar.value() == 500           # 애니메이션 없이 즉시
    motion.animate_scroll(bar, 99999)   # 범위 클램프
    assert bar.value() == 1000


def test_reduce_motion_flag(qapp, monkeypatch):
    # offscreen 이 아닌 척해도 reduce_motion 이면 꺼짐.
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    motion.set_reduce_motion(True)
    try:
        assert motion.enabled() is False
        motion.set_reduce_motion(False)
        assert motion.enabled() is True
    finally:
        motion.set_reduce_motion(False)


def test_dur_scales_with_profile(qapp):
    from aoi_verification.app.ui import theme
    theme.set_variant("instrument")
    assert motion.dur(200) == 200       # instrument motion_scale 1.0


def test_loading_overlay_instant_when_disabled(qapp):
    from aoi_verification.app.ui.widgets.loading_overlay import LoadingOverlay
    from PyQt6.QtWidgets import QWidget
    host = QWidget()
    ov = LoadingOverlay(host)
    ov.show_overlay("작업 중")
    assert ov._fade == 1.0              # 즉시 완전 표시
    assert not ov.isHidden()           # show() 호출됨
    ov.set_progress(0, 0, "탐색")        # busy
    assert not ov._busy.isHidden() and ov._progress.isHidden()
    ov.set_progress(5, 10, "처리")       # 결정형
    assert not ov._progress.isHidden() and ov._progress.maximum() == 10
    ov.hide_overlay()
    assert ov.isHidden()               # 즉시 숨김
    ov.deleteLater()
    host.deleteLater()
