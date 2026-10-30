"""GL 뷰어 계산부 — ROI 모델·분석·파일 읽기 (Qt 없이).

지키는 계약:

- 분석은 **원본 비트심도 그대로**다(16 bit 값을 8 bit 로 줄여 세지 않는다).
- 선은 전체 폭·높이이고 자기 축으로만 움직인다.  도형은 이미지 밖으로 나가지 않는다.
- 원은 픽셀 중심 기준 판정이다.  하한 < 상한을 늘 지킨다.
- 되돌리기는 50 단계, 선 보기/숨기기는 되돌리기 대상이 아니다.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from aoi_verification.app.gl import analysis, loader, roi  # noqa: E402
from aoi_verification.app.gl.roi import Roi, RoiSet         # noqa: E402


def _img(h=40, w=60, bits=16):
    rng = np.random.default_rng(7)
    return rng.integers(0, 1 << bits, size=(h, w)).astype(np.uint16)


# ---------------------------------------------------------------- ROI 모델
def test_hline_moves_only_vertically_and_clamps():
    r = Roi(1, roi.HLINE, "a", "#fff", pos=5)
    assert roi.move(r, 99, 3, 60, 40).pos == 8
    assert roi.move(r, 0, -99, 60, 40).pos == 0
    assert roi.move(r, 0, 99, 60, 40).pos == 39


def test_vline_moves_only_horizontally():
    r = Roi(1, roi.VLINE, "a", "#fff", pos=5)
    assert roi.move(r, 4, 99, 60, 40).pos == 9
    assert roi.move(r, 999, 0, 60, 40).pos == 59


def test_shape_move_stays_inside_image():
    r = Roi(1, roi.RECT, "a", "#fff", x=10, y=10, w=20, h=10)
    m = roi.move(r, 1000, 1000, 60, 40)
    assert (m.x, m.y) == (40, 30)
    m = roi.move(r, -1000, -1000, 60, 40)
    assert (m.x, m.y) == (0, 0)


def test_circle_resize_keeps_center_and_ratio():
    r = Roi(1, roi.CIRCLE, "a", "#fff", x=20, y=10, w=10, h=10)
    g = roi.resize(r, 2, 0, 60, 40)          # 반지름 +1 → 지름 +2
    assert (g.w, g.h) == (12, 12)
    assert g.center == r.center
    s = roi.resize(r, -2, 0, 60, 40)
    assert (s.w, s.h) == (8, 8)


def test_resize_has_minimum_and_image_limit():
    r = Roi(1, roi.CIRCLE, "a", "#fff", x=20, y=10, w=4, h=4)
    assert roi.resize(r, -100, 0, 60, 40).w == roi.MIN_SIZE
    big = roi.resize(r, 1000, 0, 60, 40)
    assert big.w <= 40 and big.w == big.h        # 이미지 높이(40)를 못 넘는다


def test_rect_resize_changes_both_axes_independently():
    r = Roi(1, roi.RECT, "a", "#fff", x=20, y=10, w=10, h=6)
    g = roi.resize(r, 4, -2, 60, 40)
    assert (g.w, g.h) == (14, 4)


def test_from_drag_all_quadrants_and_too_small():
    # 정사각형은 긴 변에 맞추고 드래그 방향을 따른다(이미지 밖은 안으로 민다).
    cases = {(30, 30): (10, 10, 20), (0, 0): (0, 0, 10),
             (30, 0): (10, 0, 20), (0, 30): (0, 10, 20)}
    for p1, (x, y, s) in cases.items():
        c = roi.from_drag(roi.SQUARE, (10, 10), p1, 60, 40)
        assert (c.x, c.y, c.w, c.h) == (x, y, s, s), p1
    assert roi.from_drag(roi.RECT, (10, 10), (11, 30), 60, 40) is None
    r = roi.from_drag(roi.RECT, (30, 20), (10, 5), 60, 40)
    assert (r.x, r.y, r.w, r.h) == (10, 5, 20, 15)


def test_roiset_undo_limit_and_view_toggles_not_undoable():
    s = RoiSet()
    for i in range(60):
        s.add_line(roi.HLINE, i % 40)
    n = 0
    while s.undo():
        n += 1
    assert n == roi.UNDO_LIMIT
    s2 = RoiSet()
    s2.add_line(roi.HLINE, 3)
    s2.show_h = False
    assert not s2.visible(s2.rois[0])
    assert s2.undo() and s2.rois == []          # 숨기기는 기록에 없다 — 추가만 되돌려진다
    assert s2.show_h is False


def test_roiset_delete_clear_names_and_colors():
    s = RoiSet()
    a = s.add_line(roi.HLINE, 1)
    b = s.add_line(roi.HLINE, 2)
    assert (a.name, b.name) == ("수평선 1", "수평선 2")
    assert a.color != b.color
    assert s.delete(a.id) and [r.id for r in s.rois] == [b.id]
    c = s.add_line(roi.HLINE, 3)
    assert c.name == "수평선 2" or c.name == "수평선 3"
    assert len({r.name for r in s.rois}) == len(s.rois)
    assert s.clear() and s.rois == [] and s.selected is None
    assert s.undo() and len(s.rois) == 2


# ------------------------------------------------------------------ 분석
def test_histogram_uses_full_bit_depth():
    img = np.array([[0, 1, 65535], [65535, 40000, 40000]], dtype=np.uint16)
    h = analysis.histogram(img, 16)
    assert len(h) == 65536 and h[65535] == 2 and h[40000] == 2 and h.sum() == 6


def test_histogram_out_of_range_values_go_to_last_bin():
    img = np.array([[10, 5000]], dtype=np.uint16)
    h = analysis.histogram(img, 12)              # 12 bit 범위(0~4095) 밖의 5000
    assert len(h) == 4096 and h[4095] == 1 and h.sum() == 2


def test_stats_matches_numpy_and_threshold_counts():
    img = _img()
    st = analysis.stats(img.reshape(-1), 1000, 60000)
    assert st["n"] == img.size
    assert st["mean"] == pytest.approx(float(img.mean()))
    assert st["std"] == pytest.approx(float(img.std()))
    assert st["below"] == int((img <= 1000).sum())
    assert st["above"] == int((img >= 60000).sum())
    e = analysis.stats(np.array([], dtype=np.uint16), 1, 2)
    assert e["n"] == 0 and e["mean"] is None


def test_line_values_are_full_row_or_column():
    img = _img()
    assert np.array_equal(analysis.roi_values(img, Roi(1, roi.HLINE, "", "", pos=7)), img[7, :])
    assert np.array_equal(analysis.roi_values(img, Roi(1, roi.VLINE, "", "", pos=9)), img[:, 9])
    assert analysis.roi_values(img, Roi(1, roi.HLINE, "", "", pos=999)).size == 0


def test_rect_values_and_circle_is_pixel_center_based():
    img = _img()
    r = Roi(1, roi.RECT, "", "", x=5, y=4, w=10, h=6)
    assert np.array_equal(analysis.roi_values(img, r), img[4:10, 5:15].reshape(-1))
    c = Roi(2, roi.CIRCLE, "", "", x=10, y=10, w=10, h=10)
    m = analysis.roi_mask(c, img.shape)
    assert m.sum() == analysis.roi_values(img, c).size
    assert m[15, 15] and not m[10, 10]            # 지름 10 원: 모서리 픽셀은 밖
    assert m[:, :10].sum() == 0 and m[:10].sum() == 0
    # 정사각형 모양 영역보다 작고, 내접 원 면적(약 78.5)에 가깝다.
    assert 70 <= m.sum() <= 90


def test_mask_clips_to_image_for_shapes_outside():
    img = _img()
    r = Roi(1, roi.RECT, "", "", x=55, y=35, w=20, h=20)
    assert analysis.roi_values(img, r).size == 5 * 5
    far = Roi(2, roi.RECT, "", "", x=500, y=500, w=5, h=5)
    assert analysis.roi_values(img, far).size == 0


def test_profile_only_for_lines():
    img = _img()
    assert analysis.profile(img, Roi(1, roi.RECT, "", "", x=0, y=0, w=5, h=5)) is None
    p = analysis.profile(img, Roi(1, roi.HLINE, "", "", pos=3))
    assert np.array_equal(p, img[3, :])


def test_auto_thresholds_and_clamp():
    img = _img(bits=12).astype(np.uint16)
    h = analysis.histogram(img, 12)
    lo, hi = analysis.auto_thresholds(h)
    assert 0 <= lo < hi <= 4095
    assert (img <= lo).mean() >= 0.004 and (img >= hi).mean() >= 0.004
    assert analysis.auto_thresholds(np.zeros(4096, dtype=np.int64)) == (0, 4095)
    assert analysis.clamp_thresholds(500, 100, 12) == (500, 501)
    assert analysis.clamp_thresholds(-5, 99999, 12) == (0, 4095)


def test_overlay_scope_all_roi_off_and_colors():
    img = np.full((10, 10), 500, dtype=np.uint16)
    img[0, 0] = 10
    img[9, 9] = 1000
    rng = (0, 1000)
    allv = analysis.overlay_rgb(img, rng, 100, 900, analysis.SCOPE_ALL, [])
    assert tuple(allv[0, 0]) == analysis.LOW_RGB and tuple(allv[9, 9]) == analysis.HIGH_RGB
    assert allv[5, 5, 0] == allv[5, 5, 1] == allv[5, 5, 2]
    off = analysis.overlay_rgb(img, rng, 100, 900, analysis.SCOPE_OFF, [])
    assert (off[..., 0] == off[..., 1]).all()
    r = Roi(1, roi.RECT, "", "", x=0, y=0, w=3, h=3)
    only = analysis.overlay_rgb(img, rng, 100, 900, analysis.SCOPE_ROI, [r])
    assert tuple(only[0, 0]) == analysis.LOW_RGB
    assert tuple(only[9, 9]) != analysis.HIGH_RGB          # ROI 밖은 칠하지 않는다


def test_display_is_view_only_analysis_untouched():
    img = _img()
    before = img.copy()
    analysis.to_display(img, analysis.display_range(img, 16, True))
    assert np.array_equal(img, before)
    assert analysis.display_range(img, 16, False) == (0, 65535)


# ------------------------------------------------------------------ 파일
def test_load_16bit_tiff_keeps_values(tmp_path):
    cv2 = pytest.importorskip("cv2")
    a = _img(30, 40)
    p = tmp_path / "가나다 사진.tif"            # 한글 경로
    assert cv2.imencode(".tif", a)[0]
    p.write_bytes(cv2.imencode(".tif", a)[1].tobytes())
    g = loader.load_image(p)
    assert g.bits == 16 and np.array_equal(g.data, a) and not g.color


def test_gray_saved_as_rgb_is_not_color_but_real_color_is(tmp_path):
    cv2 = pytest.importorskip("cv2")
    g3 = np.dstack([np.full((16, 16), 90, np.uint8)] * 3)
    (tmp_path / "g.png").write_bytes(cv2.imencode(".png", g3)[1].tobytes())
    assert not loader.load_image(tmp_path / "g.png").color
    c = g3.copy()
    c[..., 2] = 200
    (tmp_path / "c.png").write_bytes(cv2.imencode(".png", c)[1].tobytes())
    lc = loader.load_image(tmp_path / "c.png")
    assert lc.color and loader.W_COLOR in lc.warnings and lc.data.ndim == 2


def test_load_missing_or_garbage_raises_korean_error(tmp_path):
    with pytest.raises(loader.GlLoadError):
        loader.load_image(tmp_path / "없음.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(loader.GlLoadError):
        loader.load_image(bad)


def test_raw_little_big_endian_header_and_short_file(tmp_path):
    a = np.arange(12, dtype=np.uint16).reshape(3, 4) * 1000
    le = tmp_path / "le.raw"
    le.write_bytes(b"\x00" * 8 + a.astype("<u2").tobytes())
    assert np.array_equal(loader.load_raw(le, 4, 3, 16, True, 8).data, a)
    be = tmp_path / "be.raw"
    be.write_bytes(a.astype(">u2").tobytes())
    assert np.array_equal(loader.load_raw(be, 4, 3, 16, False, 0).data, a)
    with pytest.raises(loader.GlLoadError):
        loader.load_raw(be, 40, 30, 16)
    assert loader.guess_raw_side(64 * 64 * 2, 16) == 64
    assert loader.guess_raw_side(1000, 16) == 0


# ------------------------------------------------------- 손잡이 크기 조절
def test_resize_handle_fixed_opposite_corner_and_flip():
    r = Roi(1, roi.RECT, "", "", x=10, y=10, w=20, h=10)
    g = roi.resize_handle(r, "se", 50, 30, 100, 100)
    assert (g.x, g.y, g.w, g.h) == (10, 10, 40, 20)
    g = roi.resize_handle(r, "nw", 0, 0, 100, 100)
    assert (g.x, g.y, g.x + g.w, g.y + g.h) == (0, 0, 30, 20)
    g = roi.resize_handle(r, "e", 5, 0, 100, 100)        # 반대편을 넘기면 뒤집힌다
    assert (g.x, g.w) == (5, 5)
    g = roi.resize_handle(r, "n", 0, 5, 100, 100)        # 변 손잡이는 한 축만 바꾼다
    assert (g.x, g.w, g.y, g.h) == (10, 20, 5, 15)


def test_resize_handle_square_circle_keep_ratio():
    c = Roi(1, roi.CIRCLE, "", "", x=10, y=10, w=20, h=20)
    g = roi.resize_handle(c, "se", 50, 40, 100, 100)
    assert g.w == g.h == 40 and (g.x, g.y) == (10, 10)
    g = roi.resize_handle(c, "e", 50, 0, 100, 100)       # 변 손잡이: 다른 축은 가운데
    assert g.w == g.h == 40 and g.y == 0
    g = roi.resize_handle(c, "nw", 0, 5, 100, 100)
    assert g.w == g.h == 30 and (g.x, g.y) == (0, 0)


def test_resize_handle_never_leaves_image():
    r = Roi(1, roi.RECT, "", "", x=10, y=10, w=20, h=10)
    g = roi.resize_handle(r, "se", 9999, 9999, 100, 80)
    assert g.x + g.w <= 100 and g.y + g.h <= 80
    assert roi.resize_handle(Roi(1, roi.HLINE, "", "", pos=3), "e", 5, 5, 9, 9).pos == 3


# ----------------------------------------------- 컬러 → Scan 자동 확대
def test_color_photo_switches_to_scan_and_returns_defect_box(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    Image = pytest.importorskip("PIL.Image")
    from aoi_verification.app.coords import scan_image

    col = np.zeros((20, 20, 3), np.uint8)
    col[..., 2] = 200
    cpath = tmp_path / "defect.jpg"
    cpath.write_bytes(cv2.imencode(".jpg", col)[1].tobytes())
    scan = tmp_path / "scan.t.jpg"
    Image.fromarray(np.full((50, 80), 77, np.uint8)).save(scan)
    cand = scan_image.Candidate(scan, (0.0, 0.0), (80, 50), (30.0, 20.0), (20, 10, 20, 20), 1.0)
    match = scan_image.ScanMatch(scan_image.OK, cpath, (1.0, 2.0), (1.0, 1.0), (cand,))
    monkeypatch.setattr(scan_image, "resolve", lambda p: match)
    scan_image.clear_caches()
    v = loader.load_for_view(cpath)
    assert v.scan_name == "scan.t.jpg" and v.target_box == (20, 10, 20, 20)
    assert v.image.shape == (50, 80) and int(v.image.data[0, 0]) == 77 and not v.image.color
    assert not v.scan_missing and v.source_path == str(cpath)


def test_color_photo_without_scan_falls_back_to_luminance(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from aoi_verification.app.coords import scan_image
    col = np.zeros((20, 20, 3), np.uint8)
    col[..., 2] = 200
    cpath = tmp_path / "defect.png"
    cpath.write_bytes(cv2.imencode(".png", col)[1].tobytes())
    monkeypatch.setattr(scan_image, "resolve",
                        lambda p: scan_image.ScanMatch(scan_image.NO_MANIFEST, cpath))
    v = loader.load_for_view(cpath)
    assert v.scan_missing and v.target_box is None and v.image.color


def test_scan_failure_never_blocks_opening(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from aoi_verification.app.coords import scan_image
    col = np.zeros((20, 20, 3), np.uint8)
    col[..., 0] = 90
    cpath = tmp_path / "d.png"
    cpath.write_bytes(cv2.imencode(".png", col)[1].tobytes())

    def boom(p):
        raise RuntimeError("scan broke")
    monkeypatch.setattr(scan_image, "resolve", boom)
    assert loader.load_for_view(cpath).scan_missing


def test_gray_photo_is_not_sent_to_scan(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from aoi_verification.app.coords import scan_image
    called = []
    monkeypatch.setattr(scan_image, "resolve", lambda p: called.append(p))
    g = np.full((16, 16), 90, np.uint8)
    (tmp_path / "g.png").write_bytes(cv2.imencode(".png", g)[1].tobytes())
    v = loader.load_for_view(tmp_path / "g.png")
    assert not called and v.scan_name == "" and not v.scan_missing
