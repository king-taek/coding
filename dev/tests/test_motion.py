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


def test_motion_is_always_on_except_headless(qapp, monkeypatch):
    """★ 모션은 **항상 켜진다**(사용자 결정) — 유일한 예외가 헤드리스다.

    한때 '모션 줄이기' 토글과 OS '동작 줄이기' 감지가 `enabled()` 를 껐다.  둘 다
    제거했지만 **offscreen 게이트는 남긴다**: 사용자 설정이 아니라 테스트·캡처의
    결정성이고, 크래시 회귀 테스트(`test_anim_lifetime`)가 이 게이트를 뒤집어
    애니메이션 경로를 재현한다."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert motion.enabled() is True
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert motion.enabled() is False


def test_dur_scales_with_profile(qapp):
    from aoi_verification.app.ui import theme
    # '도면' 은 담백한 모션(scale 0.8) — 지속시간이 그만큼 짧아진다.
    assert motion.dur(200) == int(200 * theme.PROFILE.motion_scale)


def test_transition_in_commits_immediately_when_disabled(qapp):
    """헤드리스면 진입 애니 없이 즉시 on_commit(스택 전환) — 결정론."""
    from PyQt6.QtWidgets import QWidget
    from PyQt6.QtGui import QPixmap
    container = QWidget()
    pix = QPixmap(10, 10)
    pix.fill()
    done = {"v": False}
    motion.transition_in(container, pix,
                         on_commit=lambda: done.__setitem__("v", True))
    assert done["v"] is True
    container.deleteLater()


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
