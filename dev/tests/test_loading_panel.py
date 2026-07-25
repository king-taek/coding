"""로딩 오버레이 — 반투명 스크림 + 패널이 아래에서 중앙으로 안착(사용자 요청).

CLAUDE.md 로딩 계약(set_progress 의미·결정형/busy)은 그대로 유지되는지도 함께 지킨다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QWidget          # noqa: E402

from aoi_verification.app.ui import theme                    # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (  # noqa: E402
    LoadingOverlay)

_QSS = (Path(__file__).resolve().parents[2] / "aoi_verification" / "app" / "ui"
        / "style.qss").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _overlay(qapp, w=900, h=600):
    host = QWidget()
    host.resize(w, h)
    host.show()
    ov = LoadingOverlay(host)
    for _ in range(4):
        qapp.processEvents()
    return host, ov


def test_panel_settles_at_center_and_starts_below(qapp):
    """t=1 이면 중앙, t<1 이면 중앙보다 **아래** — '아래에서 올라와 안착'."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        for _ in range(6):
            qapp.processEvents()
        def dy():
            g = ov._panel.geometry()
            return g.y() + g.height() // 2 - ov.height() // 2
        ov._on_fade(1.0)
        assert abs(dy()) <= 2, f"안착 위치가 중앙이 아니다: dy={dy()}"
        ov._on_fade(0.0)
        assert dy() > 0, "시작 위치가 중앙보다 아래여야 한다"
        assert dy() <= ov.RISE_IN_PX + 2
        ov._on_fade(0.5)
        mid = dy()
        assert 0 < mid < ov.RISE_IN_PX, f"중간 프레임이 사이에 없다: {mid}"
    finally:
        host.deleteLater()


def test_exit_travels_less_than_entry(qapp):
    """퇴장은 살짝만 내려간다(입장보다 짧고 얕게)."""
    assert LoadingOverlay.RISE_OUT_PX < LoadingOverlay.RISE_IN_PX


def test_scrim_lets_the_page_show_through(qapp):
    """스크림이 화면을 '전부 가리지' 않아야 한다 — 완전 불투명 금지."""
    for mode in theme.color_mode_keys():
        theme.set_color_mode(mode)
        assert theme.SCRIM_RGBA[3] < 255
        assert theme.SCRIM_RGBA[3] <= 130, f"{mode}: 너무 진하다"
    theme.set_color_mode("light")


def test_panel_has_own_surface_for_readability(qapp):
    """스크림이 옅어도 읽히도록 패널은 자기 면을 갖는다(QSS role)."""
    host, ov = _overlay(qapp)
    try:
        assert ov._panel.property("role") == "loadingPanel"
        assert 'QWidget[role="loadingPanel"]' in _QSS
        out = theme.render_qss(_QSS)
        assert "$" not in out
    finally:
        host.deleteLater()


def test_no_nested_graphics_effect(qapp):
    """패널 이펙트 안에 또 이펙트를 겹치면 QPainter 충돌 경고가 난다 — 금지."""
    host, ov = _overlay(qapp)
    try:
        assert ov._panel.graphicsEffect() is not None
        assert ov._bar_host.graphicsEffect() is None
    finally:
        host.deleteLater()


def test_bar_slide_keeps_panel_height_stable(qapp):
    """진행바 스태거는 위/아래 여백을 맞바꿔 패널 크기를 흔들지 않는다."""
    host, ov = _overlay(qapp)
    try:
        ov._set_bar_slide(0.0)
        m0 = ov._bar_lay.contentsMargins()
        ov._set_bar_slide(1.0)
        m1 = ov._bar_lay.contentsMargins()
        assert m0.top() + m0.bottom() == m1.top() + m1.bottom()
        assert m0.top() > m1.top()          # 아래에서 밀려 올라온다
    finally:
        host.deleteLater()


def test_headless_snaps_to_final_state(qapp):
    """오프스크린/모션 줄이기 면 모션 없이 즉시 최종 상태(테스트·캡처 결정론)."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        assert ov._fade == 1.0
        m = ov._bar_lay.contentsMargins()
        assert m.top() == 0                  # 슬라이드가 끝난 상태
        ov.hide_overlay()
        assert ov.isHidden()
    finally:
        host.deleteLater()


def test_loading_contract_preserved(qapp):
    """set_progress 의미(결정형/busy·증가 tween·범위변경 스냅)는 불변."""
    host, ov = _overlay(qapp)
    try:
        ov.show_overlay("작업 중")
        ov.set_progress(0, 0, "탐색")               # total<=0 → busy
        assert not ov._busy.isHidden() and ov._progress.isHidden()
        ov.set_progress(5, 10, "처리")              # 결정형
        assert not ov._progress.isHidden() and ov._progress.maximum() == 10
        assert ov._progress.value() == 5
        ov.set_progress(9, 10)                      # 증가
        assert ov._progress.value() == 9
        ov.set_progress(2, 40)                      # 범위 변경 → 스냅
        assert ov._progress.maximum() == 40 and ov._progress.value() == 2
    finally:
        host.deleteLater()
