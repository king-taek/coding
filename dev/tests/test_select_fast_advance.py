"""사진 선별 — 결정하면 다음 사진이 **즉시** 보이고, 보이기 전 입력은 버린다.

배경(사용자 보고 2026-10-25): →/← 로 넘길 때 앞 사진이 180ms 밀려난 뒤 다음 사진이
120ms 떠올랐다.  그 300ms 동안 다음 사진은 투명했고, 연타하면 **보지도 못한 사진이
결정**됐다(뒷사진이 안 보이고 넘어간다).  그래서:
- 사진 자리에는 모션을 걸지 않는다(잔상·이펙트 0).
- 새 사진이 그려지기 전의 결정 입력은 버린다(큐에 담지 않는다).
- 키를 누르고 있어도 한 장만 넘긴다(자동 반복 끔).
- 다음 사진은 백그라운드에서 미리 읽는다 — 결과는 그 자리에서 읽은 것과 같다.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QImage, QShortcut                 # noqa: E402
from PyQt6.QtWidgets import QLabel                               # noqa: E402

from aoi_verification.app.models.slot import ImageItem            # noqa: E402
from aoi_verification.app.ui import motion                        # noqa: E402
from aoi_verification.app.ui.pages.select_page import SelectPage   # noqa: E402
from aoi_verification.app.ui.widgets.scalable_image import ScalableImage  # noqa: E402


def _photos(tmp_path, n):
    out = []
    for i in range(n):
        im = QImage(120, 90, QImage.Format.Format_RGB32)
        im.fill(QColor(200 - 30 * i, 90, 120))
        p = tmp_path / f"p{i}.jpg"
        im.save(str(p), "JPG", 90)
        out.append(p)
    return out


def _page(styled_qapp, tmp_path, n=4):
    page = SelectPage()
    page.resize(900, 700)
    page.load_state(queue=[ImageItem(slot="S1", path=p, side="ref")
                           for p in _photos(tmp_path, n)],
                    targets={}, excluded={}, history=[])
    page.show()
    for _ in range(8):
        styled_qapp.processEvents()
    return page


def _wait(qapp, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        qapp.processEvents()


def test_next_photo_is_shown_at_once_under_a_short_ghost(styled_qapp, tmp_path,
                                                         monkeypatch):
    """모션이 켜진 실제 앱 조건 — 다음 사진은 바로 불투명하고, 잔상은 짧게 끝난다."""
    monkeypatch.setattr(motion, "enabled", lambda: True)
    page = _page(styled_qapp, tmp_path)
    second = page._state.queue[1]
    page._decide("verify")
    assert page._current is second
    assert page.center_img.graphicsEffect() is None, "다음 사진이 투명하게 숨어 있다"
    assert page._swipe_ghost is not None
    _wait(styled_qapp, motion.DUR_SWIPE_OVER + 80)
    ghosts = [w for w in page._img_scroll.viewport().findChildren(QLabel)
              if w is not page.center_img and w.isVisible()]
    assert page._swipe_ghost is None and ghosts == [], "잔상이 남아 새 사진을 덮는다"
    page.close()


def test_input_before_the_photo_is_seen_is_dropped(styled_qapp, tmp_path):
    page = _page(styled_qapp, tmp_path)
    first, second = page._state.queue[0], page._state.queue[1]
    _wait(styled_qapp, page._MIN_VIEW_MS + 20)
    page._decide_input("verify")             # 첫 사진은 보였다 → 받는다
    assert page._current is second
    page._decide_input("verify")             # 곧바로 = 둘째 사진을 볼 틈이 없었다
    assert page._current is second, "보지 못한 사진이 결정됐다"
    assert page._state.targets["S1"] == [first]
    _wait(styled_qapp, page._MIN_VIEW_MS + 20)
    page._decide_input("exclude")            # 보인 뒤에는 받는다
    assert page._state.excluded["S1"] == [second]
    page.close()


def test_unpainted_photo_takes_no_input(styled_qapp, tmp_path):
    page = _page(styled_qapp, tmp_path)
    page._close_input_gate()                 # 새 사진을 넣고 아직 안 그려짐
    page._shown_at = None
    time.sleep((page._MIN_VIEW_MS + 20) / 1000)
    cur = page._current
    page._decide_input("verify")
    assert page._current is cur
    page.close()


def test_stale_gate_timer_does_not_open_a_newer_lock(styled_qapp, tmp_path):
    page = _page(styled_qapp, tmp_path)
    page._close_input_gate()
    old = page._gate_token
    page._close_input_gate()                 # 새 사진 — 새 잠금
    page._mark_shown(old)                    # 지난 사진의 시한 타이머
    assert page._shown_at is None
    page._mark_shown(page._gate_token)
    assert page._shown_at is not None
    page.close()


def test_decision_keys_do_not_auto_repeat(styled_qapp, tmp_path):
    page = _page(styled_qapp, tmp_path)
    keys = {sc.key().toString(): sc for sc in page.findChildren(QShortcut)}
    assert keys["Right"].autoRepeat() is False
    assert keys["Left"].autoRepeat() is False
    page.close()


def test_prefetch_gives_the_same_pixels_as_direct_load(styled_qapp, tmp_path,
                                                       isolated_cache):
    paths = _photos(tmp_path, 3)
    for crop in (1.0, 0.5):
        direct = ScalableImage()
        direct.set_center_crop(crop)
        direct.set_image(paths[1])
        pre = ScalableImage()
        pre.set_center_crop(crop)
        pre.prefetch([paths[1], paths[2]])
        fut = pre._prefetched[(paths[1], pre.center_crop())]
        deadline = time.monotonic() + 5
        while not fut.done() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert fut.done()
        pre.set_image(paths[1])
        assert (paths[1], pre.center_crop()) not in pre._prefetched, "쓴 예약이 남았다"
        assert pre._pix_orig.toImage() == direct._pix_orig.toImage()
        pre.prefetch([])                     # 순서가 바뀌면 옛 예약은 버린다
        assert pre._prefetched == {}


def test_select_page_prefetches_the_next_photos(styled_qapp, tmp_path):
    page = _page(styled_qapp, tmp_path)
    q = page._state.queue
    want = {(q[1].path, page.center_img.center_crop()),
            (q[2].path, page.center_img.center_crop())}
    assert set(page.center_img._prefetched) == want
    page.close()


def test_set_image_waits_for_a_running_prefetch(styled_qapp, tmp_path,
                                                monkeypatch):
    """읽는 중인 사진은 두 번 읽지 않고 그 결과를 기다린다(쓰는 중인 캐시 파일 방지)."""
    import threading

    paths = _photos(tmp_path, 1)
    started, release = threading.Event(), threading.Event()
    calls = []
    real = ScalableImage._decode.__func__

    def slow(cls, path, crop):
        calls.append(threading.current_thread().name)
        started.set()
        release.wait(5)
        return real(cls, path, crop)

    monkeypatch.setattr(ScalableImage, "_decode", classmethod(slow))
    w = ScalableImage()
    w.prefetch([paths[0]])
    assert started.wait(5)
    threading.Timer(0.05, release.set).start()
    w.set_image(paths[0])
    assert len(calls) == 1, "같은 사진을 메인 스레드에서 또 읽었다"
    assert w._pix_orig is not None and w._pix_orig.width() == 120

