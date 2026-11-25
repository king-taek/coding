"""GL 알림 겹침(M3) · GL 확대 애니메이션(M5) · 알림 배너 펼침(M7)."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtCore import QPointF                                    # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QFrame            # noqa: E402

from aoi_verification.app.ui import motion                          # noqa: E402
from aoi_verification.app.ui.widgets.gl_canvas import GlCanvas      # noqa: E402
import numpy as np                                                  # noqa: E402


def _wait(ms: int) -> None:
    import time
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def test_toast_overlap_keeps_later_error(styled_qapp, monkeypatch):
    """A(4초) 뒤 1초에 오류 B(6초) — A 의 예약이 B 를 일찍 지우지 않는다(타이머 하나·재시작)."""
    from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog
    d = GlViewerDialog(None)
    d._toast("A")
    t1 = d.toast._motionToastTimer
    d._toast("B", error=True)
    assert d.toast._motionToastTimer is t1          # 라벨 소유 타이머 하나를 재시작
    assert t1.interval() == 6000 and t1.isActive()
    assert d.toast.text() == "B"
    d.close()


def _canvas(styled_qapp, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    c = GlCanvas()
    c.resize(400, 300)
    c.show()
    c.set_image(np.zeros((100, 200), np.uint16), np.zeros((100, 200, 3), np.uint8))
    QApplication.processEvents()
    return c


def test_wheel_zoom_animates_and_accumulates_on_target(styled_qapp, monkeypatch):
    c = _canvas(styled_qapp, monkeypatch)
    a = QPointF(120, 80)
    ix, iy = c.to_image(a.x(), a.y())
    s0 = c.scale()
    c.zoom_by(2.0, a)
    assert c.scale() == pytest.approx(s0)           # 아직 첫 프레임 — 순간이동 아님
    c.zoom_by(2.0, a)                               # 도는 중 또 휠 → 목표에 쌓임(4배)
    _wait(60)
    mid = c.scale()
    assert s0 < mid < s0 * 4
    # 도는 중에도 커서 아래 이미지 점은 그대로(좌표 변환이 맞다)
    x, y = c.to_image(a.x(), a.y())
    assert (x, y) == pytest.approx((ix, iy), abs=1e-6)
    _wait(400)
    assert c.scale() == pytest.approx(s0 * 4)
    x, y = c.to_image(a.x(), a.y())
    assert (x, y) == pytest.approx((ix, iy), abs=1e-6)


def test_zoom_to_box_animates_then_lands_exactly(styled_qapp, monkeypatch):
    c = _canvas(styled_qapp, monkeypatch)
    c.zoom_to_box((50, 20, 40, 30))
    s_start = c.scale()
    _wait(100)
    assert c.scale() != pytest.approx(s_start)
    _wait(500)
    monkeypatch.setattr(motion, "enabled", lambda: False)
    ref = GlCanvas()
    ref.resize(400, 300)
    ref.set_image(np.zeros((100, 200), np.uint16), np.zeros((100, 200, 3), np.uint8))
    ref.zoom_to_box((50, 20, 40, 30))
    assert c.view_state() == pytest.approx(ref.view_state())


def test_zoom_instant_when_motion_off(styled_qapp, monkeypatch):
    c = _canvas(styled_qapp, monkeypatch)
    monkeypatch.setattr(motion, "enabled", lambda: False)
    s0 = c.scale()
    c.zoom_by(2.0, QPointF(10, 10))
    assert c.scale() == pytest.approx(s0 * 2)


def test_banner_in_out_roundtrip(styled_qapp, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    host = QFrame()
    from PyQt6.QtWidgets import QVBoxLayout
    lay = QVBoxLayout(host)
    banner = QLabel("알림")
    banner.setVisible(False)
    lay.addWidget(banner)
    lay.addStretch(1)
    host.show()
    motion.banner_in(banner)
    assert banner.isVisible()
    _wait(400)
    assert banner.height() >= banner.sizeHint().height() - 1
    motion.banner_out(banner)
    assert banner.isVisible()                       # 접히는 중
    _wait(400)
    assert not banner.isVisible()
    # 접히는 중 새 알림이 오면 숨기지 않는다
    motion.banner_in(banner)
    _wait(300)
    motion.banner_out(banner)
    _wait(40)
    motion.banner_in(banner)
    _wait(400)
    assert banner.isVisible()
    host.close()
