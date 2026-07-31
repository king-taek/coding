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

from aoi_verification.app.models.slot import ImageItem  # noqa: E402
from aoi_verification.app.ui import theme               # noqa: E402
from aoi_verification.app.ui.widgets import side_by_side_viewer as sbs  # noqa: E402


@pytest.fixture(scope="module")
def qapp(styled_qapp):
    """테마 적용된 앱 — conftest 가 `setStyleSheet` 을 세션당 1회만 부른다."""
    return styled_qapp


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


# ---------------------------------------------------------------------------
# 3) 휠 확대 / 드래그 이동 — **마우스가 올라간 쪽만**
# ---------------------------------------------------------------------------
def test_zoom_applies_to_one_pane_only(qapp, big_source):
    """사용자 결정: 확대는 패널마다 따로.  기준을 두고 후보만 파고들 수 있어야 한다."""
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()
        v._cand_pane.zoom_by(1.1)
        assert v._cand_pane._zoom > 1.0, "후보가 확대되지 않았다"
        assert v._ref_pane._zoom == 1.0, "반대쪽 사진까지 같이 확대됐다"
    finally:
        v.close()


def test_zoom_is_clamped(qapp, big_source):
    """유효 배율(맞춤×배수)이 상·하한 안에 머문다 — 사진이 사라지지 않게.

    ※ 휠을 수백 번 굴리는 대신 **한 번에 큰 배수**를 준다.  클램프는 누적이 아니라
    한 호출 안에서 걸리므로 검증력은 같고, 확대 렌더(SmoothTransformation)를
    수백 번 반복하지 않아 스위트가 느려지지 않는다."""
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()
        pane = v._cand_pane
        box = pane._target_box()
        base = sbs.fit_scale(pane._pix.width(), pane._pix.height(),
                             box.width(), box.height())
        pane.zoom_by(10_000)
        assert base * pane._zoom <= sbs._SCALE_MAX + 1e-6, "상한을 넘겼다"
        pane.zoom_by(1e-9)
        assert base * pane._zoom >= sbs._SCALE_MIN - 1e-6, "하한 밑으로 내려갔다"
    finally:
        v.close()


def test_drag_moves_the_zoomed_image(qapp, big_source):
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()
        pane = v._cand_pane
        pane.zoom_by(1.1 ** 4)                 # 확대 상태에서만 이동이 의미 있다
        start, end = QPointF(100.0, 100.0), QPointF(140.0, 130.0)
        pane.mousePressEvent(QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        pane.mouseMoveEvent(QMouseEvent(
            QMouseEvent.Type.MouseMove, end, Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        assert (pane._off_x, pane._off_y) == (40, 30), \
            f"드래그가 사진을 옮기지 않았다: {(pane._off_x, pane._off_y)}"
        pane.mouseReleaseEvent(QMouseEvent(
            QMouseEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        assert pane._last_drag is None
        assert QPoint  # (import 사용 표시)
    finally:
        v.close()


def test_zoom_survives_a_candidate_switch(qapp, big_source):
    """←/→ 로 후보를 바꿔도 배율·위치가 유지된다 — 같은 자리를 같은 배율로 비교."""
    cands = [(ImageItem("A", Path("/tmp/a.png"), "val"), "90%"),
             (ImageItem("B", Path("/tmp/b.png"), "val"), "80%")]
    v = sbs.SideBySideViewer(Path("/tmp/r.png"), cands, 0, ref_caption="기준")
    try:
        v.show()
        qapp.processEvents()
        v._cand_pane.zoom_by(1.1 ** 3)
        zoom, off = v._cand_pane._zoom, (v._cand_pane._off_x, v._cand_pane._off_y)
        v._next()
        assert v._idx == 1
        assert v._cand_pane._zoom == zoom, "후보를 바꾸니 배율이 풀렸다"
        assert (v._cand_pane._off_x, v._cand_pane._off_y) == off
    finally:
        v.close()


def test_returning_to_fit_clears_the_offset(qapp, big_source):
    """맞춤(1.0)으로 돌아오면 이동도 함께 풀린다 — 사진이 화면 밖에 남지 않게."""
    v = _viewer()
    try:
        v.show()
        qapp.processEvents()
        pane = v._cand_pane
        pane.zoom_by(1.1)
        pane._off_x, pane._off_y = 50, 50
        pane.zoom_by(1 / 1.1)
        assert pane._zoom == 1.0
        assert (pane._off_x, pane._off_y) == (0, 0)
        # 미확대 경로로 돌아왔으므로 두 패널은 다시 같은 박스를 공유한다.
        assert v._ref_pane._box == v._cand_pane._box
    finally:
        v.close()


# ---------------------------------------------------------------------------
# 4) 시트로 띄우면 **창을 꽉 채운다**
# ---------------------------------------------------------------------------
def test_compare_viewer_fills_the_window_as_a_sheet(qapp, big_source):
    """'크게 보기'가 작게 뜨면 안 된다 — 시트 여백(8px)만 남기고 창을 채운다.

    ★ 실제로 났던 버그의 회귀 가드다: 이 뷰어가 ``setMaximumSize(주 모니터 크기)``
    를 걸고 있었고, 창이 그보다 크면(다중 모니터에서 주 모니터가 더 작거나, 큰
    모니터에 최대화한 경우) 시트 배치가 그 상한에 잘려 **창보다 작은 팝업**이 됐다.
    여기서는 화면을 800×800 으로 보는 offscreen 에서 1000×720 창을 써 그 상황을
    그대로 재현한다.

    호출부 3곳이 모두 ``full_bleed=True`` 로 띄우므로 그 계약도 함께 못 박는다."""
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QWidget
    from aoi_verification.app.ui.widgets import sheet_host

    win = QWidget()
    win.resize(1000, 720)
    host = sheet_host.SheetHost(win)
    win._sheets = host
    win.show()
    for _ in range(8):
        qapp.processEvents()

    cands = [(ImageItem("A", Path("/tmp/a.png"), "val"), "90%")]
    viewer = sbs.SideBySideViewer(Path("/tmp/r.png"), cands, 0,
                                  ref_caption="기준", parent=win)
    got = {}

    def check():
        got["hosted"] = bool(host._stack)
        got["geom"] = host._stack[-1]["root"].geometry() if host._stack else None
        got["host"] = (host.width(), host.height())
        viewer.accept()

    QTimer.singleShot(40, check)
    sheet_host.run(viewer, full_bleed=True)
    try:
        assert got["hosted"], "시트로 뜨지 않고 별도 창으로 폴백했다"
        assert got["host"] == (1000, 720), \
            f"호스트가 창을 덮지 않는다: {got['host']}"
        g = got["geom"]
        assert (g.x(), g.y()) == (8, 8), f"여백이 8px 이 아니다: {g}"
        assert (g.width(), g.height()) == (1000 - 16, 720 - 16), \
            f"창을 채우지 않는다: {g}"
    finally:
        host.close_all()
        win.hide()
        qapp.processEvents()
        win.deleteLater()
        qapp.processEvents()


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
