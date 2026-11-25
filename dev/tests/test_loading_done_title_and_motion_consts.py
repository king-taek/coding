"""V23 100% 에 제목 '완료' · [중지] ghost, M8 모션 상수가 옛 값 그대로임을 못 박는다."""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QWidget                                 # noqa: E402

from aoi_verification.app import i18n                               # noqa: E402
from aoi_verification.app.ui import motion                          # noqa: E402
from aoi_verification.app.ui.widgets.loading_overlay import (       # noqa: E402
    LoadingOverlay, _WaferMap)


@pytest.fixture()
def overlay(styled_qapp):
    host = QWidget()
    host.resize(900, 600)
    host.show()
    ov = LoadingOverlay(host)
    yield ov
    ov._finish_hide()
    host.close()


def test_title_becomes_done_at_100_and_returns(overlay):
    K = i18n.KO
    overlay.show_overlay(K.LOAD_EXPORT, cancelable=True)
    overlay.set_progress(10, 30, K.LOAD_EXPORT)
    assert overlay._label.text() != K.LOADING_DONE_TITLE
    overlay.set_progress(30, 30, K.LOAD_EXPORT)
    assert overlay._label.text() == K.LOADING_DONE_TITLE
    # 같은 문구가 100% 에서 다시 와도 '완료' 가 유지된다
    overlay.set_progress(30, 30, K.LOAD_EXPORT)
    assert overlay._label.text() == K.LOADING_DONE_TITLE
    # 다시 진행이 오면 원래 제목
    overlay.set_progress(0, 0, K.LOAD_EXPORT)
    assert overlay._label.text() == overlay._title_text != K.LOADING_DONE_TITLE


def test_done_title_does_not_leak_into_next_overlay(overlay):
    K = i18n.KO
    overlay.show_overlay(K.LOAD_EXPORT)
    overlay.set_progress(30, 30, K.LOAD_EXPORT)
    assert overlay._label.text() == K.LOADING_DONE_TITLE
    overlay._finish_hide()
    overlay.show_overlay(K.LOAD_SCORING)
    assert overlay._label.text() != K.LOADING_DONE_TITLE


def test_stop_button_is_ghost(overlay):
    assert overlay._cancel_btn.property("role") == "ghost"


def test_motion_constants_keep_old_values():
    assert (_WaferMap.PULSE_MS, _WaferMap.RIPPLE_MS) == (1600, 140)
    assert (_WaferMap.FADE_MS, _WaferMap.GREY_FADE_MS) == (200, 200)
    assert LoadingOverlay.FADE_OUT_MS == 140
    assert (LoadingOverlay.VAL_PER_PCT_MS, LoadingOverlay.VAL_TWEEN_MIN_MS,
            LoadingOverlay.VAL_TWEEN_MAX_MS) == (10, 100, 600)
    assert motion.LOADING_STRIPE_MS == 1100
    assert max(motion.LOADING_FADE_OUT_MIN_MS, motion.dur(LoadingOverlay.FADE_OUT_MS)) == max(110, motion.dur(140))
