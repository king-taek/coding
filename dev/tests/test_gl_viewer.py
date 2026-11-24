"""GL 히스토그램 뷰어 화면 — 헤드리스 조작 검증.

못 박는 것:

1. 도구는 이동·사각형·원 셋뿐이다(타원·선·정사각형 없음).  원은 늘 정원이다.
2. 선택한 ROI 는 방향키로 움직인다(Shift 10 px, 연타는 되돌리기 한 번).
3. 크기는 손잡이를 끌어 바꾼다(크기 버튼·키 없음).  숫자 키로 도구가 바뀌지 않는다.
4. 화면을 줄였다(사용자 HTML 시안 결정 2026-11-16): 통계표·선 프로파일·자동 추천·
   화면 대비 자동·로그 눈금·1:1·크기 버튼이 없다.  통계는 엑셀에만 쓴다.
5. 컬러 사진은 Scan 의 결함 영역을 확대해 보여 주고, 그 상자에 'Defect' ROI 를 자동으로 둔다.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6.QtWidgets")
np = pytest.importorskip("numpy")

from PyQt6.QtCore import QPoint, QPointF, Qt                       # noqa: E402
from PyQt6.QtTest import QTest                                     # noqa: E402
from PyQt6.QtWidgets import QApplication                           # noqa: E402

from aoi_verification.app.gl import analysis, loader                # noqa: E402
from aoi_verification.app.gl import roi as R                        # noqa: E402
from aoi_verification.app.ui.widgets.gl_canvas import TOOLS         # noqa: E402
from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog  # noqa: E402
from aoi_verification.app.ui.widgets import gl_viewer_dialog as GLV       # noqa: E402

W, H = 200, 150


def _pump(n: int = 3) -> None:
    for _ in range(n):
        QApplication.processEvents()


def _wait(pred, ms: int = 5000) -> bool:
    import time
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def _make(styled_qapp, data=None, *, bits=16, view=None):
    if data is None:
        rng = np.random.default_rng(3)
        hi = (1 << bits) - 1
        data = rng.integers(0, hi, size=(H, W)).astype(np.uint16 if bits > 8 else np.uint8)
    dlg = GlViewerDialog(None)
    dlg.resize(1280, 820)
    dlg.show()
    _pump()
    dlg.show_view(view or loader.ViewLoad(loader.GlImage(data, bits, False, "x.tif", [])))
    _pump()
    return dlg


@pytest.fixture
def dlg(styled_qapp):
    d = _make(styled_qapp)
    yield d
    d.close()
    d.deleteLater()


def _pt(canvas, x, y) -> QPoint:
    """이미지 픽셀 (x, y) 의 한가운데 화면 점 — 경계(정수 좌표)를 찍으면 반올림에 흔들린다."""
    return canvas.to_screen(x + 0.5, y + 0.5).toPoint()


def _drag(canvas, a: QPoint, b: QPoint, steps: int = 6) -> None:
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
    for i in range(1, steps + 1):
        p = QPoint(a.x() + (b.x() - a.x()) * i // steps, a.y() + (b.y() - a.y()) * i // steps)
        QTest.mouseMove(canvas, p)
        _pump(1)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
    _pump()


# ------------------------------------------------------------------ 1. 도구
def test_only_three_tools_without_numbers(dlg):
    assert TOOLS == ("move", R.RECT, R.CIRCLE)
    # 사진 위 팔레트는 그림 버튼이라 이름은 접근성 이름·툴팁에 둔다(단축키 숫자는 붙이지 않는다)
    labels = [b.accessibleName() for b in dlg._tool_btns.values()]
    assert labels == ["이동", "사각형", "원"]
    assert [b.toolTip() for b in dlg._tool_btns.values()] == labels
    assert all(not b.icon().isNull() and not b.text() for b in dlg._tool_btns.values())


def test_drawing_each_shape_by_dragging(dlg):
    c = dlg.canvas
    dlg._set_tool(R.RECT)
    _drag(c, _pt(c, 30, 20), _pt(c, 80, 50))
    r = c.rois.active()
    assert r.kind == R.RECT and (r.x, r.y, r.w, r.h) == (30, 20, 50, 30)
    c.rois.selected = None
    dlg._set_tool(R.CIRCLE)
    _drag(c, _pt(c, 100, 60), _pt(c, 140, 80))
    r = c.rois.active()
    assert r.kind == R.CIRCLE and r.w == r.h == 40        # 타원이 아니라 정원이다


def test_tiny_drag_makes_no_shape(dlg):
    c = dlg.canvas
    dlg._set_tool(R.RECT)
    _drag(c, _pt(c, 30, 20), _pt(c, 30, 20) + QPoint(1, 1), steps=1)
    assert c.rois.rois == []


# ------------------------------------------------------------------ 2. 방향키
def _add_rect(dlg, x=50, y=40, w=30, h=20, kind=R.RECT):
    r = dlg.canvas.rois.add(R.Roi(0, kind, "", "", x=x, y=y, w=w, h=h))
    dlg.canvas.update()
    return r


def test_arrow_keys_move_selected_roi(dlg):
    c = dlg.canvas
    _add_rect(dlg)
    QTest.keyClick(c, Qt.Key.Key_Right)
    QTest.keyClick(c, Qt.Key.Key_Down)
    r = c.rois.active()
    assert (r.x, r.y) == (51, 41)
    QTest.keyClick(c, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(c, Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier)
    r = c.rois.active()
    assert (r.x, r.y) == (41, 31)


def test_arrow_keys_stop_at_image_edge(dlg):
    c = dlg.canvas
    _add_rect(dlg, x=0, y=0)
    for _ in range(3):
        QTest.keyClick(c, Qt.Key.Key_Left)
        QTest.keyClick(c, Qt.Key.Key_Up)
    r = c.rois.active()
    assert (r.x, r.y) == (0, 0)


def test_arrow_burst_is_one_undo_step(dlg):
    c = dlg.canvas
    _add_rect(dlg)
    for _ in range(5):
        QTest.keyClick(c, Qt.Key.Key_Right)
    assert c.rois.active().x == 55
    QTest.keyClick(c, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert c.rois.active().x == 50                         # 연타 전체가 한 번에 되돌려진다
    QTest.keyClick(c, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert c.rois.rois == []                               # 그다음은 추가가 되돌려진다


def test_arrows_without_selection_do_nothing(dlg):
    c = dlg.canvas
    assert c.nudge(1, 0) is False


# ------------------------------------------------------------------ 3. 크기 조절
def test_size_keys_do_not_resize(dlg):
    c = dlg.canvas
    _add_rect(dlg, x=80, y=50, w=20, h=20, kind=R.CIRCLE)
    for k in (Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
        QTest.keyClick(c, k)
    g = c.rois.active()
    assert (g.x, g.y, g.w, g.h) == (80, 50, 20, 20)
    assert not hasattr(c, "grow")


def test_resize_by_dragging_handle(dlg):
    c = dlg.canvas
    _add_rect(dlg, x=50, y=40, w=30, h=20)
    handle = c._handle_points(c.rois.active())
    se = dict(handle)["se"].toPoint()
    _drag(c, se, _pt(c, 100, 80))
    g = c.rois.active()
    assert (g.x, g.y) == (50, 40) and g.w >= 49 and g.h >= 39


def test_move_shape_by_dragging_body(dlg):
    c = dlg.canvas
    _add_rect(dlg, x=50, y=40, w=40, h=30)
    _drag(c, _pt(c, 70, 55), _pt(c, 90, 75))
    g = c.rois.active()
    assert abs(g.x - 70) <= 1 and abs(g.y - 60) <= 1       # 화면 점이 정수라 ±1 px 오차


# ------------------------------------------------------------------ 통계·그래프
def test_removed_widgets_are_gone(dlg):
    from PyQt6.QtWidgets import QAbstractButton, QTableWidget
    for attr in ("table", "profile", "auto_btn", "auto_contrast", "log_cb",
                 "size_plus", "size_minus", "show_h", "show_v"):
        assert not hasattr(dlg, attr), attr
    assert dlg.findChildren(QTableWidget) == []
    texts = {b.text() for b in dlg.findChildren(QAbstractButton)}
    for gone in ("1:1", "크기", "−", "＋", "로그 눈금", "수평선 보기", "수직선 보기"):
        assert gone not in texts, gone
    assert not any(t.startswith(("자동 추천", "화면 대비")) for t in texts)
    assert "맞춤" in texts
    assert "ROI끼리 겹쳐 보기" not in texts and "데이터 범위만" not in texts      # D2 — 켜짐으로 고정, 토글 없음
    for attr in ("overlay_cb", "range_cb", "thr_range"):                            # D1 · D2 — 따로 있던 막대·토글
        assert not hasattr(dlg, attr), attr
    names = {b.accessibleName() for b in dlg.findChildren(QAbstractButton)}
    assert {"되돌리기 (Z)", "모두 지우기"} <= names            # 그림 버튼은 이름으로 찾는다
    from aoi_verification.app.ui.widgets import gl_charts
    assert not hasattr(gl_charts, "ProfileChart")
    assert not hasattr(gl_charts.HistogramChart, "set_log")


def test_stats_match_numpy(dlg):
    c = dlg.canvas
    r = _add_rect(dlg, x=10, y=10, w=20, h=10)
    dlg._refresh()
    row = dlg._stat_row(r)
    sub = dlg._gl.data[10:20, 10:30]
    assert row["n"] == sub.size and row["mean"] == pytest.approx(float(sub.mean()))
    assert row["below"] == int((sub <= dlg._lo).sum())


def test_threshold_order_is_kept(dlg):
    dlg._set_thresholds(40000, 30000, "lo")
    assert dlg._lo < dlg._hi
    dlg._set_thresholds(100, 50, "hi")
    assert dlg._lo < dlg._hi
    dlg._on_hist_drag("hi", 10)
    assert dlg._lo < dlg._hi
    assert dlg.lo_spin.value() == dlg._lo and dlg.hi_spin.value() == dlg._hi


def test_bit_depth_is_auto_detected_without_range_choice(styled_qapp):
    from PyQt6.QtWidgets import QComboBox
    d8 = _make(styled_qapp, bits=8)
    d16 = _make(styled_qapp, bits=16)
    for d in (d8, d16):
        assert not hasattr(d, "bits_combo")
        assert d.findChildren(QComboBox) == []
    assert d8._bits == 8 and d8._levels() == 256
    assert d16._bits == d16._gl.bits
    assert d16._levels() == 1 << d16._gl.bits and len(d16._full_hist) == d16._levels()
    for d in (d8, d16):
        d.close()


def test_overlay_toggle_and_scope_change_display(dlg):
    dlg.scope.set_current_key(analysis.SCOPE_OFF, emit=True)
    _pump()
    off = dlg.canvas._rgb.copy()
    assert (off[..., 0] == off[..., 1]).all()
    dlg.scope.set_current_key(analysis.SCOPE_ALL, emit=True)
    _pump()
    assert not (dlg.canvas._rgb[..., 0] == dlg.canvas._rgb[..., 1]).all()


def test_open_new_image_clears_rois(dlg):
    _add_rect(dlg)
    dlg.show_view(loader.ViewLoad(loader.GlImage(
        np.zeros((20, 30), np.uint8), 8, False, "y.png", [])))
    assert dlg.canvas.rois.rois == []
    assert dlg.canvas.image_size() == (30, 20)


# ------------------------------------------------------------------ 저장
def test_save_xlsx_and_png(dlg, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    _add_rect(dlg, x=10, y=10, w=20, h=10)
    _add_rect(dlg, x=40, y=40, w=20, h=20, kind=R.CIRCLE)
    dlg.save_xlsx(tmp_path / "out.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "out.xlsx")
    assert wb.sheetnames == ["분석 조건", "ROI 통계", "히스토그램"]      # 선 프로파일 시트는 없다
    stats = wb["ROI 통계"]
    assert stats.max_row == 4 and stats["A2"].value == "사진 전체"
    assert stats["C2"].value == W * H
    assert stats["A3"].value == "사각형 1" and stats["B3"].value == "(10, 10) 20×10"
    assert stats["C3"].value == 200 and stats["A4"].value == "원 1"
    hist = wb["히스토그램"]
    assert sum(r[1] for r in hist.iter_rows(min_row=2, values_only=True)) == W * H
    assert not (tmp_path / "out.xlsx.part").exists()

    dlg.save_png(tmp_path / "o.png", with_rois=True)
    from PyQt6.QtGui import QImage
    im = QImage(str(tmp_path / "o.png"))
    assert (im.width(), im.height()) == (W, H)              # 원본 해상도
    dlg.save_png(tmp_path / "p.png", with_rois=False)
    assert QImage(str(tmp_path / "p.png")).pixelColor(0, 0).isValid()


def test_save_failure_keeps_old_file(dlg, tmp_path):
    p = tmp_path / "keep.xlsx"
    p.write_bytes(b"old")
    class Boom:
        def save(self, *_a):
            raise OSError("disk full")
    from aoi_verification.app.gl import export
    with pytest.raises(OSError):
        export.save_workbook(Boom(), p)
    assert p.read_bytes() == b"old" and not (tmp_path / "keep.xlsx.part").exists()


# ------------------------------------------------------------------ 6. 컬러 → Scan
def test_scan_view_sets_target_box_and_zooms_in(styled_qapp):
    scan = np.full((300, 800), 90, np.uint8)
    v = loader.ViewLoad(loader.GlImage(scan, 8, False, "s.t.jpg", []),
                        scan_name="s.t.jpg", target_box=(400, 130, 40, 40), source_path="c.jpg")
    d = _make(styled_qapp, view=v)
    _pump()
    c = d.canvas
    assert [(r.x, r.y, r.w, r.h) for r in c.rois.rois] == [(416, 146, 7, 7)]     # 40 px 상자의 1/6, 한가운데
    fit = min(c.width() / 800, c.height() / 300)
    assert c.scale() > fit * 3                               # 결함 영역으로 확대됐다
    cx, cy = c.to_screen(420, 150).x(), c.to_screen(420, 150).y()
    assert abs(cx - c.width() / 2) < 3 and abs(cy - c.height() / 2) < 3    # 결함이 한가운데
    assert not hasattr(d, "banner")                           # 상시 문장은 없다 — 안내는 상태줄 오른쪽에
    assert d.note.isVisibleTo(d) and "s.t.jpg" in d.note.text() and not hasattr(d, "hint")
    assert d.note.parentWidget() is d.status.parentWidget()
    c.fit()                                                  # F — Scan 전체 보기
    assert c.scale() == pytest.approx(fit)
    d.close()


def _defect_view(box=(150, 100, 100, 100)):
    scan = np.full((H, W), 90, np.uint8)
    return loader.ViewLoad(loader.GlImage(scan, 8, False, "s.t.jpg", []),
                           scan_name="s.t.jpg", target_box=box, source_path="c.jpg")


def test_target_box_becomes_selected_defect_roi_with_histogram(styled_qapp):
    d = _make(styled_qapp, view=_defect_view())          # 상자가 이미지(200×150) 밖으로 넘친다
    rs = d.canvas.rois
    (r,) = rs.rois
    assert (r.kind, r.name, r.color) == (R.RECT, "Defect", "#FFD60A")
    assert (r.x, r.y, r.w, r.h) == (183, 133, 17, 17)  # 상자 1/6, 중심이 이미지 밖이라 안으로 가둠
    assert rs.selected == r.id and d.hist_name.text() == "Defect"
    assert [s[0] for s in d.hist._series] == ["Defect"]
    assert not hasattr(d.canvas, "set_target_box")
    d.close()


def test_view_without_target_has_no_defect_roi_and_button_disabled(dlg):
    assert dlg.canvas.rois.rois == []
    assert not dlg.defect_btn.isEnabled() and dlg.defect_btn.toolTip()


def test_defect_button_recreates_roi_once(styled_qapp):
    d = _make(styled_qapp, view=_defect_view((20, 20, 40, 40)))
    assert d.defect_btn.isEnabled()
    d.defect_btn.click()
    assert len(d.canvas.rois.rois) == 1                   # 이미 있으면 새로 만들지 않는다
    d._clear_rois()
    assert d.canvas.rois.rois == []
    d.defect_btn.click()
    (r,) = d.canvas.rois.rois
    assert r.name == "Defect" and (r.x, r.y, r.w, r.h) == (36, 36, 7, 7)
    assert d.canvas.rois.selected == r.id
    d.canvas.rois.delete()
    d.defect_btn.click()
    assert [x.name for x in d.canvas.rois.rois] == ["Defect"]
    d.close()


def test_open_color_photo_end_to_end_switches_to_scan(styled_qapp, tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    Image = pytest.importorskip("PIL.Image")
    from aoi_verification.app.coords import scan_image
    col = np.zeros((20, 20, 3), np.uint8)
    col[..., 2] = 200
    cp = tmp_path / "defect.jpg"
    cp.write_bytes(cv2.imencode(".jpg", col)[1].tobytes())
    sp = tmp_path / "scan.t.jpg"
    Image.fromarray(np.full((120, 300), 55, np.uint8)).save(sp)
    cand = scan_image.Candidate(sp, (0.0, 0.0), (300, 120), (150.0, 60.0), (130, 40, 40, 40), 1.0)
    monkeypatch.setattr(scan_image, "resolve",
                        lambda p: scan_image.ScanMatch(scan_image.OK, cp, (1.0, 2.0), (1.0, 1.0), (cand,)))
    scan_image.clear_caches()
    d = GlViewerDialog(None)
    d.resize(1280, 820)
    d.show()
    d.open_path(cp)
    for _ in range(200):
        _pump(1)
        if d._gl is not None:
            break
        QTest.qWait(20)
    assert d._gl is not None and d._gl.shape == (120, 300)
    assert [(r.x, r.y, r.w, r.h) for r in d.canvas.rois.rois] == [(146, 56, 7, 7)]
    d.close()


def test_open_failure_shows_message_not_crash(styled_qapp, tmp_path):
    d = GlViewerDialog(None)
    d.resize(1280, 820)
    d.show()
    (tmp_path / "bad.png").write_bytes(b"nope")
    d.open_path(tmp_path / "bad.png")
    for _ in range(100):
        QTest.qWait(20)
        _pump(1)
        if d.toast.text():
            break
    assert "열지 못했어요" in d.toast.text() and d._gl is None
    d.close()


def test_escape_cancels_drawing_and_otherwise_propagates(dlg):
    c = dlg.canvas
    dlg._set_tool(R.RECT)
    QTest.mousePress(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(c, 30, 20))
    QTest.mouseMove(c, _pt(c, 80, 60))
    assert c._drag and c._drag["mode"] == "draw"
    QTest.keyClick(c, Qt.Key.Key_Escape)
    assert c._drag is None
    QTest.mouseRelease(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(c, 80, 60))
    assert c.rois.rois == []


def test_number_keys_do_not_switch_tools(dlg):
    c = dlg.canvas
    for k in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_6):
        QTest.keyClick(c, k)
        assert c.tool == "move" and dlg._tool_btns["move"].isChecked()


def test_wheel_zoom_keeps_point_under_cursor(dlg):
    c = dlg.canvas
    pt = QPointF(c.width() / 2, c.height() / 2)
    before = c.to_image(pt.x(), pt.y())
    c.zoom_by(1.5, pt)
    after = c.to_image(pt.x(), pt.y())
    assert after[0] == pytest.approx(before[0]) and after[1] == pytest.approx(before[1])


# ------------------------------------------------------------------ 진입점
def test_setup_tool_rail_has_gl_button_that_opens_viewer(styled_qapp, monkeypatch):
    from aoi_verification.app.ui.pages import setup_page as sp
    from aoi_verification.app.ui.widgets import gl_viewer_dialog as gv
    seen = []
    monkeypatch.setattr(sp.sheets, "run", lambda dlg, **kw: seen.append((dlg, kw)) or 0)
    monkeypatch.setattr(sp.config, "GL_HISTOGRAM_READY", True)   # 준비되면 이렇게 열린다
    page = sp.SetupPage()
    try:
        assert page.gl_btn.text() == "GL 히스토그램"
        assert page._tool_rail.isAncestorOf(page.gl_btn)
        page.gl_btn.click()
        # 팝업(시트)이 아니라 오른쪽 화면이 GL 로 바뀐다(사용자 요청 2026-10-30).
        assert seen == []
        assert page.current_page() == "gl"
        assert isinstance(page.tool_dialog("gl"), gv.GlViewerDialog)
    finally:
        page.deleteLater()


def test_image_info_has_gl_button_enabled_only_with_photo(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import image_info_dialog as ii
    from aoi_verification.app.ui.widgets import gl_viewer_dialog as gv
    monkeypatch.setattr(ii, "_async_info", lambda: False)
    monkeypatch.setattr(ii.config, "GL_HISTOGRAM_READY", True)
    d = ii.ImageInfoDialog(None)
    assert not d.gl_btn.isHidden()
    assert not d.gl_btn.isEnabled()
    assert d.gl_btn.autoDefault() is False                  # Enter 가 GL 뷰어를 재실행하지 않는다
    p = tmp_path / "a.png"
    from PIL import Image
    Image.new("L", (40, 30), 90).save(p)                    # 회색 사진 — GL 로 볼 수 있다
    d.show_image(p)
    assert _wait(d.gl_btn.isEnabled)
    seen = []
    monkeypatch.setattr(ii.sheets, "run", lambda dlg, **kw: seen.append(dlg) or 0)
    opened = []
    monkeypatch.setattr(gv.GlViewerDialog, "open_path", lambda self, path, **kw: opened.append(path))
    d.gl_btn.click()
    assert len(seen) == 1 and isinstance(seen[0], gv.GlViewerDialog)
    assert [str(x) for x in opened] == [str(p)]
    seen[0].deleteLater()
    d.deleteLater()


# ------------------------------------------------------------------ 두 사진 비교
def _b_view(data):
    return loader.ViewLoad(loader.GlImage(data, 16, False, "b.tif", []), source_path="b.tif")


def test_compare_shares_rois_views_and_thresholds(dlg):
    c, cb = dlg.canvas, dlg.canvas_b
    b = (dlg._gl.data // 2).astype(np.uint16)
    dlg.show_compare(_b_view(b))
    _pump()
    assert dlg.pane_b.isVisible() and cb.rois is c.rois
    assert dlg.compare_btn.text() == "사진 하나로 돌아가기"
    # B 에서 그려도 A 에도 같은 ROI 가 생긴다
    dlg._set_tool(R.RECT)
    _drag(cb, _pt(cb, 30, 20), _pt(cb, 80, 50))
    assert len(c.rois.rois) == 1 and (c.rois.active().x, c.rois.active().w) == (30, 50)
    # A 를 확대하면 B 도 같은 자리를 본다
    c.zoom_by(2.0, QPointF(40, 40))
    assert cb.view_state() == pytest.approx(c.view_state())
    cb.zoom_by(0.5, QPointF(60, 60))
    assert c.view_state() == pytest.approx(cb.view_state())
    # 같은 임계값으로 두 사진을 칠한다(B 는 값이 절반이라 하한 이하가 더 많다)
    below_a = int((dlg._gl.data <= dlg._lo).sum())
    below_b = int((b <= dlg._lo).sum())
    assert below_b > below_a
    assert int((cb._rgb[..., 2] == analysis.LOW_RGB[2]).sum()) >= below_b * 0.9


def test_compare_stat_rows_are_a_b_and_delta(dlg):
    b = np.clip(dlg._gl.data.astype(np.int64) + 1000, 0, 65535).astype(np.uint16)
    dlg.show_compare(_b_view(b))
    r = _add_rect(dlg, x=10, y=10, w=20, h=10)
    dlg._refresh()
    rows = dlg._stat_rows()
    assert [x["name"] for x in rows] == ["사진 전체 · A", "사진 전체 · B", "사진 전체 · B−A",
                                         f"{r.name} · A", f"{r.name} · B", f"{r.name} · B−A"]
    sub_a, sub_b = dlg._gl.data[10:20, 10:30], b[10:20, 10:30]
    assert rows[5]["mean"] == pytest.approx(float(sub_b.mean()) - float(sub_a.mean()))
    assert dlg.hist.compare_mode() and [r[0] for r in dlg.hist.rows()] == [0, 1]


def test_compare_rejects_other_size_or_bits(dlg):
    dlg.show_compare(_b_view(np.zeros((H + 1, W), np.uint16)))
    assert dlg._gl_b is None and "A" in dlg.toast.text() and not dlg.pane_b.isVisible()
    dlg.show_compare(loader.ViewLoad(loader.GlImage(np.zeros((H, W), np.uint8), 8, False, "b", [])))
    assert dlg._gl_b is None


def test_compare_toggle_closes_and_new_a_with_other_size_closes_compare(dlg):
    dlg.show_compare(_b_view(dlg._gl.data.copy()))
    assert dlg._gl_b is not None
    dlg.compare_btn.click()
    assert dlg._gl_b is None and not dlg.pane_b.isVisible()
    assert dlg.compare_btn.text() == "두 사진 비교"
    dlg.show_compare(_b_view(dlg._gl.data.copy()))
    dlg.show_view(loader.ViewLoad(loader.GlImage(np.zeros((10, 10), np.uint16), 16, False, "n", [])))
    assert dlg._gl_b is None and not dlg.pane_b.isVisible()
    assert "닫았어요" in dlg.toast.text()


def test_compare_saves_both_in_xlsx_and_png(dlg, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    _add_rect(dlg, x=10, y=10, w=20, h=10)
    dlg.show_compare(_b_view((dlg._gl.data // 2).astype(np.uint16)))
    dlg.save_xlsx(tmp_path / "c.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "c.xlsx")
    assert wb.sheetnames == ["분석 조건", "ROI 통계", "히스토그램"]
    heads = [c_.value for c_ in wb["히스토그램"][1]]
    assert heads == ["GL", "사진 전체 A", "사진 전체 B", "사각형 1 A", "사각형 1 B"]
    stats = wb["ROI 통계"]
    assert stats.max_row == 1 + 6
    assert [stats.cell(i, 1).value for i in range(2, 8)][-1] == "사각형 1 · B−A"
    dlg.save_png(tmp_path / "c.png")
    assert (tmp_path / "c.png").exists() and (tmp_path / "c_B.png").exists()


def test_compare_hover_works_from_either_canvas(dlg):
    dlg.show_compare(_b_view(dlg._gl.data.copy()))
    _pump()                       # 분할 창이 두 칸으로 나뉘며 A 가 다시 맞춰지고 B 가 따라온다
    assert dlg.canvas_b.view_state() == pytest.approx(dlg.canvas.view_state())
    QTest.mouseMove(dlg.canvas_b, _pt(dlg.canvas_b, 20, 30))
    _pump()
    assert "x 20" in dlg.status.text()


def test_drop_and_paste_open_first_file_only(styled_qapp, tmp_path, monkeypatch):
    """끌어놓기·붙여넣기: 첫 사진 파일 하나만 연다(A 가 백그라운드로 읽혀 B 는 같이 못 연다) · 못 쓰는 것은 안내."""
    from PyQt6.QtCore import QMimeData, QUrl
    from aoi_verification.app.ui import drop_paths
    from aoi_verification.app.ui.widgets import gl_viewer_dialog as G
    d = GlViewerDialog(None)
    try:
        opened = []
        monkeypatch.setattr(d, "open_path", lambda p, **k: opened.append(p.name))
        a, b = tmp_path / "a.tif", tmp_path / "b.png"
        a.write_bytes(b"")
        b.write_bytes(b"")
        (tmp_path / "t.txt").write_text("x")
        d._open_first_file([str(tmp_path / "t.txt"), str(a), str(b)])
        assert opened == ["a.tif"]
        d._open_first_file([str(tmp_path / "t.txt")])
        assert d.toast.text() == G._KO.DROP_WRONG_KIND_IMAGE and opened == ["a.tif"]
        d._on_paste_no_path(True)
        assert d.toast.text() == G._KO.DROP_NO_PATH_IMAGE
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(str(a))])
        assert drop_paths.local_paths(m) == [str(a)]
    finally:
        d.close()


# ------------------------------------------------------------------ 화면 구성(2026-11 개편)
def test_head_has_one_primary_and_all_buttons_are_32px(dlg):
    from PyQt6.QtWidgets import QPushButton
    heads = [dlg.open_btn, dlg.compare_btn, dlg.png_btn, dlg.xlsx_btn]
    assert [b.property("role") for b in heads] == ["textAccent", "textAction", "ghost", "primary"]
    assert [b for b in dlg.findChildren(QPushButton) if b.property("role") == "primary"] == [dlg.xlsx_btn]
    _pump()
    assert {b.height() for b in heads} == {32}, [(b.text(), b.height()) for b in heads]
    assert dlg.png_rois.isCheckable() and dlg.png_rois.isChecked()     # PNG 메뉴 안의 선택


def test_inspector_keeps_its_width_when_the_window_grows(dlg):
    dlg.resize(1100, 700)
    _pump()
    w0, c0 = dlg.hist.parentWidget().width(), dlg.canvas.width()
    dlg.resize(1500, 700)
    _pump()
    assert dlg.hist.parentWidget().width() == w0 == GLV._INSPECTOR_W
    assert dlg.canvas.width() == c0 + 400                            # 늘어난 만큼 사진이 커진다


def test_tools_float_on_the_photo(dlg):
    assert all(b.parentWidget().parentWidget() is dlg.canvas for b in dlg._tool_btns.values())
    assert dlg.undo_btn.parentWidget().parentWidget() is dlg.canvas
    assert dlg.defect_btn.parentWidget().parentWidget() is dlg.canvas
    assert dlg.zoom_label.text() == f"{int(round(dlg.canvas.scale() * 100))}%"


def _axis_x(h, gl):
    h._layout()
    return int(h._gl_x(gl))


def _axis_y(h):
    return int(h._plot.bottom() + 14)            # 밝기 띠 · 손잡이 높이


def test_histogram_axis_drag_moves_values_and_keeps_order(dlg):
    """D1 — 아래 축 하나가 임계값 조작축이다: 손잡이를 끌면 값이 바뀌고, 하한 < 상한을 지킨다."""
    h = dlg.hist
    y = _axis_y(h)
    QTest.mouseClick(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(_axis_x(h, 4000), y))
    _pump()
    assert dlg._lo == h._x_gl(_axis_x(h, 4000)) and h.active() == "lo"
    hx = _axis_x(h, dlg._hi)                                  # 손잡이를 잡고 끌어 옮긴다
    QTest.mousePress(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hx, y))
    QTest.mouseMove(h, QPoint(hx - 40, y))
    QTest.mouseRelease(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(hx - 40, y))
    _pump()
    assert h.active() == "hi" and dlg._hi < 65000
    h.thresholdDragged.emit("lo", dlg._hi + 30)               # 하한을 상한 너머로 끌면 상한 바로 아래에서 멈추고 알린다
    _pump()
    assert dlg._lo == dlg._hi - 1 and dlg.thr_note.isVisibleTo(dlg) and dlg.thr_note.text()
    assert dlg.lo_spin.value() == dlg._lo and dlg.hist._lo == dlg._lo


def test_histogram_axis_keyboard_moves_the_active_handle(dlg):
    """방향키는 차트가 받는다 — ←→ 1(Shift 10), ↑↓ 손잡이 바꾸기."""
    h = dlg.hist
    QTest.mouseClick(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(_axis_x(h, 4000), _axis_y(h)))
    _pump()
    lo = dlg._lo
    QTest.keyClick(h, Qt.Key.Key_Right)
    assert dlg._lo == lo + 1
    QTest.keyClick(h, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert dlg._lo == lo + 11
    QTest.keyClick(h, Qt.Key.Key_Left)
    assert dlg._lo == lo + 10
    QTest.keyClick(h, Qt.Key.Key_Up)                          # 상한 손잡이로
    hi = dlg._hi
    QTest.keyClick(h, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert h.active() == "hi" and dlg._hi == hi - 10 and dlg._lo == lo + 10


def test_threshold_chip_opens_an_input_in_place_once_below_the_axis(dlg):
    h = dlg.hist
    assert not dlg.lo_spin.isVisibleTo(dlg) and not dlg.hi_spin.isVisibleTo(dlg)
    _pump()
    chips = h.chip_rects()
    assert set(chips) == {"lo", "hi"}
    assert all(c.top() > h._plot.bottom() for c in chips.values())      # 칩은 그래프 위가 아니라 축 아래에 한 번씩
    QTest.mouseClick(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, chips["hi"].center().toPoint())
    _pump()
    assert dlg.hi_spin.isVisibleTo(dlg) and not dlg.lo_spin.isVisibleTo(dlg)
    want = (dlg._lo + dlg._hi) // 2
    dlg.hi_spin.setValue(want)
    dlg.hi_spin.editingFinished.emit()
    _pump()
    assert dlg._hi == want and not dlg.hi_spin.isVisibleTo(dlg)


def test_axis_has_three_ticks_and_chips_never_overlap(styled_qapp):
    from aoi_verification.app.ui.widgets import gl_charts as C
    assert C.axis_ticks(0, 255) == [0, 128, 255] and C.axis_ticks(5, 5) == [5]
    h = C.HistogramChart()
    h.resize(330, 240)
    hist = np.zeros(256, np.int64)
    hist[40:200] = 10
    h.set_series([("전체", "#888888", hist)], 256)
    h.set_thresholds(70, 200)
    c = h.chip_rects()
    assert c["lo"].right() <= c["hi"].left()
    h.set_thresholds(126, 130)                                  # 두 칩이 가운데 눈금(128) 자리에 몰려도 겹치지 않는다
    c = h.chip_rects()
    assert c["lo"].right() <= c["hi"].left() and not h.grab().toImage().isNull()


def test_histogram_is_empty_and_disabled_until_a_photo_opens(styled_qapp):
    d = GlViewerDialog(None)
    try:
        assert not d.hist.isEnabled() and d.hist.chip_rects() == {}
        rng = np.random.default_rng(1)
        d.show_view(loader.ViewLoad(loader.GlImage(rng.integers(0, 255, (H, W)).astype(np.uint8),
                                                   8, False, "y.png", [])))
        assert d.hist.isEnabled() and set(d.hist.chip_rects()) == {"lo", "hi"}
    finally:
        d.close()
        d.deleteLater()


def test_scope_segment_is_first_in_the_inspector(dlg):
    from PyQt6.QtWidgets import QLabel
    host = dlg.scope.parentWidget()
    labels = [w for w in host.findChildren(QLabel) if w.text() in ("색칠 범위", "GL 히스토그램")]
    assert [w.text() for w in labels] == ["색칠 범위", "GL 히스토그램"]
    assert dlg.scope.y() < dlg.hist.y()
    assert not any(w.text() == "판정 임계값" for w in host.findChildren(QLabel))


def test_status_bar_shows_hover_readout_and_only_zoom_on_the_right(dlg):
    """D3 — 왼쪽: 마우스 아래 x y GL · 구역(색은 역할), 오른쪽: 배율만.  고정 힌트 · 크기 중복은 없다."""
    from aoi_verification.app import i18n
    assert not hasattr(dlg, "hint") and not hasattr(i18n.KO, "GL_TOOLBAR_HINT")
    assert dlg.status.text() == "" and dlg.zone_word.text() == "" and dlg.hist.hover_gl() is None
    assert dlg.zoom_status.text() == f"배율 {int(round(dlg.canvas.scale() * 100))}%"
    assert "bit" not in dlg.zoom_status.text() and "×" not in dlg.zoom_status.text()
    lo, hi = dlg._lo, dlg._hi
    data = dlg._gl.data
    cases = []
    for mask, role, word in ((data <= lo, "glZoneLow", i18n.KO.GL_ZONE_LOW),
                             ((data > lo) & (data < hi), "glZoneMid", i18n.KO.GL_ZONE_MID),
                             (data >= hi, "glZoneHigh", i18n.KO.GL_ZONE_HIGH)):
        ys, xs = np.where(mask)
        if len(xs):
            cases.append((int(xs[0]), int(ys[0]), role, word))
    assert len(cases) >= 2
    for x, y, role, word in cases:
        QTest.mouseMove(dlg.canvas, _pt(dlg.canvas, x, y))
        _pump()
        gl = int(data[y, x])
        assert dlg.status.text() == f"x {x}  y {y}   GL {gl}"
        assert word in dlg.zone_word.text() and dlg.zone_word.property("role") == role
        assert dlg.hist.hover_gl() == gl                          # 히스토그램에 그 GL 의 세로선
    dlg.canvas.leaveEvent(None)
    _pump()
    assert dlg.status.text() == "" and dlg.zone_word.text() == "" and dlg.hist.hover_gl() is None


def test_hover_line_is_drawn_on_the_histogram_and_cleared(styled_qapp):
    from aoi_verification.app.ui.widgets import gl_charts as C
    h = C.HistogramChart()
    h.resize(330, 240)
    hist = np.zeros(256, np.int64)
    hist[40:60] = 10
    h.set_series([("전체", "#888888", hist)], 256)
    h.set_thresholds(70, 200)

    def darkest(x):
        img = h.grab().toImage()
        return min(img.pixelColor(xx, y).red() for xx in (x - 1, x, x + 1)
                   for y in range(int(h._plot.top()) + 2, int(h._plot.bottom()) - 2))
    h.set_hover_gl(128)
    h._layout()
    x = int(round(h._gl_x(128)))
    with_line = darkest(x)
    h.set_hover_gl(None)
    assert darkest(x) > with_line and h.hover_gl() is None


# ------------------------------------------------------------------ 히스토그램 개편(시안 A)
def _two_rois(dlg):
    a = _add_rect(dlg, x=10, y=10, w=40, h=30)
    b = _add_rect(dlg, x=100, y=60, w=40, h=40, kind=R.CIRCLE)
    dlg._refresh()                  # 그리기는 roisChanged 로 갱신을 부른다 — 테스트는 직접
    return a, b


def test_pct_text_and_label_spread_never_overlap():
    from aoi_verification.app.ui.widgets import gl_charts as C
    assert C.pct_text(0.0) == "0%" and C.pct_text(92.84) == "92.8%" and C.pct_text(100.0) == "100.0%"
    # 구역이 좁아 가운데 값이 서로 겹치면 벌려서 겹치지 않게, 그리고 범위 안에 둔다
    xs = C.spread_labels([10, 12, 14], [40, 40, 40], 0, 130, 4)
    for (x1, w1), (x2, _w2) in zip(zip(xs, [40] * 3), zip(xs[1:], [40] * 3)):
        assert x1 + w1 + 4 <= x2 + 1e-9
    assert xs[0] >= 0 and xs[-1] + 40 <= 130 + 1e-9
    # 넉넉하면 제자리(가운데 맞춤)
    assert C.spread_labels([50, 100, 150], [20, 20, 20], 0, 200, 4) == [40, 90, 140]


def test_stack_heights_selected_row_is_taller_and_total_fits():
    from aoi_verification.app.ui.widgets import gl_charts as C
    hs = C.stack_heights(3, 2, 300.0, 20.0)
    assert len(hs) == 3 and hs[2] > hs[0] == hs[1]
    assert sum(hs) == pytest.approx(300.0)
    assert min(C.stack_heights(6, 0, 60.0, 20.0)) >= 20.0       # 모자라도 바닥 높이는 지킨다


def test_by_roi_button_needs_rois_and_is_off_during_compare(dlg):
    btn = dlg.by_roi_btn
    assert btn.isCheckable() and not btn.isEnabled() and btn.toolTip()      # ROI 없음 — 이유를 말한다
    _two_rois(dlg)
    _pump()
    assert btn.isEnabled()
    btn.click()
    _pump(5)
    assert btn.isChecked()
    btn.click()
    _pump(5)
    btn.click()
    _pump(5)
    dlg.show_compare(_b_view((dlg._gl.data // 2).astype(np.uint16)))
    _pump(5)
    assert not btn.isEnabled() and not btn.isChecked() and btn.toolTip()
    assert dlg.hist.compare_mode() and not dlg.by_roi_btn.isChecked()


def test_by_roi_stacks_all_then_each_roi_and_marks_the_selected_one(dlg):
    a, b = _two_rois(dlg)
    dlg.canvas.rois.selected = b.id
    dlg.by_roi_btn.click()
    _pump(5)
    h = dlg.hist
    assert h.stack_mode()
    assert [r[0] for r in h.rows()] == [-1, a.id, b.id]
    assert [r[1] for r in h.rows()] == ["사진 전체", a.name, b.name]
    assert h.selected_row() == b.id
    # 줄마다 구역 비율이 따로 계산된다
    ratios = [analysis.zone_ratios(r[3], dlg._lo, dlg._hi) for r in h.rows()]
    assert all(sum(x) == pytest.approx(100.0) for x in ratios)


def test_clicking_a_row_selects_that_roi_through_the_selection_flow(dlg):
    a, b = _two_rois(dlg)
    dlg.by_roi_btn.click()
    _pump(5)
    h = dlg.hist
    seen = []
    dlg.canvas.selectionChanged.connect(lambda: seen.append(dlg.canvas.rois.selected))
    rects = h.row_rects()
    assert len(rects) == 3
    # 임계선에서 떨어진 자리(줄 한가운데 가까운 x)를 누른다
    x = int(h._plot.center().x())
    x = x if min(abs(x - h._gl_x(dlg._lo)), abs(x - h._gl_x(dlg._hi))) > 12 else int(h._plot.left()) + 4
    QTest.mouseClick(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(x, int(rects[1].center().y())))
    _pump(5)
    assert dlg.canvas.rois.selected == a.id and seen[-1] == a.id and h.selected_row() == a.id
    QTest.mouseClick(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(x, int(rects[0].center().y())))
    _pump(5)
    assert dlg.canvas.rois.selected is None and h.selected_row() == -1
    assert dlg._lo < dlg._hi                                  # 줄을 눌러도 임계값은 그대로


def test_threshold_lines_still_drag_in_stack_mode_and_clear_when_rois_vanish(dlg):
    _two_rois(dlg)
    dlg.by_roi_btn.click()
    _pump(5)
    h = dlg.hist
    h._layout()
    y = int(h.row_rects()[1].center().y())
    x0 = int(h._gl_x(dlg._lo))
    QTest.mousePress(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, y))
    QTest.mouseMove(h, QPoint(x0 + 20, y))
    QTest.mouseRelease(h, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0 + 20, y))
    _pump(5)
    assert dlg._lo > h._x_gl(x0) - 1
    dlg.canvas.rois.clear()
    dlg._refresh()
    _pump(5)
    assert not dlg.by_roi_btn.isEnabled() and not dlg.hist.stack_mode()


# ------------------------------------------------------------------ 2026-11-17 디자인 검토 반영
from PyQt6.QtWidgets import QPushButton  # noqa: E402


def test_empty_viewer_has_open_as_only_primary_and_disables_the_rest(styled_qapp):
    d = GlViewerDialog(None)
    try:
        d.resize(1280, 820)
        d.show()
        _pump()
        assert d.open_btn.property("role") == "primary" and d.xlsx_btn.property("role") != "primary"
        assert [b for b in d.findChildren(QPushButton) if b.property("role") == "primary"] == [d.open_btn]
        assert not any(b.isEnabled() for b in (d.xlsx_btn, d.png_btn, d.compare_btn, d.clear_btn,
                                               d.undo_btn, *d._tool_btns.values()))
        assert not d.scope.isEnabled()
        assert not d._zoom_bar.isVisibleTo(d)
        rng = np.random.default_rng(1)
        d.show_view(loader.ViewLoad(loader.GlImage(rng.integers(0, 255, (H, W)).astype(np.uint8),
                                                   8, False, "y.png", [])))
        _pump()
        assert d.xlsx_btn.property("role") == "primary" and d.open_btn.property("role") == "textAccent"
        assert all(b.isEnabled() for b in (d.xlsx_btn, d.png_btn, d.compare_btn, *d._tool_btns.values()))
        assert d.scope.isEnabled() and d._zoom_bar.isVisibleTo(d)
        assert {b.height() for b in (d.open_btn, d.compare_btn, d.png_btn, d.xlsx_btn)} == {32}
    finally:
        d.close()


def test_warnings_stay_in_the_status_bar_while_the_photo_is_open(styled_qapp):
    rng = np.random.default_rng(2)
    img = loader.GlImage(rng.integers(0, 255, (H, W)).astype(np.uint8), 8, False, "j.jpg",
                         [loader.W_JPEG])
    d = _make(styled_qapp, view=loader.ViewLoad(img, source_path="j.jpg"))
    try:
        assert d.note.isVisibleTo(d) and GLV._KO.GL_WARN_JPEG in d.note.text()
        assert d.note.property("role") == "warn" and not hasattr(d, "hint")
        d._toast("x")                                    # 토스트가 사라져도 경고는 남는다
        assert d.note.isVisibleTo(d)
        d.show_view(loader.ViewLoad(loader.GlImage(img.data, 8, False, "k.png", [])))
        assert not d.note.isVisibleTo(d)
    finally:
        d.close()


def test_compare_shows_two_stacked_rows_with_own_ratios_and_no_legend(dlg):
    """D4 — A · B 두 줄(이름 + 색 견본 + 자기 구역 비율), 축 · 임계선은 하나, 범례는 없다."""
    b = loader.GlImage((dlg._gl.data // 2).astype(dlg._gl.data.dtype), dlg._gl.bits, False, "b.tif", [])
    dlg.show_compare(loader.ViewLoad(b, source_path="b.tif"))
    _pump()
    assert GLV._CMP_A in dlg.cap_a.text() and GLV._CMP_B in dlg.cap_b.text() and "■" in dlg.cap_a.text()
    h = dlg.hist
    assert h.compare_mode() and not h._series
    rows = h.rows()
    assert [r[1] for r in rows] == ["A · x.tif", "B · b.tif"]
    assert [r[2] for r in rows] == [GLV._CMP_A, GLV._CMP_B]               # 기존 A/B 색 유지
    assert len(h.row_rects()) == 2
    ra = analysis.zone_ratios(rows[0][3], dlg._lo, dlg._hi)
    rb = analysis.zone_ratios(rows[1][3], dlg._lo, dlg._hi)
    assert ra != rb and sum(ra) == pytest.approx(100.0) and sum(rb) == pytest.approx(100.0)
    h.resize(330, 480)
    img = h.grab().toImage()
    from PyQt6.QtGui import QColor

    def has(col, rect):
        c0 = QColor(col)
        return any(abs(img.pixelColor(x, y).red() - c0.red()) < 8 and abs(img.pixelColor(x, y).green() - c0.green()) < 8
                   and abs(img.pixelColor(x, y).blue() - c0.blue()) < 8
                   for x in range(int(rect.left()), int(rect.left()) + 24) for y in range(int(rect.top()), int(rect.top()) + 16))
    rects = h.row_rects()
    assert has(GLV._CMP_A, rects[0]) and has(GLV._CMP_B, rects[1])        # 줄 이름 앞 선 견본
    assert set(h.chip_rects()) == {"lo", "hi"}                              # 축 아래 값 칩은 하나씩만


def test_gl_inspector_segments_share_height_and_threshold_ticks_align(dlg):
    dlg.show()
    _pump()
    heights = {dlg.scope.button(k).height() for k in dlg.scope.keys()} | {dlg.by_roi_btn.height()}
    assert heights == {32}
    assert all(dlg.scope.button(k).property("grade") == "gl" for k in dlg.scope.keys())


def test_defect_roi_is_one_sixth_of_box_and_centered(styled_qapp):
    d = _make(styled_qapp, view=_defect_view((20, 10, 60, 60)))
    (r,) = d.canvas.rois.rois
    assert (r.w, r.h) == (10, 10)
    assert (r.x + r.w / 2, r.y + r.h / 2) == (50, 40)
    d.close()


def test_scan_missing_shows_reason_and_toast(styled_qapp):
    v = loader.ViewLoad(loader.GlImage(np.full((H, W), 9, np.uint8), 8, True, "c.jpg", []),
                        scan_missing=True, source_path="c.jpg", scan_status="no_candidate")
    d = _make(styled_qapp, view=v)
    assert "Defect 위치를 담은 Scan 사진이 목록에 없음" in d.note.text()
    assert "Scan 사진을 못 찾았습니다" in d.toast.text()
    d.close()




def test_long_notes_do_not_widen_the_viewer(styled_qapp):
    """긴 안내(상태줄 note · 머리줄 toast)가 한 줄로 서서 화면 최소 폭을 1332px 까지 키웠다 —
    1100px 창에서 가로 스크롤이 생겼다(실측 2026-11-24).  줄을 바꿔 최소 폭을 지킨다."""
    v = loader.ViewLoad(loader.GlImage(np.full((H, W), 9, np.uint8), 8, True, "c.jpg", []),
                        scan_missing=True, source_path="c.jpg", scan_status="no_candidate")
    d = _make(styled_qapp, view=v)
    long = "아주 긴 안내 문장이 이어진다 · " * 12
    d._set_note([long, long], warn=True)
    d.toast.setText(long)
    _pump()
    assert d.note.wordWrap() and d.toast.wordWrap()
    # 1100px 창 − 접힌 메뉴(76px) − 여백 안에 들어가야 한다.
    assert d.minimumSizeHint().width() <= 1000, d.minimumSizeHint().width()
    d.close()


def test_default_save_name_follows_tool_rule(dlg, tmp_path, monkeypatch):
    """기본 저장 이름은 다른 도구와 같은 규칙 ``GL_<사진이름>_<YYYYMMDD_HHMM>`` 이고,
    폴더도 같은 결과 폴더다(사진의 NAS 폴더가 아니라)."""
    import re
    from pathlib import Path
    from aoi_verification.app.utils import paths
    monkeypatch.setattr(paths, "results_dir", lambda: tmp_path)
    for suffix in (".xlsx", ".png"):
        p = Path(dlg._default_name(suffix))
        assert p.parent == tmp_path
        assert re.fullmatch(r"GL_x_\d{8}_\d{4}" + re.escape(suffix), p.name), p.name
