"""GL 히스토그램 뷰어 화면 — 헤드리스 조작 검증.

사용자 요구 6가지를 그대로 못 박는다:

1. 타원은 없다(도형은 사각형·정사각형·원).  원은 늘 정원이다.
2. 선택한 ROI 는 방향키로 움직인다(Shift 10 px, 연타는 되돌리기 한 번).
3. 수평선·수직선은 **보이는 화면 전체**를 가로지르고, 마우스로 끌어 옮기고, 확대·축소를 따라가며,
   수평/수직을 각각 끌 수 있다.
4. 원(도형) 크기를 조금씩 조절한다(+/−, 원은 반지름 1 px).
5. 도형 버튼: 사각형·정사각형·원.
6. 컬러 사진은 Scan 의 결함 영역을 확대해 보여 준다.
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
from aoi_verification.app.ui.widgets.gl_viewer_dialog import GlViewerDialog, GlViewerPanel  # noqa: E402

W, H = 200, 150


def _pump(n: int = 3) -> None:
    for _ in range(n):
        QApplication.processEvents()


def _make(styled_qapp, data=None, *, bits=16, view=None):
    if data is None:
        rng = np.random.default_rng(3)
        hi = (1 << bits) - 1
        data = rng.integers(0, hi, size=(H, W)).astype(np.uint16 if bits > 8 else np.uint8)
    dlg = GlViewerPanel(None)
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


def _near(c, rgb, tol=70) -> bool:
    return all(abs(a - b) <= tol for a, b in zip((c.red(), c.green(), c.blue()), rgb))


# ------------------------------------------------------------------ 1·5. 도구
def test_tools_are_shapes_without_ellipse(dlg):
    assert TOOLS == ("move", R.HLINE, R.VLINE, R.RECT, R.SQUARE, R.CIRCLE)
    labels = [b.caption() for b in dlg._tool_btns.values()]
    assert not any("타원" in t for t in labels)
    for need in ("사각형", "정사각형", "원"):
        assert any(need in t for t in labels)


def test_drawing_each_shape_by_dragging(dlg):
    c = dlg.canvas
    for tool, want in ((R.RECT, (30, 20, 50, 30)), (R.SQUARE, (30, 20, 50, 50))):
        dlg._set_tool(tool)
        _drag(c, _pt(c, 30, 20), _pt(c, 80, 50))
        r = c.rois.active()
        assert r.kind == tool and (r.x, r.y, r.w, r.h) == want
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


def test_arrow_moves_line_only_along_its_axis(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 50)
    QTest.keyClick(c, Qt.Key.Key_Right)
    assert c.rois.active().pos == 50
    QTest.keyClick(c, Qt.Key.Key_Down)
    assert c.rois.active().pos == 51
    c.rois.add_line(R.VLINE, 70)
    QTest.keyClick(c, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert c.rois.active().pos == 80


def test_arrows_without_selection_do_nothing(dlg):
    c = dlg.canvas
    assert c.nudge(1, 0) is False


# ------------------------------------------------------------------ 3. 전체 폭 선
def _line_pixel(canvas, kind, pos, along):
    """선 한가운데에서 ``along`` 만큼 떨어진 화면 픽셀 색."""
    img = canvas.grab().toImage()
    if kind == R.HLINE:
        y = int(round(canvas.to_screen(0, pos + 0.5).y()))
        return img.pixelColor(max(0, min(along, img.width() - 1)), y)
    x = int(round(canvas.to_screen(pos + 0.5, 0).x()))
    return img.pixelColor(x, max(0, min(along, img.height() - 1)))


def test_hline_spans_the_whole_view_width(dlg):
    c = dlg.canvas
    r = c.rois.add_line(R.HLINE, 60)
    c.roisChanged.emit()
    _pump()
    for x in (3, c.width() // 2, c.width() - 4):
        assert _near(_line_pixel(c, R.HLINE, 60, x), (0x7B, 0xE0, 0x8A)), x
    assert r.kind == R.HLINE


def test_vline_spans_the_whole_view_height(dlg):
    c = dlg.canvas
    c.rois.add_line(R.VLINE, 90)
    c.roisChanged.emit()
    _pump()
    for y in (3, c.height() // 2, c.height() - 4):
        assert _near(_line_pixel(c, R.VLINE, 90, y), (0x7B, 0xE0, 0x8A)), y


def test_line_follows_zoom_and_pan(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 60)
    before = c.to_screen(0, 60.5).y()
    c.zoom_by(2.0, QPointF(10, 10))
    after = c.to_screen(0, 60.5).y()
    assert after != before
    _pump()
    assert _near(_line_pixel(c, R.HLINE, 60, c.width() - 4), (0x7B, 0xE0, 0x8A)) or \
        not (0 <= after < c.height())          # 화면 밖으로 나갔으면 그려지지 않는 게 맞다
    # 같은 이미지 좌표가 새 배율에서도 같은 픽셀을 가리킨다
    assert c._pixel_at(100, after) [1] == 60


def test_click_with_line_tool_creates_full_line_at_pixel(dlg):
    c = dlg.canvas
    dlg._set_tool(R.HLINE)
    p = _pt(c, 80, 45)
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    r = c.rois.active()
    assert r.kind == R.HLINE and r.pos == 45
    dlg._set_tool(R.VLINE)
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
    assert c.rois.active().kind == R.VLINE and c.rois.active().pos == 80


def test_drag_line_with_mouse_moves_it(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 50)
    dlg._set_tool("move")
    a = QPoint(c.width() // 2, int(round(c.to_screen(0, 50.5).y())))
    b = QPoint(a.x(), int(round(c.to_screen(0, 90.5).y())))
    _drag(c, a, b)
    assert c.rois.rois[0].pos == 90
    c.rois.add_line(R.VLINE, 40)
    a = QPoint(int(round(c.to_screen(40.5, 0).x())), c.height() // 2)
    b = QPoint(int(round(c.to_screen(120.5, 0).x())), a.y())
    _drag(c, a, b)
    assert c.rois.active().pos == 120


def test_line_drag_is_one_undo_step_and_stays_in_image(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 50)
    a = QPoint(c.width() // 2, int(round(c.to_screen(0, 50.5).y())))
    _drag(c, a, QPoint(a.x(), c.height() + 500))             # 화면 밖까지 끌어도 이미지 안
    assert c.rois.rois[0].pos == H - 1
    QTest.keyClick(c, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert c.rois.rois[0].pos == 50


def test_show_hide_buttons_are_independent(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 40)
    c.rois.add_line(R.VLINE, 70)
    dlg.show_h.setChecked(False)
    _pump()
    hl, vl = c.rois.rois
    assert not c.rois.visible(hl) and c.rois.visible(vl)
    assert not _near(_line_pixel(c, R.HLINE, 40, c.width() - 4), (0x7B, 0xE0, 0x8A), 30)
    dlg.show_h.setChecked(True)
    dlg.show_v.setChecked(False)
    assert c.rois.visible(hl) and not c.rois.visible(vl)
    # 숨긴 선은 집히지도 않는다
    assert c._hit_line(QPointF(c.to_screen(70.5, 0).x(), c.height() / 2)) is None


def test_line_tools_only_grab_their_own_kind_shape_tools_grab_none(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 50)
    pt = QPointF(c.width() / 2, c.to_screen(0, 50.5).y())
    dlg._set_tool("move")
    assert c._hit_line(pt) is not None
    dlg._set_tool(R.VLINE)
    assert c._hit_line(pt) is None
    dlg._set_tool(R.HLINE)
    assert c._hit_line(pt) is not None
    dlg._set_tool(R.RECT)
    assert c._hit_line(pt) is None
    # 도형 도구로 선 가까이에서 그려도 선이 끌려가지 않는다
    _drag(c, QPoint(int(pt.x()), int(pt.y()) + 2), QPoint(int(pt.x()) + 60, int(pt.y()) + 40))
    assert c.rois.get(c.rois.rois[0].id).pos == 50 and len(c.rois.rois) == 2


def test_hidden_line_reappears_when_user_draws_a_new_one(dlg):
    c = dlg.canvas
    dlg.show_h.setChecked(False)
    dlg._set_tool(R.HLINE)
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, _pt(c, 50, 50))
    assert dlg.show_h.isChecked() and c.rois.show_h


def test_hidden_lines_do_not_enter_roi_only_overlay(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 40)
    dlg.show_h.setChecked(False)
    rois = dlg._visible_rois()
    assert rois == []


# ------------------------------------------------------------------ 4. 크기 조절
def test_plus_minus_resize_circle_by_one_pixel_radius(dlg):
    c = dlg.canvas
    r = _add_rect(dlg, x=80, y=50, w=20, h=20, kind=R.CIRCLE)
    cx, cy = r.center
    QTest.keyClick(c, Qt.Key.Key_Plus)
    g = c.rois.active()
    assert g.w == g.h == 22 and g.center == (cx, cy)
    QTest.keyClick(c, Qt.Key.Key_Minus)
    QTest.keyClick(c, Qt.Key.Key_Minus)
    assert c.rois.active().w == 18
    QTest.keyClick(c, Qt.Key.Key_Plus, Qt.KeyboardModifier.ShiftModifier)
    assert c.rois.active().w == 38                          # Shift 는 10배


def test_size_buttons_do_the_same_and_keep_focus_on_canvas(dlg):
    c = dlg.canvas
    _add_rect(dlg, x=80, y=50, w=20, h=20, kind=R.SQUARE)
    dlg.size_plus.click()
    assert c.rois.active().w == 22
    dlg.size_minus.click()
    dlg.size_minus.click()
    assert c.rois.active().w == 18
    assert dlg.size_plus.focusPolicy() == Qt.FocusPolicy.NoFocus   # 방향키가 사진 화면에 남는다


def test_rect_resize_and_minimum_and_lines_ignore_it(dlg):
    c = dlg.canvas
    _add_rect(dlg, x=50, y=40, w=10, h=6)
    QTest.keyClick(c, Qt.Key.Key_Plus)
    assert (c.rois.active().w, c.rois.active().h) == (12, 8)
    for _ in range(20):
        QTest.keyClick(c, Qt.Key.Key_Minus)
    assert (c.rois.active().w, c.rois.active().h) == (R.MIN_SIZE, R.MIN_SIZE)
    c.rois.add_line(R.HLINE, 20)
    assert c.grow(2) is False


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
def test_table_lists_whole_image_and_each_roi_and_click_selects(dlg):
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 30)
    r2 = _add_rect(dlg)
    dlg._refresh()
    assert dlg.table.rowCount() == 3
    assert dlg.table.item(0, 0).text() == "사진 전체"
    dlg._on_table_click(1, 0)
    assert c.rois.selected == c.rois.rois[0].id
    assert r2.id != c.rois.selected


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


def test_8bit_image_has_no_bit_choice_16bit_has(styled_qapp):
    d8 = _make(styled_qapp, bits=8)
    assert not d8.bits_row.isVisible() and d8._levels() == 256     # 8 bit 는 고를 것이 없다 — 행이 없다
    d16 = _make(styled_qapp, bits=16)
    assert d16.bits_row.isVisible()
    i12 = [i for i in range(d16.bits_combo.count()) if d16.bits_combo.itemData(i) == 12][0]
    d16.bits_combo.setCurrentIndex(i12)
    assert d16._levels() == 4096 and len(d16._full_hist) == 4096 and d16._hi <= 4095
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


def test_open_new_image_clears_rois_but_keeps_view_toggles(dlg):
    dlg.canvas.rois.add_line(R.HLINE, 10)
    dlg.show_h.setChecked(False)
    dlg.show_view(loader.ViewLoad(loader.GlImage(
        np.zeros((20, 30), np.uint8), 8, False, "y.png", [])))
    assert dlg.canvas.rois.rois == [] and dlg.canvas.rois.show_h is False
    assert dlg.canvas.image_size() == (30, 20)


# ------------------------------------------------------------------ 저장
def test_save_xlsx_and_png(dlg, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 30)
    _add_rect(dlg, x=10, y=10, w=20, h=10)
    dlg.save_xlsx(tmp_path / "out.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "out.xlsx")
    assert set(wb.sheetnames) == {"분석 조건", "ROI 통계", "히스토그램", "선 프로파일"}
    stats = wb["ROI 통계"]
    assert stats.max_row == 4 and stats["A2"].value == "사진 전체"
    assert stats["C2"].value == W * H
    prof = wb["선 프로파일"]
    assert prof.max_row == W + 1 and prof["B1"].value == "수평선 1"
    assert prof["B2"].value == int(dlg._gl.data[30, 0])
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
    assert c._target == (400, 130, 40, 40)
    fit = min(c.width() / 800, c.height() / 300)
    assert c.scale() > fit * 3                               # 결함 영역으로 확대됐다
    cx, cy = c.to_screen(420, 150).x(), c.to_screen(420, 150).y()
    assert abs(cx - c.width() / 2) < 3 and abs(cy - c.height() / 2) < 3    # 결함이 한가운데
    assert d.banner.isVisible() and "s.t.jpg" in d.banner.text()
    c.fit()                                                  # F — Scan 전체 보기
    assert c.scale() == pytest.approx(fit)
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
    d = GlViewerPanel(None)
    d.resize(1280, 820)
    d.show()
    d.open_path(cp)
    for _ in range(200):
        _pump(1)
        if d._gl is not None:
            break
        QTest.qWait(20)
    assert d._gl is not None and d._gl.shape == (120, 300)
    assert d.canvas._target == (130, 40, 40, 40)
    d.close()


def test_open_failure_shows_message_not_crash(styled_qapp, tmp_path):
    d = GlViewerPanel(None)
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


def test_number_keys_switch_tools(dlg):
    c = dlg.canvas
    QTest.keyClick(c, Qt.Key.Key_2)
    assert c.tool == R.HLINE and dlg._tool_btns[R.HLINE].isChecked()
    QTest.keyClick(c, Qt.Key.Key_6)
    assert c.tool == R.CIRCLE and not dlg._tool_btns[R.HLINE].isChecked()


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
    page = sp.SetupPage()
    try:
        assert page.gl_btn.text() == "GL 히스토그램"
        assert page._tool_rail.isAncestorOf(page.gl_btn)
        page.gl_btn.click()
        assert len(seen) == 1 and isinstance(seen[0][0], gv.GlViewerDialog)
        assert seen[0][1] == {"full_bleed": True}
        seen[0][0].deleteLater()
    finally:
        page.deleteLater()


def test_image_info_has_gl_button_enabled_only_with_photo(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import image_info_dialog as ii
    from aoi_verification.app.ui.widgets import gl_viewer_dialog as gv
    monkeypatch.setattr(ii, "_async_info", lambda: False)
    d = ii.ImageInfoDialog(None)
    assert not d.gl_btn.isEnabled()
    assert d.gl_btn.autoDefault() is False                  # Enter 가 GL 뷰어를 재실행하지 않는다
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG")
    d.show_image(p)
    assert d.gl_btn.isEnabled()
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


def test_compare_table_rows_are_a_b_and_delta(dlg):
    c = dlg.canvas
    b = np.clip(dlg._gl.data.astype(np.int64) + 1000, 0, 65535).astype(np.uint16)
    dlg.show_compare(_b_view(b))
    r = _add_rect(dlg, x=10, y=10, w=20, h=10)
    dlg._refresh()
    names = [dlg.table.item(i, 0).text() for i in range(dlg.table.rowCount())]
    assert names == ["사진 전체 · A", "사진 전체 · B", "사진 전체 · B−A",
                     f"{r.name} · A", f"{r.name} · B", f"{r.name} · B−A"]
    sub_a, sub_b = dlg._gl.data[10:20, 10:30], b[10:20, 10:30]
    delta = dlg.table.item(5, 3).text()
    assert delta == f"{float(sub_b.mean()) - float(sub_a.mean()):+,.1f}"
    dlg._on_table_click(5, 0)                   # 차이 줄을 눌러도 그 ROI 가 선택된다
    assert c.rois.selected == r.id
    assert [s[0] for s in dlg.hist._series] == ["A", "B"]


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
    c = dlg.canvas
    c.rois.add_line(R.HLINE, 30)
    dlg.show_compare(_b_view((dlg._gl.data // 2).astype(np.uint16)))
    dlg.save_xlsx(tmp_path / "c.xlsx")
    wb = openpyxl.load_workbook(tmp_path / "c.xlsx")
    heads = [c_.value for c_ in wb["히스토그램"][1]]
    assert heads == ["GL", "사진 전체 A", "사진 전체 B", "수평선 1 A", "수평선 1 B"]
    assert [c_.value for c_ in wb["선 프로파일"][1]] == ["위치", "수평선 1 A", "수평선 1 B"]
    assert wb["ROI 통계"].max_row == 1 + 6
    dlg.save_png(tmp_path / "c.png")
    assert (tmp_path / "c.png").exists() and (tmp_path / "c_B.png").exists()


def test_compare_hover_works_from_either_canvas(dlg):
    dlg.show_compare(_b_view(dlg._gl.data.copy()))
    _pump()                       # 분할 창이 두 칸으로 나뉘며 A 가 다시 맞춰지고 B 가 따라온다
    assert dlg.canvas_b.view_state() == pytest.approx(dlg.canvas.view_state())
    QTest.mouseMove(dlg.canvas_b, _pt(dlg.canvas_b, 20, 30))
    _pump()
    assert "X 20" in dlg.status.text()


# ------------------------------------------------------------------ 2026-10-30 화면 개편
# "버튼이 많고 생김새가 이상하고 공간 효율이 나쁘다" → 도구는 왼쪽 아이콘 레일, 저장은 메뉴 하나,
# 크기 ± · 선 프로파일 · 통계 표는 **필요할 때만** 자리를 차지한다.
def test_rail_tiles_are_square_after_qss_polish(dlg):
    # ★ Qt 는 polish 때 QSS min-height 로 setMinimumSize 를 덮어쓴다 — 코드의 setFixedSize 가
    #   34px 로 눌려 아이콘 아래 이름이 잘렸던 회귀.  크기는 QSS(role=glTile)가 정한다.
    for b in list(dlg._tool_btns.values()) + [dlg.fit_btn, dlg.one_btn, dlg.undo_btn, dlg.clear_btn]:
        assert (b.width(), b.height()) == (56, 56), b.caption()
        assert b.property("role") == "glTile" and b.toolTip()
    assert [b.caption() for b in dlg._tool_btns.values()] == ["이동", "수평선", "수직선", "사각형", "정사각형", "원"]


def test_size_stepper_only_for_selected_shape(dlg):
    c = dlg.canvas
    assert not dlg.size_plus.isVisible() and not dlg.sel_label.isVisible()
    _add_rect(dlg, x=10, y=10, w=20, h=20)
    c.roisChanged.emit()
    _pump()
    assert dlg.size_plus.isVisible() and dlg.size_minus.isVisible()
    assert "사각형 1" in dlg.sel_label.text()
    c.rois.add_line(R.HLINE, 5)                 # 선은 크기가 없다 → 숨긴다
    c.roisChanged.emit()
    _pump()
    assert not dlg.size_plus.isVisible()
    c.rois.selected = None
    c.selectionChanged.emit()
    _pump()
    assert not dlg.size_plus.isVisible()


def test_selecting_a_shape_does_not_move_the_picture(dlg):
    """± 가 나타나며 상태 줄이 커지면 사진 화면이 줄어 맞춤 배율이 바뀌고 그리던 도형이 밀렸다."""
    c = dlg.canvas
    before = (c.geometry(), c.view_state())
    _add_rect(dlg, x=10, y=10, w=20, h=20)
    c.roisChanged.emit()
    _pump()
    assert (c.geometry(), c.view_state()) == before


def test_profile_section_only_with_a_line(dlg):
    assert not dlg.profile.isVisible() and not dlg.profile_head.isVisible()
    dlg.canvas.rois.add_line(R.VLINE, 7)
    dlg.canvas.roisChanged.emit()
    _pump()
    assert dlg.profile.isVisible() and dlg.profile_head.isVisible()
    dlg.show_v.setChecked(False)                # 숨긴 선은 프로파일에서도 빠진다 → 칸도 접힌다
    _pump()
    assert not dlg.profile.isVisible()


def test_table_hidden_until_an_image_is_open(styled_qapp):
    d = GlViewerPanel(None)
    d.resize(1280, 820)
    d.show()
    _pump()
    try:
        assert not d.table.isVisible() and not d.table_head.isVisible()
        d.show_view(loader.ViewLoad(loader.GlImage(np.zeros((20, 30), np.uint8), 8, False, "y.png", [])))
        _pump()
        assert d.table.isVisible() and d.table.rowCount() == 1
    finally:
        d.close()
        d.deleteLater()


def test_save_menu_holds_xlsx_png_and_roi_toggle(dlg):
    acts = dlg.save_menu.actions()
    texts = [a.text() for a in acts if not a.isSeparator()]
    assert texts == ["엑셀로 저장…", "색 칠한 PNG 저장…", "PNG 에 ROI 선·이름도 그리기"]
    assert dlg.png_rois.isCheckable() and dlg.png_rois.isChecked()


def test_dialog_wraps_panel_and_opens_given_path(styled_qapp, monkeypatch):
    opened = []
    monkeypatch.setattr(GlViewerPanel, "open_path", lambda self, path, **kw: opened.append(str(path)))
    d = GlViewerDialog(None, image_path="/x/y.png")
    try:
        assert isinstance(d.panel, GlViewerPanel) and opened == ["/x/y.png"]
        assert d.windowTitle() == "GL 히스토그램"
    finally:
        d.deleteLater()


def test_threshold_note_hidden_when_empty(dlg):
    assert not dlg.thr_note.isVisible()
    dlg._set_thresholds(dlg._hi + 5, dlg._hi, "lo")        # 하한이 상한을 넘으면 바로 아래서 멈추고 알린다
    _pump()
    assert dlg.thr_note.isVisible() and dlg._lo == dlg._hi - 1
    dlg._set_thresholds(0, dlg._hi, "lo")
    _pump()
    assert not dlg.thr_note.isVisible()
