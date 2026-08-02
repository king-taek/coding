"""색 모드 전환 — 창 **전체**가 함께 바뀐다 + 스크롤 위치를 잃지 않는다.

사용자 지적 두 건:
1. "하단바가 아직 다크모드가 아닌 상태로 잠깐 나옵니다."
   원인: 크로스페이드가 ``self._stack`` 만 덮었다.  상태바는 스택 밖이라 QSS 재적용
   즉시 새 색이 됐고, 전환 700ms 내내 본문과 다른 모드로 서 있었다.
   실측(고치기 전, 전환 0ms): 상태바 (28,26,21) / 본문 (236,233,226).
2. "다크모드 전환할 때 스크롤이 최상단으로 옮겨갑니다."
   원인: 전환이 페이지를 **버리고 다시 만든다**(구워진 색을 갈아 끼우려고).
   스크롤 위치는 그 과정에서 사라졌다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inspect

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QElapsedTimer                          # noqa: E402
from PyQt6.QtWidgets import QLabel                              # noqa: E402

from aoi_verification.app.ui import main_window as mw           # noqa: E402
from aoi_verification.app.ui import motion, theme               # noqa: E402


@pytest.fixture
def motion_on(monkeypatch):
    """헤드리스에서도 실제 애니메이션 경로를 태운다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)


@pytest.fixture
def window(styled_qapp):
    w = mw.MainWindow()
    w.resize(900, 700)
    w.show()
    for _ in range(20):
        styled_qapp.processEvents()
    yield w
    w.close()
    w.deleteLater()
    styled_qapp.processEvents()
    theme.set_color_mode("light")


def _spin(qapp, ms: int) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents()


# ── 1) 창 전체가 함께 바뀐다 ──────────────────────────────────────────────
def test_overlay_covers_the_whole_window_not_just_the_pages(
        styled_qapp, window, motion_on):
    """★ 오버레이가 상태바까지 덮는다 — 스택만 덮으면 하단바만 혼자 바뀐다."""
    w = window
    w._on_appearance_changed("dark")
    try:
        overlay = w.findChild(QLabel, "_pageRecolorOverlay")
        assert overlay is not None, "크로스페이드 오버레이가 없다"
        assert overlay.parent() is w, \
            "오버레이가 창의 자식이 아니다 — 스택 밖(상태바)이 안 덮인다"

        bar = w._status_bar.geometry()
        rect = overlay.geometry()
        assert rect.contains(bar), (
            f"오버레이 {rect} 가 상태바 {bar} 를 덮지 못한다 — "
            "전환 중 하단바만 다른 색으로 보인다")
    finally:
        _spin(styled_qapp, motion.DUR_RECOLOR + 250)


def test_status_bar_matches_the_content_mid_transition(
        styled_qapp, window, motion_on):
    """전환 도중 어느 시점에도 하단바와 본문이 **같은 색**이어야 한다."""
    w = window
    w._on_appearance_changed("dark")
    try:
        for _ in range(3):
            _spin(styled_qapp, 90)
            img = w.grab().toImage()
            bar = w._status_bar.geometry()
            here = img.pixelColor(img.width() // 2,
                                  bar.y() + bar.height() // 2)
            body = img.pixelColor(img.width() // 2, img.height() // 3)
            assert abs(here.red() - body.red()) <= 24, (
                f"하단바({here.red()})와 본문({body.red()})이 따로 논다")
    finally:
        _spin(styled_qapp, motion.DUR_RECOLOR + 250)


def test_snapshot_is_taken_from_the_window():
    """소스 계약 — 다시 `self._stack.grab()` 으로 돌아가면 버그가 재발한다."""
    src = inspect.getsource(mw.MainWindow._on_appearance_changed)
    assert "self.grab()" in src
    assert "self._stack.grab()" not in src


# ── 2) 스크롤 위치 보존 ───────────────────────────────────────────────────
def test_scroll_position_survives_the_transition(styled_qapp, window,
                                                 motion_on):
    """어두운 화면을 켜도 보고 있던 자리에 그대로 있어야 한다."""
    w = window
    bar = w._setup_page._scroll.verticalScrollBar()
    if bar.maximum() <= 0:
        pytest.skip("이 창 크기에서는 셋업 페이지에 스크롤이 없다")
    target = bar.maximum() // 2
    bar.setValue(target)
    assert bar.value() == target

    w._on_appearance_changed("dark")
    _spin(styled_qapp, motion.DUR_RECOLOR + 250)

    new_bar = w._setup_page._scroll.verticalScrollBar()
    assert new_bar is not bar, "페이지가 다시 만들어지지 않았다(전제가 깨졌다)"
    assert new_bar.value() == pytest.approx(target, abs=2), \
        f"스크롤이 {new_bar.value()} 로 튀었다(원래 {target})"


def test_draft_carries_the_scroll_offset(styled_qapp, window):
    """``capture_draft``/``restore_draft`` 계약 — 복원 경로가 여기 하나뿐이다."""
    page = window._setup_page
    bar = page._scroll.verticalScrollBar()
    if bar.maximum() <= 0:
        pytest.skip("이 창 크기에서는 셋업 페이지에 스크롤이 없다")
    bar.setValue(bar.maximum())
    draft = page.capture_draft()
    assert draft["scroll_y"] == bar.maximum()


def test_restore_uses_a_parented_timer():
    """★ 정적 ``QTimer.singleShot`` 은 쓰지 않는다.

    정적 타이머는 페이지의 자식이 아니라, 전환 도중 페이지가 다시 파괴되면 죽은
    위젯으로 콜백이 들어간다(모듈 관습: 세그폴트 전례)."""
    from aoi_verification.app.ui.pages import setup_page

    fn = setup_page.SetupPage._restore_scroll
    # ★ 독스트링을 빼고 **코드만** 본다 — 그 함정을 경고하는 문장이 독스트링에 있어서
    #   원문 전체를 검사하면 경고문 자체에 걸려 가드가 거짓 실패한다.
    code = inspect.getsource(fn).replace(fn.__doc__ or "", "")
    assert "QTimer(self)" in code
    assert "QTimer.singleShot" not in code
