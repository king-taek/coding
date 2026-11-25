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
        dlg.reset()                                                     # 다른 Zone 에서 눌러도 Zone 1 값은 그대로
        dlg._set_zone(("Zones", 1))
        assert dlg.drop_value.text() == "7" and dlg.reset_btn.isEnabled()
        dlg.reset()
        assert dlg.drop_value.text() == "0" and not dlg.reset_btn.isEnabled()
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
    """왼쪽 열 폴더 목록에 놓인 줄(지우려고 숨겨 둔 옛 줄은 레이아웃에 없다)."""
    items = (dlg._folder_lay.itemAt(i).widget() for i in range(dlg._folder_lay.count()))
    return [w for w in items if isinstance(w, T._FolderRow)]


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


def test_many_folders_stay_in_the_left_list_and_scroll(styled_qapp, tmp_path, monkeypatch):
    """폴더가 많아도 접지 않는다 — 왼쪽 열 목록에 전부 줄로 놓이고(+n 더 보기 없음), 열이 세로로만 스크롤된다."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QScrollArea
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
        dlg.resize(1040, 600)
        dlg.show()
        _prime(dlg, T, *roots)
        dlg.set_folders(roots)
        styled_qapp.processEvents()
        rows = _chips(dlg, T)
        assert [r.info.root for r in rows] == roots and not hasattr(dlg, "more_btn")
        side = dlg.findChild(QScrollArea)
        assert side.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert all(r.width() <= T.SIDE_W for r in rows)                 # 잘려 나가거나 가로로 넘치지 않는다
        dlg.select_folder(roots[-1])
        styled_qapp.processEvents()
        assert _chips(dlg, T)[-1].info.selected and _chips(dlg, T)[-1].seg_host.isVisibleTo(dlg)
        assert not _chips(dlg, T)[0].seg_host.isVisibleTo(dlg)            # 컬러 | 스캔 은 고른 줄에만
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
    _x, _y, w, h = T.defect_box((100, 150), (2.0, 4.0), 1.0, 1.0)  # 축마다 Scan 픽셀 크기(300÷100=3, 300÷150=2 µm/px) — 40 µm
    assert (w, h) == pytest.approx((40 / 3.0, 20.0))
    assert T.defect_box((150, 150), (0.0, 2.0), 1.0, 1.0) is None
    # Defect 크기는 Surface 픽셀(px) → µm, µm → Crop 픽셀은 Scan 픽셀 크기(300 µm ÷ Crop 폭) — 둘은 다른 값이다.
    x, y, w, h = T.defect_box((100, 100), (2.0, 2.0), 3.0, 4.0)   # 최소 40 µm, Scan 3 µm/px → 13.33 px
    assert (w, h) == pytest.approx((40 / 3.0, 40 / 3.0)) and (x + w / 2, y + h / 2) == pytest.approx((50.0, 50.0))
    _x, _y, w, h = T.defect_box((100, 100), (2.0, 2.0), 10.0, 12.0)  # 24 µm × 3 = 72 µm → 24 px (Surface 2 µm/px 로 나누면 36 — 틀림)
    assert (w, h) == pytest.approx((24.0, 24.0))


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


def test_scan_detail_red_box_has_all_four_edges_centered(styled_qapp, tmp_path):
    """상세 보기 그림에서 빨간 박스의 위 · 아래 · 왼 · 오른 네 변이 모두 있고, Crop 가운데(±2 px)에 대칭이다."""
    from PyQt6.QtGui import QColor, QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w, target = _scan_wafer(tmp_path)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        dlg._set_zone((target.zone_dir, target.zone))
        dlg._set_tab("keep")
        side = 177                                                     # Scan 300 µm ÷ 177 px — Surface 픽셀 크기와 다르다
        img = QImage(side, side, QImage.Format.Format_RGB888)
        img.fill(QColor(110, 110, 110))
        dlg.set_scans({(w, target.index): img})
        dlg._toggle(T.key_of(target))
        pm = dlg.grid_host.findChildren(T._BigPhoto)[0].pixmap()
        big = pm.toImage()
        red = [(x, y) for y in range(big.height()) for x in range(big.width())
               if big.pixelColor(x, y).red() > 200 and big.pixelColor(x, y).green() < 100]
        assert red
        xs, ys = [p[0] for p in red], [p[1] for p in red]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        mid_x, mid_y = (left + right) // 2, (top + bottom) // 2
        for edge in ([(x, top) for x in range(left, right + 1)], [(x, bottom) for x in range(left, right + 1)],
                     [(left, y) for y in range(top, bottom + 1)], [(right, y) for y in range(top, bottom + 1)]):
            assert all(p in set(red) for p in edge)                      # 네 변 모두 이어진 선
        cx, cy = big.width() / 2.0, big.height() / 2.0
        assert abs((left + right) / 2.0 - cx) <= 2 and abs((top + bottom) / 2.0 - cy) <= 2
        assert abs((right - left) - (bottom - top)) <= 1                 # 정사각
        assert 0 < mid_x < big.width() and 0 < mid_y < big.height()
    finally:
        dlg.deleteLater()


def test_auto_remove_folder_recommends_again_and_undo_restores(styled_qapp, tmp_path, monkeypatch):
    """자동 추천: 꼭 잡기가 있던 폴더를 빼면 그 Zone 추천값은 레시피 값으로 돌아가고(남은 고름이 있으면 그걸로 다시 추천),
    되돌리면 고름과 추천값이 그대로 돌아온다.  직접 조정은 빼도 바꾼 값을 유지한다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        dlg.set_mode("auto")
        in_lot = next(d for d in dlg._w.defects if dlg._w.folders[d.wafer] != a)
        dlg.toggle_pick(T.key_of(in_lot))
        assert dlg._ov                                                   # 추천이 값을 올렸다
        picked, ov, rec = dict(dlg._picked), dict(dlg._ov), set(dlg._rec_zones)
        dlg.remove_folder(lot)                                           # 유일한 꼭 잡기가 사라진다
        assert not dlg._picked and not dlg._ov and not dlg._rec_zones    # 추천값이 레시피로 돌아감
        dlg._undo()
        assert set(dlg._picked) == set(picked) and dlg._ov == ov and dlg._rec_zones == rec
        # 남은 고름이 있으면 그 고름으로 다시 추천 = 그 고름만 골랐을 때의 추천값
        in_a = next(d for d in dlg._w.defects if dlg._w.folders[d.wafer] == a)
        dlg.toggle_pick(T.key_of(in_a))
        both, ov_both = dict(dlg._picked), dict(dlg._ov)
        dlg.remove_folder(lot)
        only_a_ov = dict(dlg._ov)
        dlg.reset()
        dlg.toggle_pick(T.key_of(in_a))
        assert only_a_ov == dlg._ov and len(dlg._picked) == 1
        dlg.reset()
        dlg.set_folders([a, lot])
        for d in both.values():
            dlg.toggle_pick(T.key_of(d))
        assert dlg._ov == ov_both
    finally:
        dlg.deleteLater()


def test_manual_remove_folder_keeps_user_overrides(styled_qapp, tmp_path, monkeypatch):
    """직접 조정에서는 폴더를 빼도 사용자가 바꾼 값이 그대로다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        ov = dict(dlg._ov)
        assert ov
        dlg.remove_folder(lot)
        assert dlg._ov == ov
    finally:
        dlg.deleteLater()


def test_tiles_get_thumbs_in_background_not_on_gui_thread(styled_qapp, tmp_path, monkeypatch):
    """타일은 자리표시로 바로 뜨고, 썸네일은 백그라운드에서 만들어 신호로 올린다(원본 디코드가 GUI 를 막지 않는다)."""
    import threading
    import time
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.utils import image_io
    image_io.clear_tile_cache()
    seen = []
    real = image_io.get_thumb_path

    def spy(src, **kw):
        seen.append(threading.current_thread())
        return real(src, **kw)

    monkeypatch.setattr(image_io, "get_thumb_path", spy)
    from PyQt6.QtGui import QColor, QImage
    wafer = _wafer_with_photos(tmp_path)
    for f in wafer.glob("*.c.*.jpeg"):                                # 빈 파일을 진짜 작은 JPEG 로
        im = QImage(300, 300, QImage.Format.Format_RGB32)
        im.fill(QColor(120, 130, 140))
        assert im.save(str(f), "JPG")
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(wafer))
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert dlg.tiles
        main = threading.current_thread()
        assert all(t is not main for t in seen)                       # 그리는 동안 GUI 스레드는 만들지 않았다
        end = time.time() + 10
        while time.time() < end and any(t.wait_path is not None for t in dlg.tiles.values()):
            styled_qapp.processEvents()
            time.sleep(0.01)
        assert all(t.wait_path is None and t.pic.pixmap() is not None and not t.pic.pixmap().isNull()
                   for t in dlg.tiles.values())
        assert seen and all(t is not main for t in seen)
    finally:
        dlg.close()


def test_drop_and_paste_add_folders(styled_qapp, tmp_path, monkeypatch):
    """끌어놓기·붙여넣기: 사진은 들어 있는 폴더로, 폴더는 그대로 · 못 쓰는 것은 안내(디스크는 drop 에서만)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.ui import drop_paths
    from PyQt6.QtCore import QMimeData, QUrl
    dlg = T.ThresholdPreviewDialog()
    try:
        got = []
        monkeypatch.setattr(dlg, "add_folders", lambda fs: got.append([Path(f) for f in fs]))
        d1, d2 = tmp_path / "a", tmp_path / "b"
        d1.mkdir(); d2.mkdir()
        (d2 / "x.jpg").write_bytes(b"")
        (tmp_path / "t.txt").write_text("x")
        dlg._add_dropped([str(d1), str(d2 / "x.jpg")])
        assert got == [[d1, d2]]
        dlg._add_dropped([str(tmp_path / "t.txt")])
        assert dlg.notice_label.text() == T._KO.DROP_WRONG_KIND_FOLDER and len(got) == 1
        dlg._on_paste_no_path(True)
        assert dlg.notice_label.text() == T._KO.DROP_NO_PATH_IMAGE
        m = QMimeData()
        m.setUrls([QUrl.fromLocalFile(str(d1))])
        assert drop_paths.local_paths(m) == [str(d1)]
    finally:
        dlg.close()


# ---------------------------------------------------------------- 화면 개편(2026-11-17): 왼쪽 열 · 비율 막대 · 밑줄 탭 · 밀착 타일 · 줄 아래 상세
def _shown_dlg(T, styled_qapp, tmp_path, mode="manual"):
    dlg = T.ThresholdPreviewDialog()
    dlg.resize(1280, 800)
    dlg.show()
    dlg.set_wafer(T.load_wafer(_wafer_with_photos(tmp_path)))
    dlg.set_mode(mode)
    styled_qapp.processEvents()
    return dlg


def test_summary_is_one_line_with_ratio_bar(styled_qapp, tmp_path):
    """요약은 한 줄 수치(빠짐 · 남음 · 전체) + 비율 막대 하나 — 막대 값이 수치와 같고, 꼭 잡기 · 놓침은 있을 때만 나온다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        assert (dlg.ratio.drop, dlg.ratio.total) == (0, 10) and not dlg.ratio.ticks
        assert (dlg.keep_label.text(), dlg.all_label.text()) == ("10", "10")
        assert not dlg.pick_box.isVisibleTo(dlg) and not dlg.miss_box.isVisibleTo(dlg)
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert (dlg.ratio.drop, dlg.ratio.total) == (7, 10) and dlg.drop_value.property("tone") == "over"
        assert (dlg.keep_label.text(), dlg.all_label.text()) == ("3", "10")
        row = dlg.zone_btns[("Zones", 1)]                                # Zone 행의 작은 막대도 같은 값
        assert (row._drop, row._total) == (7, 10)
        dlg.set_mode("auto")
        d = max(dlg._in_zone(), key=lambda x: x.area)
        dlg.toggle_pick(T.key_of(d))
        assert dlg.pick_box.isVisibleTo(dlg) and dlg.pick_label.text() == "1"
        assert len(dlg.ratio.ticks) == 1 and 0 < dlg.ratio.ticks[0] < 1   # 꼭 잡기 눈금
    finally:
        dlg.deleteLater()


def test_zone_rows_are_rows_not_boxes(styled_qapp, tmp_path):
    """Zone 은 상자 버튼이 아니라 행(이름 + 작은 막대 + 빠짐 수) — 하나만 골라지고, 글자는 읽을 수 있게 남는다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        rows = dlg.zone_btns
        assert all(isinstance(b, T._ZoneRow) for b in rows.values())
        assert rows[("Zones", 1)].isChecked() and not rows[("Zones", 63)].isChecked()
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert "−7" in rows[("Zones", 1)].text() and "Defect 10" in rows[("Zones", 1)].text()
        rows[("Zones", 63)].click()
        assert dlg._zone == ("Zones", 63) and not rows[("Zones", 1)].isChecked()
        assert rows[("Zones", 1)].grab().width() > 0                      # 그려진다(예외 없음)
    finally:
        dlg.deleteLater()


def test_left_column_stacks_folders_above_zones_without_chip_row(styled_qapp, tmp_path, monkeypatch):
    """폴더 칩 줄은 없다 — 왼쪽 열 위가 폴더 목록, 아래가 Zone 이고, 윗줄에는 폴더 버튼이 없다.
    [컬러 | 스캔] 은 고른 폴더 줄에만 보이고, ✕ 는 줄마다 하나다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    _no_load(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.resize(1280, 800)
        dlg.show()
        _prime(dlg, T, a, lot)
        dlg.set_folders([a, lot])
        styled_qapp.processEvents()
        assert not hasattr(dlg, "_chips_host")
        rows = _chips(dlg, T)
        assert [r.info.root for r in rows] == [a, lot] and all(r.x_btn is not None for r in rows)
        assert dlg.pick_btn.property("role") == "textAccent"              # 조용한 [+ 폴더]
        assert dlg.pick_btn.mapTo(dlg, dlg.pick_btn.rect().topLeft()).x() < T.SIDE_W
        assert rows[0].mapTo(dlg, rows[0].rect().topLeft()).y() < dlg.zone_btns[("Zones", 1)].mapTo(dlg, dlg.zone_btns[("Zones", 1)].rect().topLeft()).y()
        assert not any(r.seg_host.isVisibleTo(dlg) for r in rows)
        dlg.select_folder(lot)
        styled_qapp.processEvents()
        rows = _chips(dlg, T)
        assert [r.seg_host.isVisibleTo(dlg) for r in rows] == [False, True]
        rows[1].x_btn.click()                                              # ✕ — 빼기
        assert dlg._roots == [a]
    finally:
        dlg.deleteLater()


def test_underline_tabs_and_tile_class_heading(styled_qapp, tmp_path):
    """탭은 밑줄 탭(하나만 골라짐), 타일은 분류 이름 제목이 격자 위에 한 번만 — 타일마다 캡션은 없다."""
    from PyQt6.QtWidgets import QLabel
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        assert {b.property("role") for b in dlg.tab_btns.values()} == {"underTab"}
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        assert dlg.tab_btns["drop"].isChecked() and not dlg.tab_btns["keep"].isChecked()
        dlg.tab_btns["keep"].click()
        styled_qapp.processEvents()
        assert dlg._tab == "keep" and dlg.tab_btns["keep"].isChecked() and not dlg.tab_btns["drop"].isChecked()
        heads = [lab for lab in dlg.grid_host.findChildren(QLabel) if lab.property("role") == "ovGroupName" and lab.isVisibleTo(dlg)]
        assert len(heads) == 1 and "RDL Defect" in heads[0].text() and "3" in heads[0].text()
        assert len(dlg.tiles) == 3
        tile = next(iter(dlg.tiles.values()))
        assert tile.size().width() == tile.size().height() == T.TILE_PX    # 사진만 — 캡션 줄이 없다
        assert [lab for lab in tile.findChildren(QLabel) if lab.property("role") == "muted"] == []
    finally:
        dlg.deleteLater()


def test_tile_marks_pick_check_without_scan_mark_or_more_button(styled_qapp):
    """Scan Crop 에도 사진 위 글자 표시(S)는 없다.  꼭 잡기 = 오른쪽 위 체크.  가리킬 때 나오던 … 버튼은 없다."""
    from PyQt6.QtGui import QImage
    from PyQt6.QtWidgets import QLabel
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    img = QImage(40, 40, QImage.Format.Format_RGB888)
    plain = T._Tile(img, T.TILE_PX, picked=False)
    picked = T._Tile(img, T.TILE_PX, picked=True)
    color = T._Tile(Path("x.jpg"), T.TILE_PX)
    plain.show()
    assert not [lab for lab in plain.findChildren(QLabel) if lab.property("role") == "tileMark"]
    assert not [lab for lab in color.findChildren(QLabel) if lab.property("role") == "tileMark"]
    assert "S" not in [lab.text() for lab in plain.findChildren(QLabel)]
    assert picked.badge.isVisibleTo(picked) and not plain.badge.isVisibleTo(plain)
    assert picked.badge.x() > T.TILE_PX // 2 and picked.badge.y() < 12       # 오른쪽 위
    assert not hasattr(plain, "detail_btn") and not hasattr(plain, "detail")


def test_detail_opens_right_under_its_row_as_hairline_band(styled_qapp, tmp_path):
    """타일을 누르면 그 줄 바로 아래에 위아래 선만 있는 상세가 펼쳐진다(굵은 파란 상자 · 긴 경로 없음).
    다시 누르거나 [닫기] 로 접는다.  자동 추천에서도 같다 — [꼭 잡기] 버튼은 없다(팝업에서 고른다)."""
    from PyQt6.QtWidgets import QFrame, QPushButton
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        keys = list(dlg.tiles)
        dlg._toggle(keys[0])
        styled_qapp.processEvents()
        band = dlg.grid_host.findChildren(QFrame)
        box = next(f for f in band if f.property("role") == "ovDetail")
        assert box.property("role") != "card" and "2px" not in box.styleSheet()
        band_w = dlg._band
        i = dlg.grid.indexOf(band_w)
        assert band_w is box.parentWidget().parentWidget() and dlg.grid.itemAt(i - 1).widget() is dlg._row_of[keys[0]]   # 그 줄 바로 아래
        names = [b.text() for b in box.findChildren(QPushButton)]
        assert T._KO.OVERKILL_DETAIL_CLOSE in names
        assert dlg.tiles[keys[0]].ring.isVisibleTo(dlg)                    # 펼친 타일 표시
        txt = [lab.text() for lab in box.findChildren(T.QLabel)]
        assert not any(t.startswith("/") or "Scanresult" in t for t in txt)   # 긴 경로 없음
        next(b for b in box.findChildren(QPushButton) if b.text() == T._KO.OVERKILL_DETAIL_CLOSE).click()
        assert dlg._open is None and not [p for p in dlg.grid_host.findChildren(T._BigPhoto) if p.isVisibleTo(dlg)]
        dlg.set_mode("auto")
        k0 = next(iter(dlg.tiles))
        dlg._toggle(k0)
        styled_qapp.processEvents()
        box = next(f for f in dlg.grid_host.findChildren(QFrame) if f.property("role") == "ovDetail" and f.isVisibleTo(dlg))
        assert [b.text() for b in box.findChildren(QPushButton)] == [T._KO.OVERKILL_DETAIL_CLOSE]   # 자동 추천도 꼭 잡기 버튼 없음
        assert dlg._picked == {} and dlg._open == k0
    finally:
        dlg.deleteLater()


def test_criterion_operator_follows_filter_type_and_flags_adjustment():
    """기준 앞 연산자 — 1(범위) · 5 는 ≥, 4 는 >.  슬라이더로 옮겼으면(조정) 레시피 값과 다르다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    assert T.filter_op(1) == "≥" and T.filter_op(5) == "≥" and T.filter_op(4) == ">"
    C = T.Criterion
    assert not C("Area", 12.3, 5.0, 5.0, 1).adjusted
    assert C("Area", 12.3, 8.0, 5.0, 1).adjusted


def _open_first_detail(T, dlg, styled_qapp):
    dlg._toggle(next(iter(dlg.tiles)))
    styled_qapp.processEvents()
    return next(f for f in dlg.grid_host.findChildren(T.QFrame) if f.property("role") == "ovDetail")


def _chip_texts(chip):
    return [lab.text() for lab in chip.findChildren(T_QLabel()) if lab.text()]


def T_QLabel():
    from PyQt6.QtWidgets import QLabel
    return QLabel


def test_detail_chip_shows_value_and_recipe_criterion_without_sentence(styled_qapp, tmp_path):
    """상세 — 문장 없이 기준마다 칩 하나: 이름 · 이 Defect 의 값 · '레시피 ≥ 값'.  옮겼을 때만 '조정 ≥ 값'.  지금 기준에 걸린 값은 빨강."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg._set_tab("keep")
        box = _open_first_detail(T, dlg, styled_qapp)
        assert not [lab for lab in box.findChildren(T.QLabel) if lab.property("role") == "ovVerdict"]
        assert not any("차이로" in lab.text() for lab in box.findChildren(T.QLabel))
        d = dlg._tile_defects[next(iter(dlg.tiles))]
        crits = dlg._criteria(d, P.why_dropped(d, dlg._w.zones, dlg._ov))
        chips = [f for f in box.findChildren(T.QFrame) if f.property("role") == "ovChip"]
        assert crits and len(chips) == len(crits)
        for chip, cr in zip(chips, crits):
            txt = _chip_texts(chip)
            assert cr.name in txt and f"{cr.value:.{cr.dec}f}" in txt
            assert T._KO.THRESH_PREVIEW_RECIPE_VALUE.format(v=f"{cr.op} {cr.recipe:.{cr.dec}f}") in txt
            assert not any(t.startswith("조정") for t in txt)                 # 안 옮겼다 — 조정 없음
        dlg.set_value(14, R.FEATURE_AREA, 40.0)                                  # 슬라이더를 옮긴 뒤
        dlg._set_tab("drop")
        keys = list(dlg.tiles)
        dlg._toggle(keys[0])                                                     # 열면
        styled_qapp.processEvents()
        box = next(f for f in dlg.grid_host.findChildren(T.QFrame) if f.property("role") == "ovDetail" and f.isVisibleTo(dlg))
        d = dlg._tile_defects[keys[0]]
        crits = dlg._criteria(d, P.why_dropped(d, dlg._w.zones, dlg._ov))
        area = next(c for c in crits if c.name == "Area")
        chip = next(f for f in box.findChildren(T.QFrame) if f.property("role") == "ovChip"
                    and "Area" in _chip_texts(f))
        assert T._KO.OVERKILL_CHIP_ADJUSTED.format(v=f"≥ {area.cut:.{area.dec}f}") in _chip_texts(chip)
        assert area.adjusted and f"{area.cut:.1f}" == "40.0"
        val = next(lab for lab in chip.findChildren(T.QLabel) if lab.property("role") == "ovChipValue")
        assert bool(val.property("fail")) == (not area.ok)
    finally:
        dlg.deleteLater()


def test_criteria_ok_flag_matches_kept_or_dropped(styled_qapp, tmp_path):
    """빠진 Defect 는 걸린 기준이 하나 이상(ok 거짓), 남은 Defect 는 모든 기준이 ok 다(묶음 안은 전부 통과 — ``preview.why_dropped`` 와 같은 규칙)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        drops = dlg.dropped()
        assert drops
        for d, why in drops:
            crits = dlg._criteria(d, why)
            assert crits and any(not c.ok for c in crits)
        kept = [d for d in dlg._in_zone() if P.is_kept(d, dlg._w.zones, dlg._ov)]
        assert kept
        for d in kept:
            assert all(c.ok for c in dlg._criteria(d, None))
    finally:
        dlg.deleteLater()


def test_defect_box_toggle_hides_the_red_box_and_persists(styled_qapp, tmp_path):
    """Defect 표시 토글 — 끄면 빨간 박스 없이 다시 그리고, 고른 값은 다음 상세에도 이어진다.  Scan 이 아니면 토글이 없다."""
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

        def reds():
            styled_qapp.processEvents()
            im = dlg.grid_host.findChildren(T._BigPhoto)[-1].pixmap().toImage()
            return sum(1 for x in range(im.width()) for y in range(im.height())
                       if im.pixelColor(x, y).red() > 180 and im.pixelColor(x, y).green() < 90)
        dlg._toggle(T.key_of(target))
        tog = dlg.detail_box_btn
        assert tog.text() == T._KO.OVERKILL_DEFECT_BOX and tog.isCheckable() and tog.isChecked()
        assert reds() > 0
        tog.click()                                                   # 끈다
        assert not tog.isChecked() and reds() == 0 and dlg._show_box is False
        dlg._toggle(T.key_of(target))                                 # 닫았다가 다시 열어도
        dlg._toggle(T.key_of(target))
        assert not dlg.detail_box_btn.isChecked() and reds() == 0
        dlg.detail_box_btn.click()                                    # 다시 켠다
        assert reds() > 0 and dlg._show_box is True
        tog = dlg.detail_box_btn
        assert img.pixelColor(side // 2, side // 2).red() == 90       # 원본 Crop 은 건드리지 않는다
    finally:
        dlg.deleteLater()


def test_defect_box_toggle_is_absent_for_color_photos(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg.set_value(14, R.FEATURE_AREA, 40.0)
        box = _open_first_detail(T, dlg, styled_qapp)
        assert T._KO.OVERKILL_DEFECT_BOX not in [b.text() for b in box.findChildren(T.QPushButton)]
    finally:
        dlg.deleteLater()


def test_rules_panel_is_boxless_with_distribution_aligned_to_slider(styled_qapp, tmp_path):
    """기준 열: 둥근 상자 없음, 슬라이더 · 분포 · 자름선이 같은 x(손잡이 중심) — 값을 바꾸면 자름선도 따라가고, 슬라이더는 남아 있다."""
    from PyQt6.QtWidgets import QStyle, QStyleOptionSlider
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        assert dlg.rules.property("role") != "card"
        sl, spin = dlg.sliders[(14, R.FEATURE_AREA)]
        assert spin.height() == 32 and sl.property("role") == "axis"
        hist = next(h for h in dlg.rules.findChildren(T._Hist) if h.parentWidget() is sl.parentWidget())
        assert hist.width() == sl.width()                                   # 같은 폭
        for um in (30.0, 40.0, 60.0):
            dlg.set_value(14, R.FEATURE_AREA, um)
            styled_qapp.processEvents()
            opt = QStyleOptionSlider()
            sl.initStyleOption(opt)
            hr = sl.style().subControlRect(QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, sl)
            tol = 1.5 + ((hist.width() - 2 * T._Hist.PAD) / sl.maximum() if hist._axis.log else 0)   # 로그 축은 정수 칸에 맞춘다
            assert abs(hist.cut_x() - hr.center().x()) <= tol, um           # 자름선 = 손잡이 중심
    finally:
        dlg.deleteLater()


# ------------------------------------------------------------------ 2026-11-17 디자인 검토 반영
def test_empty_overkill_shows_one_line_and_add_button_and_disables_modes(styled_qapp):
    """폴더가 없으면 본문 가운데 안내 + [+ 폴더](32px), 모드 버튼은 꺼지고 'Zone' 소제목은 숨는다."""
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.resize(1280, 800)
        dlg.show()
        styled_qapp.processEvents()
        assert dlg.empty_state.isVisibleTo(dlg)
        assert dlg.empty_hint.text() == i18n.KO.OVERKILL_EMPTY
        assert dlg.empty_pick_btn.text() == i18n.KO.OVERKILL_ADD_FOLDER
        assert dlg.empty_pick_btn.property("role") == "ghost" and dlg.empty_pick_btn.height() == 32
        assert not any(b.isEnabled() for b in dlg.mode_btns.values())
        assert not dlg._zone_head.isVisibleTo(dlg) and not dlg.summary.isVisibleTo(dlg)
        assert dlg.empty_hint.geometry().center().x() > dlg.width() // 3        # 본문 가운데쪽
    finally:
        dlg.deleteLater()


def test_empty_state_goes_away_with_a_wafer(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        assert not dlg.empty_state.isVisibleTo(dlg)
        assert all(b.isEnabled() for b in dlg.mode_btns.values())
        assert dlg._zone_head.isVisibleTo(dlg) and dlg.summary.isVisibleTo(dlg)
    finally:
        dlg.deleteLater()


def test_summary_and_tab_use_the_same_word_for_dropped(styled_qapp, tmp_path):
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    assert i18n.KO.OVERKILL_STAT_DROP == i18n.KO.THRESH_PREVIEW_TAB_DROP == "새로 빠짐"


def test_scan_detail_puts_scan_beside_title_and_file_name_below(styled_qapp, tmp_path):
    """Scan Crop 상세: 'Scan' 은 제목 줄(Zone … 옆), 아래 줄은 파일 이름(끝부분) — 'Scan' 한 단어만 뜨지 않는다."""
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QLabel
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
        img.setText("src", str(w / "wafer1.t.jpg"))
        dlg.set_scans({(w, target.index): img})
        dlg._toggle(T.key_of(target))
        texts = [l.text() for l in dlg.grid_host.findChildren(QLabel)]
        assert any(t.startswith("Zone ") and t.endswith(" · Scan") for t in texts)
        files = [l for l in dlg.grid_host.findChildren(QLabel) if l.property("role") == "ovFile"]
        assert [l.text() for l in files] == ["…/wafer1.t.jpg"]
        # 원본 이름을 모르면 빈 줄 대신 아예 숨긴다
        img2 = QImage(side, side, QImage.Format.Format_RGB888)
        img2.fill(QColor(90, 90, 90))
        dlg.set_scans({(w, target.index): img2})
        dlg._toggle(T.key_of(target))
        dlg._toggle(T.key_of(target))
        files = [l for l in dlg.grid_host.findChildren(QLabel) if l.property("role") == "ovFile"]
        assert files and not files[0].isVisibleTo(dlg) and files[0].text() != "Scan"
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------------------------------------- 읽는 중 중지
def _stub_start(monkeypatch, T):
    monkeypatch.setattr(T._Load, "start", lambda self: None)


def test_loading_overlay_shows_stop_button(styled_qapp, tmp_path, monkeypatch):
    """읽어야 하는 폴더를 고르면 덮개에 [중지] 가 보인다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, _lot = _lot_setup(tmp_path)
    _stub_start(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_folders([a])
        assert dlg._overlay_on
        assert not dlg._loading._cancel_btn.isHidden()
    finally:
        dlg.deleteLater()


def test_stop_drops_unread_roots_and_ignores_late_result(styled_qapp, tmp_path, monkeypatch):
    """[중지] → 읽던 폴더가 빠지고 덮개가 닫힌다.  옛 읽기의 늦은 마무리는 아무것도 바꾸지 않는다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, _lot = _lot_setup(tmp_path)
    _stub_start(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_folders([a])
        old = dlg._token
        dlg._loading.cancel_requested.emit()
        assert dlg._roots == [] and not dlg._overlay_on and dlg._token > old
        dlg._on_loaded(old, True)
        assert dlg._roots == [] and not dlg._overlay_on
    finally:
        dlg.deleteLater()


def test_stop_keeps_already_read_roots(styled_qapp, tmp_path, monkeypatch):
    """이미 읽은 폴더가 있으면 그것만 남는다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    a, lot = _lot_setup(tmp_path)
    dlg = T.ThresholdPreviewDialog()
    try:
        _prime(dlg, T, a)
        _stub_start(monkeypatch, T)
        dlg.set_folders([a, lot])
        assert dlg._roots == [a, lot] and dlg._overlay_on
        dlg._loading.cancel_requested.emit()
        assert dlg._roots == [a] and not dlg._overlay_on
    finally:
        dlg.deleteLater()


# ------------------------------------------------------------------ 2026-11-17 UX 다듬기
def test_defect_box_toggle_shows_state_by_color_only_no_check_icon(styled_qapp, tmp_path):
    """'Defect 표시' — 켜짐 · 꺼짐 모두 체크 아이콘이 없다(상태는 색으로만, QSS :checked)."""
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
        tog = dlg.detail_box_btn
        assert tog.isChecked() and tog.icon().isNull()
        tog.click()
        assert not tog.isChecked() and tog.icon().isNull()
        assert not hasattr(T, "_check_icon")
    finally:
        dlg.deleteLater()


def test_clicking_tiles_keeps_tile_widgets_and_opens_closes_the_band(styled_qapp, tmp_path):
    """타일을 눌러 띠를 펼치고 · 다른 타일로 옮기고 · 접어도 타일 위젯은 그대로다(다시 만들지 않는다 — 썸네일이 깜박이지 않게)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg._set_tab("keep")
        keys = list(dlg.tiles)
        assert len(keys) >= 2
        before = {k: id(t) for k, t in dlg.tiles.items()}
        assert dlg._band is None
        dlg.tiles[keys[0]].clicked.emit()
        styled_qapp.processEvents()
        assert dlg._open == keys[0] and dlg._band is not None and dlg.tiles[keys[0]].is_open
        band = dlg._band
        dlg.tiles[keys[1]].clicked.emit()                                  # 다른 타일(같은 줄이든 아래 줄이든)
        styled_qapp.processEvents()
        assert dlg._open == keys[1] and dlg._band is not None and dlg._band_row is dlg._row_of[keys[1]]
        assert dlg.tiles[keys[1]].is_open and not dlg.tiles[keys[0]].is_open
        assert dlg.grid.itemAt(dlg.grid.indexOf(dlg._band) - 1).widget() is dlg._row_of[keys[1]]
        if dlg._row_of[keys[0]] is dlg._row_of[keys[1]]:
            assert dlg._band is band                                       # 같은 줄 — 띠를 새로 만들지 않고 삼각형만 옮긴다
            assert dlg._band.caret == pytest.approx(dlg._caret_x(keys[1]))
        dlg.tiles[keys[1]].clicked.emit()                                  # 같은 타일 다시 = 접기
        styled_qapp.processEvents()
        assert dlg._open is None and dlg._band is None
        assert not [b for b in dlg.grid_host.findChildren(T._DetailBand) if b.isVisibleTo(dlg)]
        assert {k: id(t) for k, t in dlg.tiles.items()} == before
        assert not any(t.is_open for t in dlg.tiles.values())
    finally:
        dlg.deleteLater()


def test_open_tile_has_three_px_ring_and_band_caret_points_at_it(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg._set_tab("keep")
        k = list(dlg.tiles)[1]
        dlg._toggle(k)
        styled_qapp.processEvents()
        t = dlg.tiles[k]
        assert t.ring.isVisibleTo(dlg) and not t.hover.isVisibleTo(dlg)
        assert "3px" in styled_qapp.styleSheet().split('tileRing')[1].split("}")[0]
        # 삼각형 x = 그 타일 가운데(띠와 줄이 같은 왼쪽 끝)
        assert (t.mapTo(dlg.grid_host, t.rect().center()).x() - dlg._band.x()) == pytest.approx(dlg._band.caret, abs=1.5)
    finally:
        dlg.deleteLater()


def _ask_first_action(monkeypatch, T, seen):
    """우클릭 메뉴를 띄우지 않고 첫 항목을 고른 것으로 한다."""
    def fake_exec(self, *a, **k):
        seen.extend(x.text() for x in self.actions())
        return self.actions()[0]
    monkeypatch.setattr(T.QMenu, "exec", fake_exec)


def test_right_click_on_color_tile_and_big_photo_offers_enlarge(styled_qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QPoint
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg._set_tab("keep")
        seen, shown = [], []
        _ask_first_action(monkeypatch, T, seen)
        monkeypatch.setattr(dlg, "_enlarge", lambda photo, d=None: shown.append(photo))
        k = next(iter(dlg.tiles))
        tile = dlg.tiles[k]
        tile.customContextMenuRequested.emit(QPoint(5, 5))                 # 우클릭
        assert seen == [T._KO.CTX_VIEW_LARGER] and shown == [dlg._pic(dlg._tile_defects[k])]
        assert dlg._open is None and not dlg._picked                       # 선택 · 꼭 잡기는 건드리지 않는다
        dlg._toggle(k)
        big = dlg._band.findChildren(T._BigPhoto)[0]
        big.customContextMenuRequested.emit(QPoint(5, 5))
        assert len(shown) == 2 and dlg._open == k
    finally:
        dlg.deleteLater()


def test_right_click_on_scan_tile_opens_full_scan_with_overlay_or_falls_back_to_crop(styled_qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QPoint
    from PyQt6.QtGui import QColor, QImage
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.ui.widgets import zoom_window as Z
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
        seen, opened = [], []
        _ask_first_action(monkeypatch, T, seen)

        class _V:
            def __init__(self, path, parent=None, *, image=None, overlay=None):
                opened.append((Path(path), image, overlay))
        monkeypatch.setattr(Z, "FullscreenViewer", _V)
        monkeypatch.setattr(T.sheets, "run", lambda viewer, **k: None)
        dlg.tiles[T.key_of(target)].customContextMenuRequested.emit(QPoint(5, 5))
        assert seen == [T._KO.CTX_VIEW_LARGER]
        th = dlg._full_scan
        assert th is not None
        th.wait(20000)
        styled_qapp.processEvents()
        assert len(opened) == 1 and opened[0][1] is not None               # 원본(없으면 Crop 그 자체) 이 열렸다
        if opened[0][2] is not None:
            assert len(opened[0][2]) == 4                                  # Scan 원본 위 Crop 범위
        dlg._on_full_scan(img, None, None, "")                             # 원본을 못 읽으면 Crop 을 크게 본다
        assert opened[-1][1] is img and opened[-1][2] is None
        n = len(opened)
        dlg._enlarge_tile(T.key_of(target))                                # 빠른 두 번 클릭 — 읽는 중에는 한 번만
        dlg._enlarge_tile(T.key_of(target))
        th = dlg._full_scan
        th.wait(20000)
        styled_qapp.processEvents()
        assert len(opened) == n + 1
    finally:
        dlg.deleteLater()


def test_left_click_on_scan_big_photo_enlarges_like_right_click(styled_qapp, tmp_path, monkeypatch):
    """펼친 값의 Scan Crop 사진도 Color 처럼 누르면 크게 본다 — 우클릭 [크게보기] 와 같은 경로."""
    from PyQt6.QtCore import Qt
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
        k = T.key_of(target)
        dlg._toggle(k)
        assert isinstance(dlg._pic(dlg._tile_defects[k]), QImage)
        big = dlg._band.findChildren(T._BigPhoto)[0]
        assert big.cursor().shape() == Qt.CursorShape.PointingHandCursor
        asked = []
        monkeypatch.setattr(dlg, "_enlarge_tile", lambda i: asked.append(i))
        big.clicked.emit()
        assert asked == [k]
    finally:
        dlg.deleteLater()


def test_log_axis_rule_and_roundtrip():
    """90번째 백분위가 선형 축 앞 10% 안이면 로그.  로그 축의 칸 ↔ 값은 되돌아오고 레시피 · 최소 · 최대에 정확히 닿는다."""
    import math
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    squashed = [10.0 + i * 0.1 for i in range(100)] + [4571.0]
    assert T.use_log_axis(5.0, 4571.0, squashed)                             # 거의 다 10~20, 하나만 4571
    assert not T.use_log_axis(5.0, 100.0, [5.0 + i for i in range(96)])      # 고르게 퍼짐 — 선형
    assert not T.use_log_axis(0.0, 100.0, [1.0, 2.0]) and not T.use_log_axis(5.0, 5.0, [5.0]) and not T.use_log_axis(5.0, 9.0, [])
    rec, hi = 5.0, 4571.0
    ax = T._Axis(rec, hi, rec, 0.5, 1, 0, 1, log=True)
    assert ax.log and ax.n == T.LOG_STEPS
    assert ax.value_at(0) == rec and ax.value_at(ax.n) == hi
    assert ax.frac(rec) == 0 and ax.frac(hi) == 1 and ax.frac(math.sqrt(rec * hi)) == pytest.approx(0.5)
    for i in range(ax.n + 1):
        assert ax.index_of(ax.value_at(i)) == i                              # 정수 칸 ↔ 값 되돌아옴
    assert all(ax.value_at(i) <= ax.value_at(i + 1) for i in range(ax.n))
    down = T._Axis(2.0, 4571.0, 5.0, 0.5, 1, 6, 1, log=True)                 # 내리기 — 레시피(5.0)가 중간 칸이어도 정확히 닿는다
    assert down.value_at(down.index_of(5.0)) == 5.0
    lin = T._Axis(5.0, 25.0, 5.0, 0.5, 1, 0, 40)                             # 선형은 예전 그대로
    assert not lin.log and lin.value_at(3) == 6.5 and lin.index_of(6.5) == 3 and lin.frac(15.0) == 0.5
    assert T.log_decades(5.0, 4571.0) == [10.0, 100.0, 1000.0]


def test_log_axis_slider_and_histogram_share_the_mapping(styled_qapp, tmp_path):
    """Area(30~314 µm² — 거의 다 아래쪽)는 로그 축: 슬라이더 맨 앞 = 레시피, 끝 = 최댓값, 입력한 값은 정확히 적용되고 손잡이는 가장 가까운 칸."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        sl, spin = dlg.sliders[(14, R.FEATURE_AREA)]
        hist = next(h for h in dlg.rules.findChildren(T._Hist) if h.parentWidget() is sl.parentWidget())
        foot = next(f for f in dlg.rules.findChildren(T._AxisFoot) if f.parentWidget() is sl.parentWidget())
        ax = hist._axis
        assert ax.log and sl.maximum() == ax.n and foot._decades == [100.0]
        sl.setValue(ax.n)                                                    # 끝까지 — 최댓값
        assert float(spin.text()) == pytest.approx(ax.hi, abs=0.051)
        sl.setValue(0)
        assert float(spin.text()) == pytest.approx(ax.rec, abs=0.051) and not dlg._ov
        dlg.set_value(14, R.FEATURE_AREA, 100.0)                             # 입력값은 정확히 적용
        assert float(spin.text()) == 100.0
        assert sl.value() == ax.index_of(100.0) and abs(ax.value_at(sl.value()) - 100.0) < 100.0 * 0.03
        assert hist.cut_x() == pytest.approx(T._Hist.PAD + ax.frac(100.0) * (hist.width() - 2 * T._Hist.PAD))
    finally:
        dlg.deleteLater()


# ---------------------------------------------------------------- Defect 고르기 팝업(2026-11-17)
def _capture_run(monkeypatch, T):
    """sheet_host.run 을 막지 않는 가짜로 — 띄운 팝업을 모아 둔다(넘긴 옵션은 `shown_kw`)."""
    class _Shown(list):
        kw: list = []

    shown = _Shown()
    shown.kw = []
    monkeypatch.setattr(T.sheets, "run", lambda dlg, **kw: shown.append(dlg) or shown.kw.append(kw) or 0)
    return shown


def _wait_apply(dlg, qapp, limit_s=10.0):
    """[적용] 의 백그라운드 계산이 끝나 로딩이 걷힐 때까지 이벤트를 돌린다."""
    import time
    t0 = time.perf_counter()
    while dlg._overlay_on and time.perf_counter() - t0 < limit_s:
        qapp.processEvents()
        time.sleep(0.005)
    assert not dlg._overlay_on


def test_auto_mode_tile_click_opens_band_without_picking(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        k0 = next(iter(dlg.tiles))
        dlg.tiles[k0].clicked.emit()
        styled_qapp.processEvents()
        assert dlg._open == k0 and dlg._band is not None and dlg._picked == {} and dlg._ov == {}
    finally:
        dlg.deleteLater()


def test_pick_button_only_in_auto_and_needs_wafer(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        assert not dlg.pick_open_btn.isVisibleTo(dlg)
        dlg.set_mode("auto")
        assert dlg.pick_open_btn.isVisibleTo(dlg) and dlg.pick_open_btn.isEnabled()
        assert dlg.pick_open_btn.text() == T._KO.OVERKILL_PICK_OPEN
        dlg.set_mode("manual")
        assert not dlg.pick_open_btn.isVisibleTo(dlg)
        dlg.set_wafer(None)
        dlg.set_mode("auto")
        assert not dlg.pick_open_btn.isEnabled()
        dlg.open_pick_dialog()                                             # 웨이퍼 없음 — 열리지 않는다
        assert dlg._pick_dlg is None
    finally:
        dlg.deleteLater()


def test_open_pick_dialog_builds_with_current_zone_and_items(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    shown = _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        dlg.pick_open_btn.click()
        assert len(shown) == 1 and dlg._pick_dlg is shown[0]
        assert shown.kw == [{"full_bleed": True}]                          # 창 전체로 띄운다
        pk = shown[0]
        assert pk._zone == dlg._zone == ("Zones", 1)
        assert [k for k, _l in pk._zones] == dlg.zone_keys()
        assert pk._zones[0][1].startswith(T.zone_label("Zones", dlg._w.zones.get(("Zones", 1)), 1)[0])
        assert {T.key_of(d) for d in pk._items} == {T.key_of(d) for d in dlg._in_zone()}
        d0 = pk._items[0]
        assert pk._caption_of(d0).startswith(dlg._class_name(d0.bin)) and "µm" in pk._caption_of(d0)
        dlg.open_pick_dialog()                                             # 열려 있는 동안 두 번 열리지 않는다
        assert len(shown) == 1
        pk.reject()
        assert dlg._pick_dlg is None and dlg._pick_wanted == set()
    finally:
        dlg.deleteLater()


def test_mode_click_into_auto_opens_pick_once_only_when_nothing_picked(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    shown = _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg.mode_btns["auto"].click()
        assert len(shown) == 1
        shown[0].reject()
        dlg.mode_btns["auto"].click()                                      # 이미 자동 모드 — 다시 열지 않는다
        assert len(shown) == 1
        dlg.mode_btns["manual"].click()
        dlg.toggle_pick(T.key_of(dlg._in_zone()[0]))
        dlg.mode_btns["auto"].click()                                      # 이미 고른 것이 있다
        assert len(shown) == 1
    finally:
        dlg.deleteLater()


def test_pick_applied_sets_picks_and_recommend_and_overkill_stat(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        by_area = sorted(dlg._in_zone(), key=lambda d: d.area)
        a, b = by_area[4], by_area[7]
        over = [by_area[0], by_area[1], by_area[2]]
        assert not dlg.ov_box.isVisibleTo(dlg)
        dlg.open_pick_dialog()
        dlg._pick_dlg.applied.emit({T.key_of(a): a, T.key_of(b): b}, {T.key_of(d): d for d in over})
        _wait_apply(dlg, styled_qapp)
        rec, missed = P.recommend([a, b], dlg._w.zones, dlg._snap)
        assert missed == [] and dlg._ov == rec and set(dlg._picked) == {T.key_of(a), T.key_of(b)}
        assert set(dlg._overkill) == {T.key_of(d) for d in over}
        gone = sum(1 for d in over if not P.is_kept(d, dlg._w.zones, dlg._ov))
        assert gone > 0 and dlg.ov_box.isVisibleTo(dlg) and dlg.ov_label.text() == f"{gone}/3"
        dlg.reset()
        assert dlg._overkill == {} and not dlg.ov_box.isVisibleTo(dlg)
    finally:
        dlg.deleteLater()


def test_pick_preview_matches_direct_computation_and_keeps_state(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        by_area = sorted(dlg._in_zone(), key=lambda d: d.area)
        picked, over = [by_area[5]], [by_area[0], by_area[9]]
        before = (dict(dlg._ov), dict(dlg._picked), dict(dlg._overkill), set(dlg._rec_zones))
        got = dlg._pick_preview(picked, over)
        assert (dict(dlg._ov), dict(dlg._picked), dict(dlg._overkill), set(dlg._rec_zones)) == before   # 상태는 그대로
        ov = P.recommend(picked, dlg._w.zones, dlg._snap)[0]
        not_kept = [d for d in dlg._w.defects if not P.is_kept(d, dlg._w.zones, ov)]
        keys = {T.key_of(d) for d in not_kept}
        assert got == (len(not_kept), sum(T.key_of(d) in keys for d in over), 0) and got[0] > 0
        # 다른 Zone 의 직접 조정은 그대로 센다 + 고른 것이 빠지면 놓침으로 센다
        dlg.set_value(14, R.FEATURE_AREA, 9999.0)
        n_manual = dlg._pick_preview([], [])[0]
        assert n_manual == len([d for d, _w in dlg.dropped()]) == 10
    finally:
        dlg.deleteLater()


def test_pick_pixmap_and_request_use_tile_cache(styled_qapp, tmp_path, monkeypatch):
    from PyQt6.QtGui import QPixmap
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.utils import image_io
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        d = dlg._in_zone()[0]
        photo = dlg._pic(d)
        assert dlg._pick_pixmap(d, 96) is None
        asked = []
        monkeypatch.setattr(dlg, "_want", lambda p, size, kind: asked.append((p, size, kind)))
        dlg._pick_request([d], 96)
        dlg._pick_request([d], 720)
        assert asked == [(photo, 96, "thumb"), (photo, 720, "mid")]
        assert (str(photo), 96, "thumb") in dlg._pick_wanted
        pm = QPixmap(8, 8)
        image_io._get_tile_cache().put(T._tile_key(photo, 96), pm)
        image_io._get_tile_cache().put(T._mid_key(photo, 720), pm)
        assert dlg._pick_pixmap(d, 96) is pm and dlg._pick_pixmap(d, 720) is pm
        dlg._render_tiles()                                                # 다시 그려도 팝업 요청은 취소되지 않는다
        assert (str(photo), 96, "thumb") in dlg._wanted
    finally:
        dlg.deleteLater()


def test_pick_thumb_quality_shares_disk_thumb_size(styled_qapp, tmp_path, monkeypatch):
    """팝업이 미리 불러 두는 '썸네일 화질' 은 디스크 썸네일 크기(THUMB_PX)다 — 같은 캐시 종류('thumb')를 쓰고, 중간 이미지만 'mid'."""
    from aoi_verification.app.ui.widgets import overkill_pick_dialog as O
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        d = dlg._in_zone()[0]
        photo = dlg._pic(d)
        asked = []
        monkeypatch.setattr(dlg, "_want", lambda p, size, kind: asked.append((size, kind)))
        dlg._pick_request([d], O._THUMB)
        dlg._pick_request([d], O._BIG)
        assert asked == [(O._THUMB, "thumb"), (O._BIG, "mid")] and O._THUMB == T.Sizing.THUMB_PX
        assert photo is not None
    finally:
        dlg.deleteLater()


def test_apply_large_set_shows_loading_and_does_not_block(styled_qapp, tmp_path, monkeypatch):
    """많이 고르고 [적용] — 로딩(busy)이 뜨고, 계산은 백그라운드에서 끝나 로딩이 걷힌다(예외 없음, 결과는 직접 계산과 같다)."""
    import dataclasses
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        base = dlg._in_zone()
        many = [dataclasses.replace(base[i % len(base)], index=1000 + i) for i in range(3000)]
        picked = {T.key_of(d): d for d in many[:1500]}
        over = {T.key_of(d): d for d in many[1500:]}
        seen = []
        monkeypatch.setattr(dlg._loading, "set_progress", lambda done, total, msg="": seen.append((done, total, msg)))
        dlg._on_pick_applied(picked, over)
        assert dlg._overlay_on and dlg._loading.isVisibleTo(dlg)           # 계산이 끝나기 전에 로딩이 떠 있다
        assert seen and seen[0][:2] == (0, 0) and seen[0][2] == T._KO.THRESH_PREVIEW_APPLYING   # 양을 모르니 busy
        _wait_apply(dlg, styled_qapp)
        assert len(dlg._picked) == 1500 and len(dlg._overkill) == 1500
        assert dlg._ov == P.recommend(list(picked.values()), dlg._w.zones, dlg._snap)[0]
    finally:
        dlg.deleteLater()


def test_apply_failure_restores_picks_and_shows_notice(styled_qapp, tmp_path, monkeypatch):
    """B5 — 추천값 계산이 실패하면 고르기 전 상태로 되돌리고 알린다(적용된 것처럼 보이지 않게)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        d = dlg._in_zone()[0]
        before = (dict(dlg._picked), dict(dlg._overkill))

        def boom(*a, **k):
            raise RuntimeError("x")
        monkeypatch.setattr(T.P, "recommend", boom)
        dlg._on_pick_applied({T.key_of(d): d}, {})
        _wait_apply(dlg, styled_qapp)
        assert (dlg._picked, dlg._overkill) == before
        assert dlg.notice_label.text() == T._KO.THRESH_PREVIEW_APPLY_FAIL
    finally:
        dlg.deleteLater()


def test_rich_failure_clears_progress_text_and_notifies(styled_qapp, tmp_path, monkeypatch):
    """B6 — RichSurface 읽기 실패(done 에 None)면 '내리기 준비 NN%' 글자를 지우고 알린다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        dlg._rich_token = 3
        dlg.rich_label.setText(T._KO.OVERKILL_RICH_PROGRESS.format(p=40))
        dlg._on_rich(3, dlg._w.folder, None)
        assert dlg.rich_label.text() == ""
        assert dlg.notice_label.text() == T._KO.OVERKILL_RICH_FAIL
    finally:
        dlg.deleteLater()


def test_apply_late_result_of_older_apply_is_dropped(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    _capture_run(monkeypatch, T)
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="auto")
    try:
        dlg._pick_token = 5
        dlg._overlay_on = True
        dlg._on_pick_computed(4, {("x", 1, 2): 3.0})            # 옛 세대 — 버린다
        assert dlg._overlay_on and ("x", 1, 2) not in dlg._ov
    finally:
        dlg.deleteLater()


def test_detail_rows_end_with_stretch_so_surplus_height_does_not_move_rows(styled_qapp, tmp_path):
    """띠가 접히고 펼쳐지는 동안의 남는 높이를 끝의 빈 칸이 받는다 — 위쪽 줄이 출렁이지 않는다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path, mode="manual")
    try:
        dlg._set_tab("keep")
        last = dlg.grid.itemAt(dlg.grid.count() - 1)
        assert last.spacerItem() is not None and last.widget() is None
    finally:
        dlg.deleteLater()


_ROUND = """[General]
ZoneName=ROUND
ZoneID=3
[Surface Feature Filter Area]
Classify=29
FeatureType=1
FilterType=5
LowValue=37.96
"""


def test_untouched_value_field_losing_focus_keeps_recipe_value(styled_qapp, tmp_path):
    """값 칸에 커서만 넣었다 빼도(editingFinished) 레시피 값이 화면 자리로 반올림된 값(37.96 → 38.0)으로 박혀
    그 사이 Defect(37.98)가 '새로 빠짐' 이 되던 문제 — 바꾸지 않았으면 레시피 값 그대로여야 한다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    (tmp_path / "Zones").mkdir()
    (tmp_path / "Zones" / "R.ini").write_text(_ROUND)
    zones = R.read_zones(tmp_path)
    defects = (P.Defect(0, "Zones", 3, 29, 29, 37.98, 3.0, 9.0, 0.0, 0.0, 0.0),)
    w = T.WaferData(tmp_path, defects, (), zones, {29: R.ClassInfo("A", False)}, (1.0, 1.0))
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(w)
        spin = dlg.sliders[(29, R.FEATURE_AREA)][1]
        assert spin.text() == "38.0" and dlg.drop_value.text() == "0"
        spin.editingFinished.emit()                                   # 커서만 넣었다 뺌
        assert dlg._ov == {} and dlg.drop_value.text() == "0" and not dlg.reset_btn.isEnabled()
        dlg.set_value(29, R.FEATURE_AREA, 38.5)                       # 실제로 바꾸면 그대로 반영
        assert dlg.drop_value.text() == "1"
    finally:
        dlg.deleteLater()


def _two_zone_wafer(T, folder: Path):
    """Zone 1 · Zone 2 가 각각 Area ≥ 10 기준 하나 — Defect 는 Zone 마다 3개."""
    zones = {("Zones", z): R.Zone(z, f"Z{z}", {29: (R.Filter(R.FEATURE_AREA, 5, 10.0, 10.0),)}) for z in (1, 2)}
    defects = tuple(P.Defect(i, "Zones", 1 + i // 3, 29, 29, 20.0 + 10 * (i % 3), 3.0, 9.0, 0.0, float(i), 0.0)
                    for i in range(6))
    return T.WaferData(folder, defects, (), zones, {29: R.ClassInfo("A", False)}, (1.0, 1.0))


def test_reset_only_touches_the_current_zone(styled_qapp, tmp_path):
    """Zone 하나에서 [레시피 값으로 되돌리기] 를 눌렀더니 모든 Zone 이 되돌아가던 문제 — 지금 Zone 만 되돌린다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(_two_zone_wafer(T, tmp_path))
        z1, z2 = ("Zones", 1), ("Zones", 2)
        dlg._set_zone(z1)
        dlg.set_value(29, R.FEATURE_AREA, 25.0)
        k = next(k for k in dlg._ov)
        d1, d2 = (next(d for d in dlg._w.defects if (d.zone_dir, d.zone) == z) for z in (z1, z2))
        dlg._picked[T.key_of(d1)] = d1
        dlg._picked[T.key_of(d2)] = d2
        dlg._overkill[T.key_of(d2)] = d2
        dlg._set_zone(z2)
        dlg.set_value(29, R.FEATURE_AREA, 35.0)
        assert {k[:2] for k in dlg._ov} == {z1, z2} and dlg.reset_btn.isEnabled()
        dlg.reset()                                                   # Zone 2 에서 되돌림
        assert {k[:2] for k in dlg._ov} == {z1}
        assert set(dlg._picked) == {T.key_of(d1)} and dlg._overkill == {}
        assert not dlg.reset_btn.isEnabled()
        dlg._set_zone(z1)
        assert dlg.reset_btn.isEnabled() and dlg._ov[k] == 25.0       # Zone 1 값은 그대로
        dlg.reset()
        assert dlg._ov == {} and dlg._picked == {}
    finally:
        dlg.deleteLater()


def test_picks_are_saved_on_apply_and_loadable_later(styled_qapp, tmp_path, monkeypatch):
    """[적용] 하면 고른 것이 자동 저장되고, 새 화면에서 [저장한 선택 불러오기] 로 같은 추천값이 돌아온다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.threshold import saved_picks as SP
    _capture_run(monkeypatch, T)
    dlg = T.ThresholdPreviewDialog()
    dlg.set_mode("auto")
    try:
        dlg.set_wafer(_two_zone_wafer(T, tmp_path))
        assert not dlg.saved_btn.isEnabled()
        z1 = ("Zones", 1)
        ds = [d for d in dlg._w.defects if (d.zone_dir, d.zone) == z1]
        dlg.open_pick_dialog()
        dlg._pick_dlg.applied.emit({T.key_of(ds[1]): ds[1]}, {T.key_of(ds[0]): ds[0]})
        _wait_apply(dlg, styled_qapp)
        want_ov, want_picked = dict(dlg._ov), set(dlg._picked)
        assert want_ov and SP.load(tmp_path, 6) == {z1: ({ds[1].index}, {ds[0].index})}
        assert dlg.saved_btn.isEnabled()
    finally:
        dlg.deleteLater()
    dlg2 = T.ThresholdPreviewDialog()
    dlg2.set_mode("auto")
    try:
        dlg2.set_wafer(_two_zone_wafer(T, tmp_path))
        assert dlg2._picked == {} and dlg2.saved_btn.isEnabled()
        dlg2.load_saved_picks()
        assert set(dlg2._picked) == want_picked and dlg2._ov == want_ov
        assert [T.key_of(d) for d in dlg2._overkill.values()] == [T.key_of(ds[0])]
        dlg2.reset()                                                   # 그 Zone 을 되돌리면 저장분도 비운다
        assert SP.load(tmp_path, 6) == {} and not dlg2.saved_btn.isEnabled()
    finally:
        dlg2.deleteLater()


def test_saved_picks_of_other_zones_survive_before_loading(styled_qapp, tmp_path, monkeypatch):
    """[저장한 선택 불러오기] 를 누르기 전에 다른 Zone 을 고르거나 값만 되돌려도 예전 저장분은 남는다(Zone 단위 저장)."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    from aoi_verification.app.threshold import saved_picks as SP
    _capture_run(monkeypatch, T)
    z1, z2 = ("Zones", 1), ("Zones", 2)
    dlg = T.ThresholdPreviewDialog()
    dlg.set_mode("auto")
    try:
        dlg.set_wafer(_two_zone_wafer(T, tmp_path))
        ds1 = [d for d in dlg._w.defects if (d.zone_dir, d.zone) == z1]
        ds2 = [d for d in dlg._w.defects if (d.zone_dir, d.zone) == z2]
        SP.save(tmp_path, 6, {z1: ({ds1[1].index}, {ds1[0].index})})      # 예전에 저장한 Zone 1 선택
        dlg._set_zone(z2)
        dlg.set_value(29, R.FEATURE_AREA, 35.0)                           # 값만 바꾸고
        dlg.reset()                                                       # 되돌림 — 고른 것은 없었다
        assert SP.load(tmp_path, 6) == {z1: ({ds1[1].index}, {ds1[0].index})}
        dlg.open_pick_dialog()
        dlg._pick_dlg.applied.emit({T.key_of(ds2[0]): ds2[0]}, {})        # Zone 2 만 고르고 적용
        _wait_apply(dlg, styled_qapp)
        assert SP.load(tmp_path, 6) == {z1: ({ds1[1].index}, {ds1[0].index}), z2: ({ds2[0].index}, set())}
        dlg.load_saved_picks()                                            # 불러오면 둘 다 돌아온다
        assert set(dlg._picked) == {T.key_of(ds1[1]), T.key_of(ds2[0])}
    finally:
        dlg.deleteLater()


def test_saved_picks_ignored_when_defect_count_differs(tmp_path):
    """다시 스캔해 Defect 수가 달라진 폴더는 옛 index 가 엉뚱한 Defect 를 가리키니 쓰지 않는다."""
    from aoi_verification.app.threshold import saved_picks as SP
    SP.save(tmp_path, 6, {("Zones", 1): ({1}, {0})})
    assert SP.load(tmp_path, 6) == {("Zones", 1): ({1}, {0})} and SP.load(tmp_path, 7) == {}


def test_arrow_keys_move_the_open_photo_in_the_grid(styled_qapp, tmp_path):
    """사진 목록에서 방향키로 현재(펼친) 사진이 그 방향으로 옮겨 간다 — 좌우는 줄을 넘어 이어지고, 위아래는 같은 칸."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg._set_tab("keep")
        dlg._cols = lambda: 4                                              # 한 줄 4장 — 줄이 여러 개
        dlg._render_tiles()
        rows = dlg._tile_rows
        assert len(rows) >= 3 and len(rows[0]) == 4
        QTest.keyClick(dlg, Qt.Key.Key_Right)                              # 아무것도 안 펼쳤으면 첫 사진
        assert dlg._open == rows[0][0] and dlg.tiles[rows[0][0]].is_open
        QTest.keyClick(dlg, Qt.Key.Key_Left)                               # 맨 앞에서는 멈춘다
        assert dlg._open == rows[0][0]
        for _ in range(4):
            QTest.keyClick(dlg, Qt.Key.Key_Right)
        assert dlg._open == rows[1][0] and dlg.tiles[rows[1][0]].is_open and not dlg.tiles[rows[0][0]].is_open
        QTest.keyClick(dlg, Qt.Key.Key_Down)
        assert dlg._open == rows[2][0]
        QTest.keyClick(dlg, Qt.Key.Key_Up)
        QTest.keyClick(dlg, Qt.Key.Key_Up)
        assert dlg._open == rows[0][0] and dlg._band_row is dlg._row_of[rows[0][0]]
        QTest.keyClick(dlg, Qt.Key.Key_Up)                                 # 맨 위에서는 멈춘다
        assert dlg._open == rows[0][0]
    finally:
        dlg.deleteLater()


def test_arrow_keys_stay_with_the_slider_when_it_has_focus(styled_qapp, tmp_path):
    """슬라이더에 포커스가 있으면 방향키는 슬라이더 값을 바꾸고 사진은 옮기지 않는다."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        sl = next(iter(dlg.sliders.values()))[0]
        sl.setFocus()
        QTest.keyClick(sl, Qt.Key.Key_Right)
        assert dlg._open is None
    finally:
        dlg.deleteLater()


def _enlarged_viewer(dlg, T, monkeypatch, styled_qapp, defect):
    """크게보기를 열어(시트는 가짜로 가로챈다) 뷰어를 돌려준다 — Scan 확인 스레드가 끝날 때까지 기다린다."""
    import time
    from PyQt6.QtGui import QPixmap
    src = dlg._w.folder / "fake_mid.png"                      # 사진 파일은 빈 파일이라 뷰어가 쓸 그림을 대신 준다
    QPixmap(400, 300).save(str(src))
    monkeypatch.setattr(T.image_io, "get_mid_path", lambda p, **k: src)
    got = []
    monkeypatch.setattr(T.sheets, "run", lambda dlg_, **kw: got.append(dlg_) or 0)
    dlg._enlarge_tile(T.key_of(defect))
    viewer = got[0]
    t0 = time.perf_counter()
    while viewer._scan_tag.text() == T._KO.VIEWER_SCAN_CHECKING and time.perf_counter() - t0 < 10:
        styled_qapp.processEvents()
        time.sleep(0.01)
    return viewer


def test_enlarged_color_photo_shows_scan_indicator_and_switches_to_scan_crop(styled_qapp, tmp_path, monkeypatch):
    """Color 사진 크게보기 — 그 Defect 의 Scan 이 있으면 '있음' 표시 + [Scan image 보기] 로 Scan Crop 을 같은 자리에서 본다
    (다시 누르면 Color 로 돌아간다).  Scan 이 없으면 '없음' 이고 버튼은 안 나온다."""
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w, target = _scan_wafer(tmp_path)
    colored = next(d for d in P.load_defects(w)[0] if d is not target and (0, d.index) in T.load_wafer(w).photos)
    lst = w / "ScanResultImageList.txt"
    import cv2
    import numpy as np
    cv2.imwrite(str(w / "1.3.t.1.jpeg"), np.full((1000, 2000, 3), 60, np.uint8))
    lst.write_text(lst.read_text() + f"1.3.t.1.jpeg,{colored.x},{colored.y}\n")        # 컬러 있는 Defect 도 Scan 이 덮는다
    far = next(d for d in P.load_defects(w)[0] if d.index not in (target.index, colored.index)
               and (0, d.index) in T.load_wafer(w).photos and (abs(d.x - colored.x) + abs(d.y - colored.y)) > 20000)
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(w))
        dlg._set_zone((colored.zone_dir, colored.zone))
        v = _enlarged_viewer(dlg, T, monkeypatch, styled_qapp, dlg._w.defects[colored.index])
        assert v._scan_tag.text() == T._KO.VIEWER_SCAN_YES and not v._btn_scan.isHidden()
        color_pix = v._pix
        v._btn_scan.click()
        assert v._scan_on and v._pix is not color_pix and v._pix.width() == v._scan_img.width()
        assert v._btn_scan.text() == T._KO.VIEWER_SCAN_BACK
        v._btn_scan.click()
        assert not v._scan_on and v._pix is color_pix and v._btn_scan.text() == T._KO.VIEWER_SCAN_VIEW
        v2 = _enlarged_viewer(dlg, T, monkeypatch, styled_qapp, dlg._w.defects[far.index])
        assert v2._scan_tag.text() == T._KO.VIEWER_SCAN_NONE and v2._btn_scan.isHidden()
    finally:
        dlg.deleteLater()


def test_enlarged_photo_without_scan_list_says_none_at_once(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = T.ThresholdPreviewDialog()
    try:
        dlg.set_wafer(T.load_wafer(_wafer_with_photos(tmp_path)))
        d = dlg._w.defects[0]
        v = _enlarged_viewer(dlg, T, monkeypatch, styled_qapp, d)
        assert v._scan_tag.text() == T._KO.VIEWER_SCAN_NONE and v._btn_scan.isHidden()
    finally:
        dlg.deleteLater()
