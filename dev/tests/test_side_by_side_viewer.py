"""좌우 비교 뷰어('크게 보기') — 초기 표시 크기 + 상단 액션 버튼 가독성.

회귀 대상 두 가지:
1. 열자마자 사진이 아주 작게 시작하던 버그.  ``__init__`` 의 첫 렌더는 레이아웃이
   돌기 전이라 이미지 라벨이 기본 최소크기(≈100×30)였고, 그때 잡힌 공통 박스가
   ``_redraw`` 에서 우선 적용돼 창이 커도 사진이 40×30 으로 남았다.
2. 상단 '다음'과 '닫기' 사이 액션 버튼의 글자가 안 보이던 버그.  뷰어가
   ``setStyleSheet("background-color: …")`` 을 부르면 **자식 전체**가 그 배경을
   물려받아, 채운 버튼(role=primary)의 면이 시트 색으로 덮이고 글자만 on_accent 로
   남아 배경에 묻혔다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QColor, QPixmap                # noqa: E402
from PyQt6.QtWidgets import QApplication               # noqa: E402

from aoi_verification.app.models.slot import ImageItem  # noqa: E402
from aoi_verification.app.ui import theme               # noqa: E402
from aoi_verification.app.ui.widgets import side_by_side_viewer as sbs  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    theme.apply_to_app(app)
    return app


@pytest.fixture
def big_source(monkeypatch):
    """원본 디코드를 큰 더미 이미지로 대체(파일 I/O 없이)."""
    def _fake(_path):
        pm = QPixmap(1600, 1200)
        pm.fill(QColor("#3060a0"))
        return pm
    monkeypatch.setattr(sbs, "_decode_original", _fake)


def _viewer(action_label="이 후보로 매치"):
    cands = [(ImageItem("A", Path("/tmp/a.png"), "val"), "90%")]
    return sbs.SideBySideViewer(Path("/tmp/r.png"), cands, 0,
                                ref_caption="기준", action_label=action_label)


def _relative_luminance(c: QColor) -> float:
    def lin(v: int) -> float:
        x = v / 255.0
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c.red()) + 0.7152 * lin(c.green()) + 0.0722 * lin(c.blue())


def _contrast(a: QColor, b: QColor) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# 1) 열자마자 사진이 패널을 채운다
# ---------------------------------------------------------------------------
def test_image_fills_pane_on_first_show(qapp, big_source):
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()          # showEvent 의 0ms 재동기화까지 흘린다
        label = v._ref_pane.img_size()
        pix = v._ref_pane._img.pixmap().size()
        assert label.width() > 0 and label.height() > 0
        # 비율 유지라 한 축은 꽉 차야 한다 — 버그 때는 40×30(12%)이었다.
        fill = max(pix.width() / label.width(), pix.height() / label.height())
        assert fill > 0.95, f"사진이 패널을 못 채운다: {pix} in {label}"
    finally:
        v.close()


def test_both_panes_share_one_box(qapp, big_source):
    """기준·후보가 같은 박스로 스케일된다(한쪽만 커지지 않는다)."""
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()
        assert v._ref_pane._box == v._cand_pane._box
        assert v._ref_pane._box is not None and v._ref_pane._box.width() > 100
    finally:
        v.close()


# ---------------------------------------------------------------------------
# 2) 상단 액션 버튼의 글자가 배경에 묻히지 않는다
# ---------------------------------------------------------------------------
def test_viewer_does_not_paint_children_background(qapp, big_source):
    """뷰어는 인라인 배경을 걸지 않는다 — 자식 버튼 면까지 덮기 때문."""
    v = _viewer()
    try:
        assert v.styleSheet() == ""
        assert v.property("role") == "sheet"   # 면은 QSS 가 칠한다
    finally:
        v.close()


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_action_button_text_is_legible(qapp, big_source, mode):
    """액션 버튼(다음↔닫기 사이)의 글자/배경 대비가 WCAG AA(4.5:1) 이상."""
    theme.set_color_mode(mode)
    theme.apply_to_app(qapp)
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()
        img = v.btn_action.grab().toImage()
        counts: dict[str, int] = {}
        for y in range(img.height()):
            for x in range(img.width()):
                name = img.pixelColor(x, y).name()
                counts[name] = counts.get(name, 0) + 1
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:2]
        assert len(top) == 2, "버튼이 단색으로만 렌더 — 글자가 안 보인다"
        bg, fg = QColor(top[0][0]), QColor(top[1][0])
        ratio = _contrast(bg, fg)
        assert ratio >= 4.5, f"{mode}: 대비 {ratio:.2f}:1 (배경 {bg.name()} / 글자 {fg.name()})"
    finally:
        v.close()
        theme.set_color_mode("light")
        theme.apply_to_app(qapp)
