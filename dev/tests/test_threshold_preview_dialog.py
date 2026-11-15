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
    assert sorted(w.photos) == [d.index for d in w.defects]
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
