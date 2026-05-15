"""window_size_dialog 의 순수 헬퍼 함수 단위 테스트.

GUI 의존성 없는 부분(``filter_standard_resolutions`` /
``suggest_default_size`` /  ``UserSizeChoice``)만 검증.
"""

import os

import pytest

# PyQt6 가 있을 때만 — 헬퍼는 의존성이 없지만 모듈 임포트 시 QGuiApplication
# 을 참조하므로 ``QT_QPA_PLATFORM=offscreen`` 으로 안전하게 임포트한다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from aoi_verification.app.ui.widgets import window_size_dialog as wsd  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_filter_keeps_only_resolutions_within_monitor(qapp):
    out = wsd.filter_standard_resolutions(1920, 1080)
    # 1280×720, 1366×768, 1600×900, 1920×1080 만.
    assert (1280, 720) in out
    assert (1920, 1080) in out
    # 2560×1440 은 빠져야 함.
    assert (2560, 1440) not in out
    assert (3840, 2160) not in out


def test_filter_strict_below(qapp):
    # 모니터가 1000×800 이면 1280×720 도 1280>1000 이라 빠짐.
    out = wsd.filter_standard_resolutions(1000, 800)
    assert out == []


def test_suggest_default_returns_minimum_or_better(qapp):
    w, h = wsd.suggest_default_size()
    # 어떤 환경이든 최소 한도는 만족.
    assert w >= wsd.MIN_WIDTH
    assert h >= wsd.MIN_HEIGHT


def test_user_choice_dataclass_fields():
    c = wsd.UserSizeChoice(width=1600, height=900, fullscreen=False)
    assert c.width == 1600 and c.height == 900 and c.fullscreen is False
    c2 = wsd.UserSizeChoice(width=1920, height=1080, fullscreen=True)
    assert c2.fullscreen is True
