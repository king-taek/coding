"""Overkill 튜닝 — LOT 폴더 안에서 웨이퍼(슬롯)를 골라 합치기.

LOT 을 더하면 전부 합치고, 사용자가 웨이퍼별 Defect 수를 보고 몇 장만 남긴다(사용자 결정 2026-11-19 시안 A)."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

ROOT = Path(__file__).resolve().parents[2]
FS_UBM = ROOT / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"
COUNTS = {"27125535EWA1": 14, "27125535EWA2": 6, "27125535EWA3": 10}      # 슬롯 1·2·3 의 Defect 수(앞에서 자른다)


def _lot(tmp_path: Path, slots: bool = True) -> Path:
    """실물 폴더 사본 3장짜리 LOT.  사진은 저장소에 두지 않는다(빈 파일)."""
    lot = tmp_path / "lot"
    lot.mkdir()
    for n in COUNTS:
        d = lot / n
        shutil.copytree(FS_UBM, d)
        for name in (FS_UBM / "사진목록.txt").read_text(encoding="utf-8").splitlines():
            if name and not name.startswith("#"):
                (d / name).write_bytes(b"")
        if slots:
            (d / "WaferInfo.ini").write_text(f"[AutoCycleInfo]\nActiveSlot={n[-1]}\n", encoding="utf-8")
    return lot


@pytest.fixture
def dlg(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    monkeypatch.setattr(T._Load, "start", lambda self: (_ for _ in ()).throw(AssertionError("읽기 스레드 금지")))
    lot = _lot(tmp_path)
    d = T.ThresholdPreviewDialog()
    d._expand[lot] = T.expand_root(lot)
    for f, _r in d._expand[lot]:
        w = T.load_wafer(f)
        d._cache[f] = replace(w, defects=w.defects[:COUNTS[f.name]])
    d.set_folders([lot])
    d.lot = lot
    d.T = T
    yield d
    d.deleteLater()


def _w(dlg, name):
    return next(f for f, _r in dlg._expand[dlg.lot] if f.name == name)


def _row(dlg):
    return next(w for w in (dlg._folder_lay.itemAt(i).widget() for i in range(dlg._folder_lay.count()))
                if isinstance(w, dlg.T._FolderRow))


def _list(dlg):
    return next((w for w in (dlg._folder_lay.itemAt(i).widget() for i in range(dlg._folder_lay.count()))
                 if isinstance(w, dlg.T._WaferList)), None)


def _total(dlg):
    return len(dlg._w.defects) if dlg._w is not None and not dlg._w.note else 0


def test_default_merges_every_wafer_and_row_text_is_unchanged(dlg):
    assert _total(dlg) == 30
    assert _row(dlg).side.text() == "30" and "웨이퍼 3" in _row(dlg).name._full
    assert dlg._off == set() and _list(dlg) is None            # 목록은 접혀 있다
    assert hasattr(_row(dlg), "disc")


def test_toggle_wafer_changes_merged_count_and_lot_text(dlg):
    dlg.toggle_wafer(_w(dlg, "27125535EWA3"))
    assert _row(dlg).side.text() == "2/3장 · 20"                # 줄은 바로 바뀐다(합치기는 모아서)
    dlg.flush_wafer_choice()
    assert _total(dlg) == 20 and len(dlg._w.folders) == 2
    dlg.toggle_wafer(_w(dlg, "27125535EWA3"))                  # 다시 넣으면 원래 문구로
    dlg.flush_wafer_choice()
    assert _total(dlg) == 30 and _row(dlg).side.text() == "30"


def test_rapid_toggles_merge_once(dlg, monkeypatch):
    calls = []
    orig = dlg._remerge
    monkeypatch.setattr(dlg, "_remerge", lambda: (calls.append(1), orig())[1])
    for n in ("27125535EWA1", "27125535EWA2", "27125535EWA3"):
        dlg.toggle_wafer(_w(dlg, n))
    dlg.flush_wafer_choice()
    assert len(calls) == 1


def test_all_and_none(dlg):
    dlg.set_lot_wafers(dlg.lot, False)
    dlg.flush_wafer_choice()
    assert _total(dlg) == 0 and dlg._w.note and dlg.T._KO.OVERKILL_LOT_NONE_PICKED == dlg._w.note
    assert _row(dlg).side.text() == "0/3장 · 0"
    dlg.set_lot_wafers(dlg.lot, True)
    dlg.flush_wafer_choice()
    assert _total(dlg) == 30 and not dlg._off and _row(dlg).side.text() == "30"


def test_expanded_list_rows_names_and_order(dlg):
    dlg.toggle_lot_open(dlg.lot)
    lst = _list(dlg)
    assert [r.name._full for r in lst.rows] == ["#1 · 27125535EWA1", "#2 · 27125535EWA2", "#3 · 27125535EWA3"]
    assert [r.count.text() for r in lst.rows] == ["14", "6", "10"]
    dlg.toggle_lot_sort(dlg.lot)                                # 많은 순
    assert [r.count.text() for r in _list(dlg).rows] == ["14", "10", "6"]
    assert _list(dlg).ctl["top"].isChecked()
    dlg.toggle_lot_sort(dlg.lot)                                # 다시 누르면 폴더 순서
    assert [r.count.text() for r in _list(dlg).rows] == ["14", "6", "10"]


def test_row_click_toggles_and_excluded_row_is_dimmed(dlg):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    dlg.toggle_lot_open(dlg.lot)
    row = _list(dlg).rows[1]
    assert row.property("excluded") is False and row.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)            # 줄 어디를 눌러도
    assert _w(dlg, "27125535EWA2") in dlg._off
    assert _list(dlg).rows[1].property("excluded") is True
    dlg.flush_wafer_choice()
    assert _total(dlg) == 24


def test_slot_unknown_shows_folder_name_only(styled_qapp, tmp_path, monkeypatch):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    lot = _lot(tmp_path, slots=False)
    d = T.ThresholdPreviewDialog()
    try:
        d._expand[lot] = T.expand_root(lot)
        for f, _r in d._expand[lot]:
            d._cache[f] = T.load_wafer(f)
        d.set_folders([lot])
        d.toggle_lot_open(lot)
        names = [r.name._full for r in next(w for w in (d._folder_lay.itemAt(i).widget() for i in range(d._folder_lay.count()))
                                           if isinstance(w, T._WaferList)).rows]
        assert names == list(COUNTS)
    finally:
        d.deleteLater()


def test_single_wafer_folder_has_no_disclosure(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets import threshold_preview_dialog as T
    lot = _lot(tmp_path)
    one = lot / "27125535EWA1"
    d = T.ThresholdPreviewDialog()
    try:
        d._expand[one] = T.expand_root(one)
        d._cache[one] = T.load_wafer(one)
        d.set_folders([one])
        row = next(w for w in (d._folder_lay.itemAt(i).widget() for i in range(d._folder_lay.count()))
                   if isinstance(w, T._FolderRow))
        assert not hasattr(row, "disc")
    finally:
        d.deleteLater()


def test_picked_defects_of_excluded_wafer_are_dropped(dlg):
    """뺀 웨이퍼의 꼭 잡기는 버리고 자동 추천은 남은 고름으로 다시 계산 — 화면에 없는 Defect 로 추천하지 않는다."""
    dlg.set_mode("auto")
    w = dlg._w
    on_1 = next(d for d in w.defects if w.folders[d.wafer].name == "27125535EWA1")
    on_2 = next(d for d in w.defects if w.folders[d.wafer].name == "27125535EWA2")
    dlg.toggle_pick(dlg.T.key_of(on_1))
    dlg.toggle_pick(dlg.T.key_of(on_2))
    dlg.toggle_wafer(_w(dlg, "27125535EWA2"))
    dlg.flush_wafer_choice()
    assert len(dlg._picked) == 1
    kept = next(iter(dlg._picked.values()))
    assert dlg._w.folders[kept.wafer].name == "27125535EWA1"
    assert dlg.notice.isVisibleTo(dlg) and "1" in dlg.notice_label.text()
    ov_one = dict(dlg._ov)
    dlg.reset()                                                 # 그 한 개만 골랐을 때의 추천과 같다
    dlg.toggle_pick(dlg.T.key_of(next(d for d in dlg._w.defects if dlg._w.folders[d.wafer].name == "27125535EWA1"
                                      and d.index == kept.index)))
    assert dlg._ov == ov_one


def test_removing_lot_clears_selection(dlg):
    dlg.toggle_lot_open(dlg.lot)
    dlg.toggle_lot_sort(dlg.lot)
    dlg.toggle_wafer(_w(dlg, "27125535EWA1"))
    dlg.flush_wafer_choice()
    assert dlg._off and dlg._open_lots
    dlg.remove_folder(dlg.lot)
    assert not dlg._off and not dlg._open_lots and not dlg._by_count
    dlg.set_folders([dlg.lot])                                  # 다시 더하면 전부 넣은 채
    assert _total(dlg) == 30 and _row(dlg).side.text() == "30"
