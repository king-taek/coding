"""'크게 보기' 가 실제로 **크게** 열리는지.

버그: 우클릭 '크게 보기' 로 여는 `FullscreenViewer` 가 배율 1.0 고정이라,
mid 캐시(긴 변 ≤800px)를 창 전체(≈1400px) 한가운데 1:1 로 그렸다 — 이름과 정반대로
작게 떴다.  이제 첫 표시 때 `fit_scale` 로 뷰포트를 꽉 채운다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


# ── 순수 함수 — PyQt6 없이도 검증된다 ────────────────────────────────────
def _fit():
    from aoi_verification.app.ui.widgets.zoom_window import fit_scale
    return fit_scale


def test_fit_scale_fills_the_view_on_the_limiting_axis():
    fit_scale = _fit()
    # 가로가 제약: 800×600 → 1600×1600 이면 2.0 (가로 기준)
    assert fit_scale(800, 600, 1600, 1600) == pytest.approx(2.0)
    # 세로가 제약: 800×600 → 1600×600 이면 1.0 (세로 기준)
    assert fit_scale(800, 600, 1600, 600) == pytest.approx(1.0)


def test_fit_scale_upscales_small_images():
    """★ 1.0 상한을 두지 않는다 — 작은 사진도 창을 채워야 '크게 보기'다."""
    fit_scale = _fit()
    assert fit_scale(200, 150, 1400, 900) > 1.0


def test_fit_scale_shrinks_large_images_to_fit():
    fit_scale = _fit()
    s = fit_scale(5000, 4000, 1400, 900)
    assert s < 1.0
    assert 5000 * s <= 1400 + 1e-6 and 4000 * s <= 900 + 1e-6


def test_fit_scale_is_safe_on_degenerate_input():
    fit_scale = _fit()
    for args in ((0, 100, 800, 600), (100, 0, 800, 600),
                 (100, 100, 0, 600), (100, 100, 800, 0)):
        assert fit_scale(*args) == 1.0


# ── 위젯 동작 ────────────────────────────────────────────────────────────
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QPixmap                      # noqa: E402
from PyQt6.QtWidgets import QApplication             # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _viewer(qapp, tmp_path, monkeypatch, pix_w=400, pix_h=300):
    """mid 캐시를 가짜 픽스맵으로 대체한 뷰어(디스크 I/O 없이)."""
    from aoi_verification.app.ui.widgets import zoom_window as zw

    src = tmp_path / "die.png"
    QPixmap(pix_w, pix_h).save(str(src))
    monkeypatch.setattr(zw.image_io, "get_mid_path", lambda p, **k: src)
    return zw.FullscreenViewer(src)


def test_first_resize_fits_the_image_to_the_window(qapp, tmp_path,
                                                   monkeypatch):
    from aoi_verification.app.ui.widgets.zoom_window import fit_scale

    v = _viewer(qapp, tmp_path, monkeypatch)
    v.resize(1400, 900)                     # 시트 호스트가 씌우는 크기에 해당
    v.show()
    QApplication.processEvents()

    assert v._fitted is True
    # ★ 기준은 **다이얼로그가 아니라 사진이 놓이는 칸**이다.  하단 조작 힌트 줄만큼
    #   라벨이 짧으므로, 다이얼로그 크기로 맞추면 캔버스가 라벨보다 커져 중앙 정렬이
    #   사진 위아래를 균등하게 잘라 낸다(사용자는 잘린 줄도 모른다).
    vw, vh = v._view_size()
    assert (vw, vh) == (v._label.width(), v._label.height())
    assert vh < 900, "힌트 줄이 있는데 사진 칸이 다이얼로그와 같은 높이다"
    assert v._scale == pytest.approx(
        fit_scale(v._pix.width(), v._pix.height(), vw, vh))
    # 400×300 을 이 칸에 맞추면 확대다 — 이게 이 버그의 핵심.
    assert v._scale > 1.0
    v.deleteLater()


def test_the_photo_is_never_cropped_by_the_hint_row(qapp, tmp_path, monkeypatch):
    """캔버스가 사진 칸보다 크면 라벨이 위아래를 잘라 낸다 — 그 조합을 막는다.

    세로가 제약이 되는 사진(가로로 납작하지 않은 것)에서 특히 그렇다."""
    v = _viewer(qapp, tmp_path, monkeypatch, pix_w=400, pix_h=300)
    v.resize(1400, 900)
    v.show()
    QApplication.processEvents()

    canvas = v._label.pixmap()
    assert canvas is not None and not canvas.isNull()
    assert canvas.height() <= v._label.height(), (
        f"캔버스({canvas.height()})가 사진 칸({v._label.height()})보다 커서 잘린다")
    assert canvas.width() <= v._label.width()
    # 맞춤 결과가 칸 안에 온전히 들어간다(잘림 0).
    assert int(v._pix.height() * v._scale) <= v._label.height() + 1
    assert int(v._pix.width() * v._scale) <= v._label.width() + 1
    v.deleteLater()


def test_later_resize_does_not_clobber_user_zoom(qapp, tmp_path, monkeypatch):
    """맞춤은 **한 번만**.  사용자가 휠로 잡은 배율을 창 크기 변화가 덮지 않는다."""
    v = _viewer(qapp, tmp_path, monkeypatch)
    v.resize(1400, 900)
    v.show()
    QApplication.processEvents()

    v._scale = 3.5                          # 사용자가 휠로 확대했다고 치자
    v.resize(1000, 700)
    QApplication.processEvents()

    assert v._scale == pytest.approx(3.5)
    v.deleteLater()


def test_original_swap_keeps_the_on_screen_size(qapp, tmp_path, monkeypatch):
    """원본으로 갈아 끼울 때 화면상 크기가 튀면 안 된다(배율 재보정)."""
    from PyQt6.QtGui import QImage

    v = _viewer(qapp, tmp_path, monkeypatch, pix_w=400, pix_h=300)
    v.resize(1400, 900)
    v.show()
    QApplication.processEvents()
    before_w = v._pix.width() * v._scale

    v._on_original_loaded(QImage(1600, 1200, QImage.Format.Format_RGB32))

    assert v._pix.width() == 1600
    assert v._pix.width() * v._scale == pytest.approx(before_w, rel=1e-6)
    v.deleteLater()


def test_loader_is_not_a_child_of_the_viewer(qapp, tmp_path, monkeypatch):
    """실행 중인 QThread 가 뷰어와 함께 파괴되면 프로세스가 죽는다 —
    로더는 뷰어의 자식이 아니어야 한다."""
    from aoi_verification.app.ui.widgets import zoom_window as zw

    src = tmp_path / "die.png"
    QPixmap(10, 10).save(str(src))
    ld = zw._OriginalLoader(src)
    try:
        assert ld.parent() is None
    finally:
        ld.wait(2000)
