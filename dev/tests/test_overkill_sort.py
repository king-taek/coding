"""Overkill 튜닝 사진 칸 정렬 줄 — 묶음 안에서만 · 값 없음은 맨 뒤 · 다시 누르면 방향 · 탭마다 따로."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from aoi_verification.app.threshold import rules as R

pytest.importorskip("PyQt6.QtWidgets")

FS_UBM = Path(__file__).resolve().parents[2] / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"


def _shown_dlg(T, app, tmp_path):
    """실물 폴더 사본 + 사진목록.txt 이름의 빈 사진(test_threshold_preview_dialog 와 같은 준비)."""
    w = tmp_path / FS_UBM.name
    shutil.copytree(FS_UBM, w)
    for name in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines():
        if name and not name.startswith("#"):
            (w / name).write_bytes(b"")
    dlg = T.ThresholdPreviewDialog()
    dlg.resize(1280, 800)
    dlg.show()
    dlg.set_wafer(T.load_wafer(w))
    app.processEvents()
    return dlg


def test_sort_in_groups_keeps_group_order_and_puts_missing_last():
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import sort_in_groups
    items = [("B", 2), ("A", 5), ("B", None), ("A", 1), ("B", 9), ("A", 5)]
    g, v = (lambda t: t[0]), (lambda t: t[1])
    assert sort_in_groups(items, g, v, True) == [("B", 9), ("B", 2), ("B", None), ("A", 5), ("A", 5), ("A", 1)]
    assert sort_in_groups(items, g, v, False) == [("B", 2), ("B", 9), ("B", None), ("A", 1), ("A", 5), ("A", 5)]


def test_sort_value_units_and_unmeasured_contrast():
    from aoi_verification.app.threshold import preview as P
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    w = T.WaferData(folder=None, px=(2.0, 2.0))
    d = P.Defect(index=0, zone_dir="Zones", zone=1, bin=14, cls=14, area=10.0, width=3.0, length=5.0,
                 contrast=0.0, x=0.0, y=0.0)
    assert T.sort_value(w, d, "area") == pytest.approx(40.0)          # px² → µm²
    assert T.sort_value(w, d, "width") == pytest.approx(6.0)
    assert T.sort_value(w, d, "length") == pytest.approx(10.0)
    assert T.sort_value(w, d, "contrast") is None                      # 0 = 안 잼


def _areas(dlg):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    return [T.sort_value(dlg._w, d, "area") for d in dlg._tile_defects.values()]


def test_click_sorts_toggles_direction_and_shows_value(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    dlg = _shown_dlg(T, styled_qapp, tmp_path)
    try:
        dlg.set_value(14, R.FEATURE_AREA, 40.0)                       # 새로 빠짐 7장
        base = list(dlg.tiles)
        assert dlg.sort_bar.isVisibleTo(dlg) and dlg.sort_btns["base"].isChecked()
        assert not any(t.cap.isVisibleTo(t) for t in dlg.tiles.values())   # 기본 = 값 띠 없음
        dlg.sort_btns["area"].click()
        a = _areas(dlg)
        assert a == sorted(a, reverse=True) and dlg.sort_btns["area"].text() == "Area ▼"
        t0 = next(iter(dlg.tiles.values()))
        assert t0.cap.isVisibleTo(t0) and t0.cap.text() == f"{a[0]:,.1f} µm²"
        dlg.sort_btns["area"].click()                                  # 다시 누르면 낮은 순
        a = _areas(dlg)
        assert a == sorted(a) and dlg.sort_btns["area"].text() == "Area ▲"
        dlg.sort_btns["width"].click()                                 # 다른 글자는 높은 순부터
        assert dlg.sort_btns["width"].text() == "Width ▼" and dlg.sort_btns["area"].text() == "Area"
        dlg._set_tab("keep")                                           # 탭마다 따로 — 남음은 기본
        assert dlg.sort_btns["base"].isChecked()
        dlg._set_tab("drop")
        assert dlg.sort_btns["width"].isChecked()
        dlg.sort_btns["base"].click()                                  # 기본 = 원래(기준에 가까운) 순서
        assert list(dlg.tiles) == base
    finally:
        dlg.deleteLater()
