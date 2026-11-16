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


def _scan_wafer(tmp_path: Path, name: str = "27125535EWA5", color: int = 4):
    """실물 폴더 사본 — Color 사진은 ``color`` 장만 두고(나머지 Defect 는 사진 없음) Scan 목록 + 1000x2000 Scan 한 장."""
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    w = tmp_path / name
    shutil.copytree(FS_UBM, w)
    names = [n for n in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines()
             if n and not n.startswith("#")]
    for n in names[:color]:
        (w / n).write_bytes(b"")
    defects, _note = P.load_defects(w)
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    have = T.load_wafer(w).photos
    target = [d for d in defects if (0, d.index) not in have][0]
    cv2.imwrite(str(w / "1.2.t.1.jpeg"), np.full((1000, 2000, 3), 90, np.uint8))
    (w / "ScanResultImageList.txt").write_text(f"Version=1\n1.2.t.1.jpeg,{target.x},{target.y}\n")
    return w, target


def test_color_mode_falls_back_to_scan_for_defects_without_color(styled_qapp, tmp_path):
    """질문 띠는 없다.  칩이 [컬러] 여도 컬러 사진이 없는 Defect 는 같은 폴더 Scan(.t.) Crop 으로 채운다(자리표시 → 사진)."""
    from PyQt6.QtGui import QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w, target = _scan_wafer(tmp_path)
    dlg = T.ThresholdPreviewDialog()
    try:
        assert not hasattr(dlg, "scan_ask")
        dlg.set_wafer(T.load_wafer(w))
        k = T.key_of(target)
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        assert k in dlg.tiles and dlg._scan_pending(target)              # 대신 채울 자리표시 타일이 먼저 선다
        got = T.scan_crops(dlg._scan_items())
        skey = (w, target.index)
        assert skey in got and isinstance(got[skey], QImage)             # 키 = (웨이퍼 폴더, Defect.index)
        assert abs(got[skey].width() - round(300.0 / PX)) <= 1           # 300 µm Crop
        dlg.set_scans(got)
        assert isinstance(dlg._pic(target), QImage)
        colored = next(d for d in dlg._w.defects if d is not target and T.key_of(d) in dlg._w.photos)
        assert not isinstance(dlg._pic(colored), QImage)                 # 컬러 사진이 있으면 컬러
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


def test_scan_fills_async_without_asking(styled_qapp, tmp_path):
    """폴더를 올리면 묻지 않고 자리표시 타일이 서고, Crop 이 도착하면 그림이 채워진다(스레드 포함)."""
    import time
    from PyQt6.QtGui import QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w, target = _scan_wafer(tmp_path)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        k = T.key_of(target)
        assert k in dlg.tiles                                  # 자리표시 타일이 바로 선다
        end = time.time() + 20
        while time.time() < end and dlg._scan_thread is not None:
            styled_qapp.processEvents()
            time.sleep(0.01)
        assert dlg._scan_thread is None and isinstance(dlg._scans.get((w, target.index)), QImage)
        assert not dlg.scan_status.text()
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------------------------- 폴더 칩
def _prime(dlg, T, *roots):
    """읽기 스레드 없이 칩 상태를 만든다 — 웨이퍼 목록 · 웨이퍼 데이터를 메모리에 올려 둔다."""
    for r in roots:
        dlg._expand[r] = T.expand_root(r)
        for d, _real in dlg._expand[r]:
            if d not in dlg._cache:
                dlg._cache[d] = T.load_wafer(d)


def _chips(dlg, T) -> list:
    """칩 줄에 놓인 칩(지우려고 숨겨 둔 옛 칩은 레이아웃에 없다)."""
    items = (dlg._chips.itemAt(i).widget() for i in range(dlg._chips.count()))
    return [w for w in items if isinstance(w, T._FolderChip)]


def _no_load(monkeypatch, T):
    def boom(self):
        raise AssertionError("읽기 스레드를 시작하면 안 된다")
    monkeypatch.setattr(T._Load, "start", boom)


def _lot_setup(tmp_path):
    """웨이퍼 폴더 A(14) + LOT(웨이퍼 2장, 28)."""
    a = _wafer_with_photos(tmp_path / "a")
    lot = tmp_path / "lot"
    lot.mkdir()
    for n in ("27125535EWA6", "27125535EWA7"):
        shutil.copytree(a, lot / n)
    return a, lot


def test_chip_count_text_and_lot_chip(styled_qapp, tmp_path):
    """칩마다 Defect 총수(웨이퍼 칩 14 · LOT 칩 28 + 웨이퍼 수), LOT 칩은 이름 옆에 '웨이퍼 2', 툴팁에 웨이퍼별 수."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        inc = dlg._included()
        ia, il = dlg._chip_info(a, inc), dlg._chip_info(lot, inc)
        assert (ia.count, ia.lot, ia.wafers) == (14, False, 1)
        assert (il.count, il.lot, il.wafers) == (28, True, 2)
        chips = _chips(dlg, T)
        assert [c.side.text() for c in chips] == ["14", "28"]
        assert "27125535EWA6" in il.tip and "14" in il.tip and str(lot) in il.tip
        assert "27125535EWA6" not in ia.tip
    finally:
        dlg.deleteLater()


def test_chip_select_filters_tiles_but_not_counts(styled_qapp, tmp_path, monkeypatch):
    """이름을 누르면 사진 칸만 그 폴더(LOT 칩 = 그 안 웨이퍼 전부)로 거른다 — 새로 빠짐 · 남음 · 탭 숫자 · Zone 버튼은 전체 기준 그대로."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert dlg.drop_value.text() == "21" and len(dlg.tiles) == 21          # 3장 × 7
        before = (dlg.drop_value.text(), dlg.keep_label.text(), dlg.tab_btns["drop"].text(),
                  dlg.zone_btns[("Zones", 1)].text())
        dlg.select_folder(a)
        assert len(dlg.tiles) == 7 and dlg.filter_label.text()
        assert {dlg._w.folders[d.wafer] for d in dlg._tile_defects.values()} == {a}
        assert (dlg.drop_value.text(), dlg.keep_label.text(), dlg.tab_btns["drop"].text(),
                dlg.zone_btns[("Zones", 1)].text()) == before                    # 숫자는 그대로
        dlg.select_folder(lot)                                                    # 다른 칩 — 바꾼다
        assert len(dlg.tiles) == 14 and dlg._sel == lot
        assert next(c for c in _chips(dlg, T) if c.info.root == lot).info.selected
        dlg.select_folder(lot)                                                    # 다시 누르면 전체
        assert dlg._sel is None and len(dlg.tiles) == 21 and not dlg.filter_label.text()
    finally:
        dlg.deleteLater()


def test_remove_is_synchronous_and_undo_restores(styled_qapp, tmp_path, monkeypatch):
    """✕ 는 읽지 않는다(스레드 · 덮개 없음) — 메모리에서 다시 합치고 '뺐습니다 · 꼭 잡기 n개도 빠짐' + [되돌리기].
    되돌리면 그 칩과 꼭 잡기가 돌아온다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        dlg.set_mode("auto")
        in_a = next(d for d in dlg._w.defects if dlg._w.folders[d.wafer] == a)
        in_lot = next(d for d in dlg._w.defects if dlg._w.folders[d.wafer] != a)
        dlg.toggle_pick(T.key_of(in_a))
        dlg.toggle_pick(T.key_of(in_lot))
        ov = dict(dlg._ov)
        dlg.remove_folder(lot)
        assert dlg._thread is None and not dlg._overlay_on and not dlg.is_loading()
        assert dlg._roots == [a] and dlg._w.folders == (a,) and len(dlg._picked) == 1
        assert dlg.notice.isVisibleTo(dlg) and dlg.undo_btn.isVisibleTo(dlg)
        assert dlg.notice_label.text() == "lot 를 뺐습니다 · 꼭 잡기 1개도 빠짐"
        assert dlg._view.get(lot)                                                # 칩 설정은 남는다
        dlg._undo()
        assert dlg._roots == [a, lot] and len(dlg._w.folders) == 3 and len(dlg._picked) == 2
        assert dlg._ov == ov and not dlg.notice.isVisibleTo(dlg) and dlg._thread is None
        dlg.remove_folder(a)                                                       # 첫 칩을 빼면 기준이 같아도 새 첫 칩이 된다
        assert dlg._roots == [lot] and dlg._w.folders[0].parent == lot
    finally:
        dlg.deleteLater()


def test_duplicate_add_flashes_and_does_not_reload(styled_qapp, tmp_path, monkeypatch):
    """이미 고른 폴더 · LOT 안 웨이퍼 · 웨이퍼를 이미 다 가진 LOT 을 또 더하면 읽지 않고 그 칩을 반짝이고 '이미 있습니다'."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        dlg.add_folders([a])
        assert dlg._roots == [a, lot] and dlg._flash == {a: "dup"}
        assert dlg.notice_label.text() == f"{a.name} 는 이미 있습니다"
        dlg._flash.clear()
        dlg.add_folders([lot / "27125535EWA6"])                                   # LOT 안 웨이퍼
        assert dlg._roots == [a, lot] and dlg._flash == {lot: "dup"}
        dlg._flash.clear()
        twin = tmp_path / "twin"                                                    # 같은 웨이퍼를 가리키는 또 다른 LOT
        twin.mkdir()
        try:
            (twin / "27125535EWA6").symlink_to(lot / "27125535EWA6", target_is_directory=True)
        except OSError:
            pytest.skip("symlink 불가")
        _prime(dlg, T, twin)
        dlg.add_folders([twin])
        assert dlg._roots == [a, lot] and dlg._flash == {lot: "dup"}
        assert dlg.notice_label.text() == "twin 는 이미 있습니다"
    finally:
        dlg.deleteLater()


def test_add_keeps_screen_usable_and_reports_in_chip(styled_qapp, tmp_path):
    """처음 읽을 때만 화면을 덮는다.  이미 화면이 있으면 덮개 없이 새 칩에서만 진행을 보이고, 끝나면 '더했습니다' 알림 + 초록 칩."""
    import time
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    dlg = T.ThresholdPreviewDialog()

    def wait():
        end = time.time() + 30
        while time.time() < end and (dlg.is_loading() or dlg._thread is not None and dlg._thread.isRunning()):
            styled_qapp.processEvents()
            time.sleep(0.01)
        styled_qapp.processEvents()

    try:
        dlg.add_folders([a])
        assert dlg._overlay_on                                                     # 처음 — 화면이 아직 없다
        wait()
        assert dlg._w is not None and not dlg._overlay_on and dlg._w.folders == (a,)
        assert dlg.notice_label.text() == f"{a.name} 를 더했습니다 · 웨이퍼 1장 · Defect 14"
        dlg.add_folders([lot])
        assert not dlg._overlay_on and dlg._w is not None                          # 화면은 그대로
        chip = next(c for c in _chips(dlg, T) if c.info.root == lot)
        assert chip.info.state == "loading" and "읽는 중" in chip.side.text()
        wait()
        assert dlg._w.folders[0] == a and len(dlg._w.folders) == 3
        assert dlg.notice_label.text() == "lot 를 더했습니다 · 웨이퍼 2장 · Defect 28"
        assert dlg._flash.get(lot) == "added"
    finally:
        dlg.deleteLater()


def test_per_folder_color_scan_default_toggle_and_cache(styled_qapp, tmp_path):
    """칩마다 [컬러 | 스캔]: 컬러 사진이 있으면 컬러, 스캔만 있으면 스캔, 없는 쪽은 못 누른다.
    스캔으로 바꾼 폴더만 Scan Crop 으로 보이고, 잘라 둔 Crop 은 폴더를 빼고 더해도 남는다(키 = (웨이퍼 폴더, index))."""
    from PyQt6.QtGui import QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, _t = _scan_wafer(tmp_path / "a")                      # 컬러 4장 + Scan 목록
    b = tmp_path / "b" / "27125535EWA5"                      # Scan 목록만 — 컬러 사진 0장
    shutil.copytree(FS_UBM, b)
    shutil.copy(a / "ScanResultImageList.txt", b / "ScanResultImageList.txt")
    c = tmp_path / "c" / "27125535EWA5"                      # 사진도 Scan 도 없음
    shutil.copytree(FS_UBM, c)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, b, c)
        dlg.set_folders([a, b, c])
        assert (dlg._view[a], dlg._view[b], dlg._view[c]) == ("color", "scan", "color")
        chips = {ch.info.root: ch for ch in _chips(dlg, T)}
        assert chips[a].seg["color"].isEnabled() and chips[a].seg["scan"].isEnabled()
        assert not chips[b].seg["color"].isEnabled() and chips[b].seg["scan"].isChecked()
        assert not chips[c].seg["color"].isEnabled() and not chips[c].seg["scan"].isEnabled()
        w = dlg._w
        d_a = next(d for d in w.defects if w.folders[d.wafer] == a and T.key_of(d) in w.photos)   # a 의 컬러 있는 Defect
        d_b = next(d for d in w.defects if w.folders[d.wafer] == b)
        img = QImage(20, 20, QImage.Format.Format_RGB888)
        dlg.set_scans({(a, d_a.index): img, (b, d_b.index): img})
        assert not isinstance(dlg._pic(d_a), QImage)               # 컬러 폴더 — 컬러 사진
        assert isinstance(dlg._pic(d_b), QImage)                   # 스캔 폴더 — 컬러 사진이 없으니 Crop
        dlg.set_view(a, "scan")
        assert dlg._view[a] == "scan" and isinstance(dlg._pic(d_a), QImage)
        dlg.set_view(a, "color")
        assert not isinstance(dlg._pic(d_a), QImage)
        dlg.remove_folder(b)
        dlg.add_folders([b])                                        # 뺐다 다시 더해도(읽은 것 그대로) 설정 · Crop 이 남는다
        assert dlg._view[b] == "scan" and (b, d_b.index) in dlg._scans
        assert isinstance(dlg._pic(next(d for d in dlg._w.defects if dlg._w.folders[d.wafer] == b
                                        and d.index == d_b.index)), QImage)
    finally:
        dlg.deleteLater()


def test_merge_base_is_first_folder_even_when_others_are_more(tmp_path):
    """기준 = 첫 웨이퍼의 레시피.  다른 레시피가 더 많아도 첫 쪽이 기준이고 나머지는 제외한다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w1 = _wafer_with_photos(tmp_path / "x")
    others = []
    for n in ("27125535EWA6", "27125535EWA7"):
        o = tmp_path / "y" / n
        shutil.copytree(w1, o)
        ini = next((o / "Zones").glob("amk*.ini"))
        ini.write_text(ini.read_text(encoding="utf-8", errors="replace").replace("LowValue=", "LowValue=1"),
                       encoding="utf-8")
        others.append(o)
    ws = [T.load_wafer(w1)] + [T.load_wafer(o) for o in others]
    m = T.merge_wafers(ws)
    assert m.folders == (w1,) and m.skipped == tuple(others)
    m2 = T.merge_wafers(ws[1:] + ws[:1])                        # 순서를 바꾸면 기준도 바뀐다
    assert m2.folders == tuple(others) and m2.skipped == (w1,)


def test_excluded_chip_and_base_change_resets_values(styled_qapp, tmp_path, monkeypatch):
    """다른 레시피 폴더는 칩에 '제외: 레시피 다름'(점선 · 빼기는 됨).  첫 폴더를 빼 기준이 바뀔 때만 값을 되돌리고 알린다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a = _wafer_with_photos(tmp_path / "a")
    b = tmp_path / "b" / "27125535EWA9"
    shutil.copytree(a, b)
    ini = next((b / "Zones").glob("amk*.ini"))
    ini.write_text(ini.read_text(encoding="utf-8", errors="replace").replace("LowValue=", "LowValue=1"), encoding="utf-8")
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, b)
        dlg.set_folders([a, b])
        ib = dlg._chip_info(b, dlg._included())
        assert ib.state == "excluded" and ib.reason == "제외: 레시피 다름"
        assert dlg._w.folders == (a,)
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        dlg.remove_folder(b)                                       # 기준은 그대로 — 값 유지, 되돌림 알림 없음
        assert dlg._ov and "기준 레시피" not in dlg.notice_label.text()
        dlg.undo_btn.click()
        assert dlg._roots == [a, b] and dlg._ov
        dlg.remove_folder(a)                                       # 첫 폴더를 뺌 → 기준이 b 의 레시피로
        assert dlg._w.folders == (b,) and dlg._ov == {}
        assert dlg.notice_label.text().endswith("기준 레시피가 바뀌어 값을 처음으로 되돌렸습니다")
    finally:
        dlg.deleteLater()


def test_chip_overflow_collapses_into_more_chip(styled_qapp, tmp_path, monkeypatch):
    """칩이 줄 폭을 넘으면 뒤쪽은 '+n 더 보기' 로 접히고, 누르면 접힌 칩 전체가 목록으로 뜬다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    base = _wafer_with_photos(tmp_path / "base")
    roots = [base]
    for i in range(7):
        r = tmp_path / f"r{i}" / f"27125535EWB{i}"
        shutil.copytree(base, r)
        roots.append(r)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.resize(900, 600)
        dlg.show()
        _prime(dlg, T, *roots)
        dlg.set_folders(roots)
        styled_qapp.processEvents()
        dlg._render_chips()
        shown = _chips(dlg, T)
        assert dlg.more_btn is not None and dlg._hidden_chips
        assert len(shown) + len(dlg._hidden_chips) == 8 and shown[0].info.root == base
        assert dlg.more_btn.text() == f"+{len(dlg._hidden_chips)} 더 보기"
        dlg.select_folder(dlg._hidden_chips[-1].root)              # 고른 칩은 접히지 않고 남는다
        assert all(i.root != dlg._sel for i in dlg._hidden_chips)
        dlg._open_more()
        assert len(dlg._more_pop.findChildren(T._FolderChip)) == len(dlg._hidden_chips)
        dlg._more_pop.hide()
    finally:
        dlg.deleteLater()


def test_defect_box_geometry():
    """상세 보기 빨간 박스: 가운데 · 한 변 = Defect 크기 × 3(최소 40 µm, 최대 Crop 300 µm), 크기를 모르면 √면적."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    px = (2.0, 2.0)                                              # µm/px — Crop 300 µm = 150 px
    x, y, w, h = T.defect_box((150, 150), px, 3.0, 4.0)          # 4 px = 8 µm × 3 = 24 → 최소 40 µm = 20 px
    assert (w, h) == (20.0, 20.0) and (x, y) == (65.0, 65.0)
    _x, _y, w, h = T.defect_box((150, 150), px, 10.0, 12.0)      # 12 px = 24 µm × 3 = 72 µm = 36 px
    assert (w, h) == (36.0, 36.0)
    _x, _y, w, h = T.defect_box((150, 150), px, 0.0, 0.0, 100.0)  # √(100 px² × 4) = 20 µm × 3 = 60 µm = 30 px
    assert (w, h) == (30.0, 30.0)
    _x, _y, w, h = T.defect_box((150, 150), px, 90.0, 5.0)       # 180 µm × 3 > 300 → Crop 한 변
    assert (w, h) == (150.0, 150.0)
    _x, _y, w, h = T.defect_box((100, 150), (2.0, 4.0), 1.0, 1.0)  # 축마다 픽셀 크기 — 40 µm = 20 × 10 px
    assert (w, h) == (20.0, 10.0)
    assert T.defect_box((150, 150), (0.0, 2.0), 1.0, 1.0) is None


def test_scan_detail_draws_red_box_only_for_scan_crop(styled_qapp, tmp_path):
    """상세 보기의 Scan Crop 에만 1px 빨간 박스가 들어간다(타일 · 컬러 사진은 그대로)."""
    from PyQt6.QtGui import QColor, QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w, target = _scan_wafer(tmp_path)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        side = round(300.0 / PX)
        img = QImage(side, side, QImage.Format.Format_RGB888)
        img.fill(QColor(90, 90, 90))
        dlg.set_scans({(w, target.index): img})
        dlg._toggle(T.key_of(target))
        big = dlg.grid_host.findChildren(T._BigPhoto)[0].pixmap().toImage()
        box = T.defect_box((side, side), dlg._w.px, target.width, target.length, target.area)
        k = big.width() / side
        mid_y = int((box[1] + box[3] / 2) * k)
        reds = [big.pixelColor(x, mid_y) for x in range(max(0, int(box[0] * k) - 1), int(box[0] * k) + 2)]
        assert any(c.red() > 180 and c.green() < 90 for c in reds)
        assert img.pixelColor(int(box[0]), side // 2).red() == 90          # 원본 Crop 은 건드리지 않는다
        tile_img = dlg.tiles[T.key_of(target)].pic.pixmap().toImage()
        assert not any(tile_img.pixelColor(x, y).red() > 180 and tile_img.pixelColor(x, y).green() < 90
                       for x in range(0, tile_img.width(), 2) for y in range(0, tile_img.height(), 2))
    finally:
        dlg.deleteLater()
