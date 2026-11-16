"""Threshold 미리보기 화면 — Zone 으로 고르고, 기준을 올리면 빠지는 수가 코어와 같은지."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from aoi_verification.app.threshold import preview as P  # noqa: E402
from aoi_verification.app.threshold import rules as R  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FS_UBM = ROOT / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"
PX = 1.6903343                     # 그 폴더 Params_WaferInfo.ini RefPixelSizeX


def _wafer_with_photos(tmp_path: Path) -> Path:
    """실물 폴더 사본 + 사진목록.txt 이름의 빈 사진(사진은 저장소에 두지 않는다)."""
    w = tmp_path / "27125535EWA5"
    shutil.copytree(FS_UBM, w)
    for name in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines():
        if name and not name.startswith("#"):
            (w / name).write_bytes(b"")
    return w


def test_load_wafer_reads_pixels_and_pairs_photos(tmp_path):
    """Defect 14 = 장비 Export, 사진 짝 14/14(사진 정보 화면과 같은 5 µm 규칙), 레시피 값 검산 0."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = T.load_wafer(_wafer_with_photos(tmp_path))
    assert w.note == "" and len(w.defects) == 14 and w.goods == ()
    assert w.px == (PX, PX) and w.misses == 0
    assert sorted(w.photos) == [(0, d.index) for d in w.defects]
    assert abs(w.to_um(R.FEATURE_AREA, 10.4996793) - 30.0) < 0.01          # Zone ini px² → RTP.txt µm²


def test_no_pixel_size_stops_with_reason(tmp_path):
    """픽셀 크기를 못 읽으면 추측값으로 보이지 않고 이유를 띄운다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = _wafer_with_photos(tmp_path)
    (w / "Params_WaferInfo.ini").unlink()
    got = T.load_wafer(w)
    assert got.note and got.px == (0.0, 0.0) and got.photos == {}


def test_zone_pick_and_raise_matches_core(styled_qapp, tmp_path):
    """Zone 목록에서 고르고 Area 를 40 µm² 로 올리면 7개가 빠진다 — 코어 count_by_bin 과 같다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(_wafer_with_photos(tmp_path)))
        assert dlg.zone_keys() == [("Zones", 1), ("Zones", 63)]          # Defect 많은 순, 기준 없는 255 는 없음
        assert dlg._zone == ("Zones", 1)
        assert dlg.drop_value.text() == "0" and not dlg.reset_btn.isEnabled()
        assert not dlg.new_box.isVisibleTo(dlg)                          # RichSurface 없음 — 내리기 없음
        assert dlg.sliders[(14, R.FEATURE_AREA)][0].value() == 0          # 슬라이더 시작 = 레시피 = 맨 왼쪽
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        core = P.count_by_bin(dlg._w.defects, dlg._w.zones, {("Zones", 1, 14, R.FEATURE_AREA): 40.0 / PX ** 2})
        total, kept = core[("Zones", 1, 14)]
        assert dlg.drop_value.text() == str(total - kept) == "7"
        assert len(dlg.tiles) == 7 and dlg.reset_btn.isEnabled()
        assert "−7" in dlg.zone_btns[("Zones", 1)].text()
        dlg._toggle(next(iter(dlg.tiles)))                              # 사진 누르면 그 줄 아래 값
        assert dlg._open is not None
        dlg._set_zone(("Zones", 63))                                    # Scan Area: Feature Filter 없음
        assert dlg.zone_btns[("Zones", 63)].isChecked() and dlg.sliders == {}
        dlg.reset()
        dlg._set_zone(("Zones", 1))
        assert dlg.drop_value.text() == "0"
    finally:
        dlg.deleteLater()


_RDL = """[General]
ZoneName=RDL
ZoneID=2
[Surface Feature Filter Width]
Classify=29
FeatureType=2
FilterType=5
LowValue=2.0
[Surface Feature Filter Contrast Average]
Classify=29
FeatureType=12
FilterType=5
LowValue=24.0
[Surface Feature Filter Area_5]
Classify=28
FeatureType=1
FilterType=5
LowValue=10.0
"""


def _d(i, b, width, area, contrast):
    return P.Defect(i, "Zones", 2, b, b, area, width, 9.0, contrast, 0.0, 0.0)


def test_two_bundles_show_two_rules_and_hide_unmeasured_contrast(styled_qapp, tmp_path):
    """한 Zone 안 기준 묶음 둘 = '기준 1 · 기준 2'.  Contrast 가 전부 0(안 잼)이면 Contrast 슬라이더는 없다.
    EDC Good 은 세지 않고 탭으로만 보인다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    (tmp_path / "Zones").mkdir()
    (tmp_path / "Zones" / "RDL.ini").write_text(_RDL)
    zones = R.read_zones(tmp_path)
    defects = (_d(0, 29, 3.0, 50.0, 0.0), _d(1, 28, 1.0, 20.0, 0.0))
    goods = (_d(2, 20, 3.0, 50.0, 0.0),)
    w = T.WaferData(tmp_path, defects, goods, zones,
                    {29: R.ClassInfo("B R Surface Dark", False), 28: R.ClassInfo("B R Other", False),
                     20: R.ClassInfo("EDC Good", True)}, (1.0, 1.0))
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(w)
        assert sorted(dlg.sliders) == [(28, R.FEATURE_AREA), (29, R.FEATURE_WIDTH)]
        assert dlg.tab_btns["good"].isVisibleTo(dlg) and "1" in dlg.tab_btns["good"].text()
        assert dlg.all_label.text().endswith("2")                         # EDC Good 은 안 센다
        dlg.set_value(29, R.FEATURE_WIDTH, 3.5)       # 0번은 기준 1 에서 떨어지지만 기준 2(Area 10)를 넘는다
        assert dlg.drop_value.text() == "0"
        dlg.set_value(28, R.FEATURE_AREA, 45.0)       # 0번(50)은 기준 2 를 아직 넘고 1번(20)만 빠진다
        assert dlg.drop_value.text() == "1"
    finally:
        dlg.deleteLater()


def test_match_photos_nearest_within_tolerance():
    d = (P.Defect(0, "Zones", 1, 29, 29, 1, 1, 1, 0, 100.0, 100.0),
         P.Defect(1, "Zones", 1, 29, 29, 1, 1, 1, 0, 103.0, 100.0))
    xy = {"a": (102.5, 100.0), "b": (200.0, 0.0), "c": None}
    assert P.match_photos(d, ["a", "b", "c"], xy.get, 5.0) == {1: "a"}


def test_export_html_lists_change_and_dropped(styled_qapp, tmp_path):
    """Area 40 µm² 로 올리고 내보내면 기존 → 변경 값 · 빠진 7 · 대표 사진이 HTML 에 들어간다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(_wafer_with_photos(tmp_path)))
        assert not dlg.export_btn.isEnabled()
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert dlg.export_btn.isEnabled()
        out = tmp_path / "보고.html"
        dlg.export_html(out)
        text = out.read_text(encoding="utf-8")
        assert "30.0" in text and "→ 40.0" in text                      # 레시피 30 µm² → 40
        assert "−7" in text and text.count("class='tile'") == 7          # 7개 빠짐, 사진 7장(8장 이하라 전부)
    finally:
        dlg.deleteLater()


def test_lowering_shows_new_count_from_rich_surface(styled_qapp, tmp_path):
    """RichSurface 가 있는 Zone 은 레시피 아래로 내릴 수 있고 '새로 생김' 이 코어 count_new 와 같다."""
    from aoi_verification.app.threshold import rich as RS
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    pi = ROOT / "dev" / "Threshold 확인" / "TB500 PI_Opening" / "20234838EWG1"
    zones = R.read_zones(pi)
    defects = (P.Defect(0, "Zones", 1, 29, 29, 30.0, 3.0, 4.0, 120.0, 0.0, 0.0),)
    w = T.WaferData(pi, defects, (), zones, {29: R.ClassInfo("B R Surface Dark", False)}, (1.0, 1.0),
                    rich=RS.load_rich(pi))
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(w)
        assert dlg.new_box.isVisibleTo(dlg) and dlg.new_value.text() == "0"
        assert dlg.sliders[(29, R.FEATURE_CONTRAST)][0].value() > 0          # 레시피 아래로 내릴 칸이 있다
        dlg.set_value(29, R.FEATURE_CONTRAST, 70.0)
        dlg.set_value(29, R.FEATURE_WIDTH, 2.0)
        dlg.set_value(29, R.FEATURE_AREA, 15.0)
        assert dlg.new_value.text() == "5" and dlg.drop_value.text() == "0"
        assert "+5" in dlg.zone_btns[("Zones", 1)].text()
        out = tmp_path / "내림.html"
        dlg.export_html(out)
        text = out.read_text(encoding="utf-8")
        assert "+5" in text and "100.0" in text and "→ 70.0" in text
        dlg.reset()
        assert dlg.new_value.text() == "0"
    finally:
        dlg.deleteLater()


def test_auto_mode_picks_keep_and_recommend_matches_core(styled_qapp, tmp_path):
    """자동 추천: 사진을 누르면 꼭 잡기 — 추천값은 코어 recommend 와 같고, 고른 것은 전부 남는다.
    고른 것을 빼면 그 Zone 은 레시피 값으로 돌아간다.  직접 조정에서 고른 것을 떨어뜨리면 '놓침' 이 뜬다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(_wafer_with_photos(tmp_path)))
        dlg.set_mode("auto")
        assert dlg._tab == "keep" and len(dlg.tiles) == 10                 # 처음엔 Zone 1 전부 남음
        by_area = sorted(dlg._in_zone(), key=lambda d: d.area)
        a, b = by_area[4], by_area[7]
        dlg.toggle_pick(T.key_of(a))
        dlg.toggle_pick(T.key_of(b))
        rec, missed = P.recommend([a, b], dlg._w.zones, dlg._snap)
        assert missed == [] and dlg._ov == rec
        low = dlg._ov[("Zones", 1, 14, R.FEATURE_AREA)]
        assert low <= a.area and dlg._w.to_um(R.FEATURE_AREA, low) == pytest.approx(
            int(dlg._w.to_um(R.FEATURE_AREA, a.area) * 10) / 10)            # 화면 자리(0.1 µm²)로 내림
        core = P.count_by_bin(dlg._w.defects, dlg._w.zones, dlg._ov)[("Zones", 1, 14)]
        assert dlg.drop_value.text() == str(core[0] - core[1]) == "7"   # Area · Width · Contrast 를 함께 올려 7개
        assert dlg.tiles[T.key_of(a)].picked and "✓2" in dlg.zone_btns[("Zones", 1)].text()
        assert "2" in dlg.pick_label.text() and dlg.miss_label.text() == ""
        dlg.set_mode("manual")                                            # 추천값이 그대로 남는다
        assert dlg._ov == rec
        dlg.set_value(14, R.FEATURE_AREA, 9999.0)
        assert dlg.missed() and dlg.miss_label.text()
        dlg.set_mode("auto")
        dlg.toggle_pick(T.key_of(a))
        dlg.toggle_pick(T.key_of(b))
        assert dlg._ov == {} and dlg.drop_value.text() == "0"
    finally:
        dlg.deleteLater()


def test_lot_folder_merges_wafers_and_skips_other_recipe(tmp_path):
    """LOT 폴더 = 아래 웨이퍼 전부.  기준이 다른 웨이퍼는 빼고, 합친 Defect 는 (웨이퍼, index) 로 겹치지 않는다.
    LOT 과 그 안의 웨이퍼를 같이 골라도 한 번만 읽는다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    lot = tmp_path / "LOT"
    lot.mkdir()
    w1 = _wafer_with_photos(lot)
    w2 = lot / "27125535EWA6"
    shutil.copytree(w1, w2)
    w3 = lot / "27125535EWA7"
    shutil.copytree(w1, w3)
    ini = next((w3 / "Zones").glob("amk*.ini"))
    ini.write_text(ini.read_text(encoding="utf-8", errors="replace").replace("LowValue=", "LowValue=1"),
                   encoding="utf-8")
    (lot / "메모").mkdir()                                                # Surface.flt 없는 폴더는 무시
    folders = T.wafer_folders([lot, w2])
    assert folders == [w1, w2, w3]
    m = T.merge_wafers([T.load_wafer(f) for f in folders])
    assert m.folders == (w1, w2) and m.skipped == (w3,)
    assert len(m.defects) == 28 and len({T.key_of(d) for d in m.defects}) == 28
    assert sorted(m.photos) == sorted(T.key_of(d) for d in m.defects)
    assert m.rich == {}


def test_adding_a_wafer_keeps_picks_and_values(styled_qapp, tmp_path):
    """폴더를 더해도 앞 웨이퍼에서 고른 꼭 잡기와 추천값은 그대로다(같은 Zone 기준).  타일은 가로로 늘어나지 않는다."""
    from PyQt6.QtWidgets import QSizePolicy
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w1 = _wafer_with_photos(tmp_path)
    w2 = tmp_path / "27125535EWA6"
    shutil.copytree(w1, w2)
    one = T.merge_wafers([T.load_wafer(w1)])
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(one)
        dlg.set_mode("auto")
        d = max(dlg._in_zone(), key=lambda x: x.area)
        dlg.toggle_pick(T.key_of(d))
        ov = dict(dlg._ov)
        assert ov
        dlg.set_wafer(T.merge_wafers([T.load_wafer(w1), T.load_wafer(w2)]))
        assert list(dlg._picked) == [(0, d.index)] and dlg._ov == ov
        assert dlg.tiles[(0, d.index)].sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
    finally:
        dlg.deleteLater()


def test_no_color_photo_asks_then_shows_scan_crop(styled_qapp, tmp_path):
    """Color 사진만 보여 주다가, Color 사진이 없는 Defect 가 있으면 '스캔 사진으로 볼까요?' 를 묻는다.
    '예' 면 같은 폴더 Scan(.t.) 사진에서 그 Defect 자리를 잘라 타일로 보인다.  '아니요' 면 다시 묻지 않는다."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from PyQt6.QtGui import QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = tmp_path / "27125535EWA5"
    shutil.copytree(FS_UBM, w)
    names = [n for n in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines()
             if n and not n.startswith("#")]
    for name in names[:4]:                                  # Color 사진은 4장만 — 나머지 Defect 는 사진 없음
        (w / name).write_bytes(b"")
    defects, _note = P.load_defects(w)
    have = T.load_wafer(w).photos
    lonely = [d for d in defects if (0, d.index) not in have]
    target = lonely[0]
    cv2.imwrite(str(w / "1.2.t.1.jpeg"), np.full((1000, 2000, 3), 90, np.uint8))
    (w / "ScanResultImageList.txt").write_text(f"Version=1\n1.2.t.1.jpeg,{target.x},{target.y}\n")

    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        assert dlg.scan_ask.isVisibleTo(dlg)
        assert f"{len(defects) - len(have)}" in dlg.scan_ask_label.text()
        assert all(isinstance(t, T._Tile) for t in dlg.tiles.values())
        assert not any(k == T.key_of(target) for k in dlg.tiles)      # 기본은 Color 사진만
        got = T.scan_crops(dlg._scan_items())
        assert T.key_of(target) in got and isinstance(got[T.key_of(target)], QImage)
        side = round(300.0 / PX)                                       # 300 µm Crop
        assert abs(got[T.key_of(target)].width() - side) <= 1
        dlg._use_scan = True
        dlg.scan_ask.setVisible(False)
        dlg.set_scans(got)
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        assert T.key_of(target) in dlg.tiles

        dlg2 = T.ThresholdPreviewDialog()
        dlg2.set_wafer(T.load_wafer(w))
        dlg2.answer_scan(False)
        assert not dlg2.scan_ask.isVisibleTo(dlg2)
        dlg2.set_wafer(T.load_wafer(w))                                # 폴더를 다시 읽어도 다시 묻지 않는다
        assert not dlg2.scan_ask.isVisibleTo(dlg2)
        dlg2.deleteLater()
    finally:
        dlg.deleteLater()


def test_group_by_source_keeps_display_order():
    """같은 Scan 원본은 한 묶음 — 묶음 순서는 처음 나온 자리, 묶음 안은 들어온 순서."""
    pytest.importorskip("PyQt6.QtWidgets")
    from types import SimpleNamespace as NS
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    m = lambda p: NS(best=NS(path=Path(p)))             # noqa: E731
    g = T.group_by_source([("a", m("x.jpg")), ("b", m("y.jpg")), ("c", m("x.jpg"))])
    assert list(g) == ["x.jpg", "y.jpg"]
    assert [k for k, _m in g["x.jpg"]] == ["a", "c"]


def test_scan_crops_batches_and_decodes_each_source_once(tmp_path, monkeypatch):
    """원본 하나에서 여러 Defect 를 자를 때 디코드는 한 번, Crop 은 배치로 흘러나온다."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from aoi_verification.app.coords import scan_image as SI
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = tmp_path / "W1"
    w.mkdir()
    cv2.imwrite(str(w / "1.t.1.jpeg"), np.full((1000, 2000, 3), 90, np.uint8))
    (w / "ScanResultImageList.txt").write_text("Version=1\n1.t.1.jpeg,0,0\n")
    monkeypatch.setattr(SI.pixel_size, "scan_pixel_size_xy", lambda f: (PX, PX))
    SI.clear_caches()
    n_open = []
    from PIL import Image
    orig = Image.open
    monkeypatch.setattr(Image, "open", lambda p, *a, **k: (n_open.append(str(p)), orig(p, *a, **k))[1])
    items = [(("k", i), w, (i * 20.0, i * 10.0)) for i in range(40)]
    SI.resolve_xy(w, (0.0, 0.0))                          # 목록 인덱스(머리 읽기)를 미리 — 아래 센 것은 본 디코드만
    n_open.clear()
    batches = []
    got = T.scan_crops(items, on_batch=batches.append)
    assert len(got) == 40 and sum(len(b) for b in batches) == 40 and len(batches) >= 2
    assert sum(1 for p in n_open if p.endswith("1.t.1.jpeg")) == 1       # 디코드 한 번


def test_answer_scan_shows_placeholders_then_fills_async(styled_qapp, tmp_path):
    """'예' 직후 화면을 막지 않고 자리표시 타일이 먼저 서고, Crop 이 도착하면 그림이 채워진다(스레드 포함)."""
    import time
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from PyQt6.QtGui import QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = tmp_path / "27125535EWA5"
    shutil.copytree(FS_UBM, w)
    names = [n for n in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines()
             if n and not n.startswith("#")]
    for name in names[:4]:
        (w / name).write_bytes(b"")
    defects, _note = P.load_defects(w)
    have = T.load_wafer(w).photos
    target = [d for d in defects if (0, d.index) not in have][0]
    cv2.imwrite(str(w / "1.2.t.1.jpeg"), np.full((1000, 2000, 3), 90, np.uint8))
    (w / "ScanResultImageList.txt").write_text(f"Version=1\n1.2.t.1.jpeg,{target.x},{target.y}\n")
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        dlg.answer_scan(True)
        k = T.key_of(target)
        assert k in dlg.tiles                                  # 자리표시 타일이 바로 선다
        end = time.time() + 20
        while time.time() < end and dlg._scan_thread is not None:
            styled_qapp.processEvents()
            time.sleep(0.01)
        assert dlg._scan_thread is None and isinstance(dlg._scans.get(k), QImage)
        assert not dlg.scan_status.text()
    finally:
        dlg.deleteLater()
