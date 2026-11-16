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

W, H = 200, 150


def _pump(n: int = 3) -> None:
    for _ in range(n):
        QApplication.processEvents()


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
    labels = [b.text() for b in dlg._tool_btns.values()]
    assert labels == ["이동", "사각형", "원"]                 # 단축키 숫자를 붙이지 않는다
    assert all(not b.toolTip() for b in dlg._tool_btns.values())


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
    assert {"맞춤", "되돌리기", "모두 지우기", "ROI끼리 겹쳐 보기", "데이터 범위만"} <= texts
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


def test_8bit_image_has_no_bit_choice_16bit_has(styled_qapp):
    d8 = _make(styled_qapp, bits=8)
    assert not d8.bits_combo.isEnabled() and d8._levels() == 256
    d16 = _make(styled_qapp, bits=16)
    assert d16.bits_combo.isEnabled()
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
    assert [(r.x, r.y, r.w, r.h) for r in c.rois.rois] == [(400, 130, 40, 40)]
    fit = min(c.width() / 800, c.height() / 300)
    assert c.scale() > fit * 3                               # 결함 영역으로 확대됐다
    cx, cy = c.to_screen(420, 150).x(), c.to_screen(420, 150).y()
    assert abs(cx - c.width() / 2) < 3 and abs(cy - c.height() / 2) < 3    # 결함이 한가운데
    assert d.banner.isVisible() and "s.t.jpg" in d.banner.text()
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
    assert (r.x, r.y, r.w, r.h) == (100, 50, 100, 100)    # 이미지 안으로 가둔 상자
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
    assert r.name == "Defect" and (r.x, r.y, r.w, r.h) == (20, 20, 40, 40)
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
    assert [(r.x, r.y, r.w, r.h) for r in d.canvas.rois.rois] == [(130, 40, 40, 40)]
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
    assert "X 20" in dlg.status.text()
