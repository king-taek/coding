"""'어느 Zone에서 왔니?' — 읽기(실물 샘플) · 고르기 계산 · 화면(헤드리스).

실물 샘플: T254 FLU-1122N AOI-16 GX57527979 멀티 레시피(BS_1 ~ BS_3D, Height 포함) — 사진은 저장소 정책상 이름만 있다.
값은 그 폴더 파일(Surface.flt · Height.flt · ManReClassify · EquipmentClassifications · Zone ini)에서 읽은 것(관측)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from aoi_verification.app.threshold import browse as B

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "dev" / "좌표 확인" / "멀티레시피(Height)" / "GX57527979(T254 FLU-1122N AOI-16)"


def _item(**kw) -> B.Item:
    base = dict(wafer=0, index=0, kind=B.KIND_SURFACE, recipe="BS_1", zone="Scan Area", first=29,
                first_name="29 · Dark", cur=29, cur_name="29 · Dark", good=False, type_name="Long dark defect",
                caught="검출 단계 Dark", area=10.0, width=2.0, length=5.0, contrast=None, height=None, x=0.0, y=0.0)
    base.update(kw)
    return B.Item(**base)


# ---------------------------------------------------------------- 읽기(실물)
@pytest.fixture(scope="module")
def sample():
    if not (SAMPLE / "Surface.flt").is_file():
        pytest.skip("샘플 폴더 없음")
    return B.load_folder(SAMPLE)


def test_sample_reads_all_defects_with_names(sample):
    assert sample.note == ""
    assert len(sample.items) == 351
    assert Counter(i.first_name for i in sample.items) == {
        "54 · B R Surface Bright": 234, "29 · B R Surface Dark": 106, "51 · B R Height LSL": 11}


def test_sample_type_and_caught_are_separate_axes(sample):
    """분류 이름과 검출 종류는 다른 파일에서 온 다른 축이다 — 따로 고를 수 있어야 한다."""
    assert Counter(i.type_name for i in sample.items) == {
        "Long bright defect": 234, "Long dark defect": 106, "Height LSL violation (CTS)": 11}
    assert Counter(i.caught for i in sample.items) == {"검출 단계 Bright": 234, "검출 단계 Dark": 106, "Height": 11}


def test_sample_recipe_zone_and_height(sample):
    assert Counter(i.recipe for i in sample.items) == {"BS_3": 236, "BS_2": 100, "BS_3D": 11, "BS_1": 4}
    assert Counter(i.zone for i in sample.items) == {"amk_mask_6_170": 319, "Scan Area": 32}
    heights = [i for i in sample.items if i.kind == B.KIND_HEIGHT]
    assert len(heights) == 11 and all(i.height is not None and i.area is None for i in heights)
    surface = [i for i in sample.items if i.kind == B.KIND_SURFACE]
    assert all(i.area is not None and i.area > 0 for i in surface)        # 픽셀 크기를 읽어 µm² 로 바꿨다


def test_sample_slot_and_scan(sample):
    assert sample.slot == "1"
    assert sample.has_scan is True


def test_missing_folder_is_note_not_raise(tmp_path):
    w = B.load_folder(tmp_path)
    assert w.items == () and w.note


# ---------------------------------------------------------------- 고르기 계산
def test_facet_counts_other_facets_narrow_but_own_does_not():
    items = [_item(index=0, first_name="A", recipe="R1"), _item(index=1, first_name="A", recipe="R2"),
             _item(index=2, first_name="B", recipe="R1")]
    passed, counts = B.facet_counts(items, {"first_name": {"A"}}, B.Flags())
    assert [i.index for i in passed] == [0, 1]
    assert counts["first_name"] == {"A": 2, "B": 1}         # 자기 칸 고름은 빼고 센다
    assert counts["recipe"] == {"R1": 1, "R2": 1}            # 다른 칸은 고른 것만큼 준다


def test_facet_counts_two_misses_counted_nowhere():
    items = [_item(first_name="B", recipe="R2")]
    passed, counts = B.facet_counts(items, {"first_name": {"A"}, "recipe": {"R1"}}, B.Flags())
    assert passed == [] and not counts["first_name"] and not counts["recipe"]


def test_flags_hide_good_and_filter():
    good = _item(index=0, good=True)
    small = _item(index=1, area=1.0)
    changed = _item(index=2, cur=0)
    assert not B.Flags().ok(good) and B.Flags(good=True).ok(good)
    assert not B.Flags(min_area=5).ok(small)
    assert B.Flags(changed=True).ok(changed) and not B.Flags(changed=True).ok(small)
    assert not B.Flags(photo=True).ok(small)
    assert not B.Flags(min_length=1).ok(_item(length=None))
    assert not B.Flags(min_width=3).ok(_item()) and B.Flags(min_width=2).ok(_item())       # width 2.0
    assert not B.Flags(min_contrast=1).ok(_item(contrast=None))
    assert B.Flags(min_contrast=5).ok(_item(contrast=5.0)) and not B.Flags(min_contrast=6).ok(_item(contrast=5.0))


def test_sort_items_none_last_both_ways():
    items = [_item(index=0, contrast=None), _item(index=1, contrast=5.0), _item(index=2, contrast=9.0)]
    assert [i.index for i in B.sort_items(items, "contrast", True)] == [2, 1, 0]
    assert [i.index for i in B.sort_items(items, "contrast", False)] == [1, 2, 0]


# ---------------------------------------------------------------- 화면
@pytest.fixture
def dlg(styled_qapp, sample):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    d = ZoneOriginDialog()
    d.resize(1400, 900)
    d.set_wafers([SAMPLE], {SAMPLE: [sample]})
    yield d
    d.close()
    d.deleteLater()


def test_dialog_starts_with_all_and_first_open(dlg):
    assert len(dlg._passed) == 351
    assert dlg._open is not None
    assert dlg.current() is dlg._passed[0]
    assert ("first_name", "54 · B R Surface Bright") in dlg.facet_rows


def test_dialog_toggle_facet_narrows_and_chip(dlg):
    dlg.toggle("recipe", "BS_3D")
    assert len(dlg._passed) == 11 and all(i.kind == B.KIND_HEIGHT for i in dlg._passed)
    assert dlg.current().kind == B.KIND_HEIGHT           # 값 칸은 늘 보이는 Defect 하나
    assert dlg.chips_host.isVisibleTo(dlg)
    row = dlg.facet_rows[("first_name", "51 · B R Height LSL")]
    assert row.count == 11
    dlg.clear_all()
    assert len(dlg._passed) == 351 and not dlg.chips_host.isVisibleTo(dlg)


def test_dialog_facet_order_is_stable_when_pressed(dlg):
    before = list(dlg._order["first_name"])
    dlg.toggle("first_name", "29 · B R Surface Dark")
    assert dlg._order["first_name"] == before


def test_dialog_sort_cycles_and_table(dlg):
    dlg.set_sort("area")
    assert dlg._sort == ("area", True)
    areas = [i.area for i in dlg._passed if i.area is not None]
    assert areas == sorted(areas, reverse=True)
    dlg.set_sort("area")
    dlg.set_sort("area")
    assert dlg._sort is None
    dlg.set_view("table")
    assert dlg.table.rowCount() == 351
    dlg.table.selectRow(3)
    assert dlg._open == dlg._tile_order[3]


def test_dialog_paging_more(dlg):
    from aoi_verification.app.ui.widgets import zone_origin_dialog as Z
    assert len(dlg._tiles) == Z.PAGE_TILES
    dlg.more()
    assert len(dlg._tiles) == 2 * Z.PAGE_TILES


def test_dialog_arrows_move_open(dlg):
    first = dlg._open
    assert dlg.move_current(1, 0)
    assert dlg._open != first and dlg._open == dlg._tile_order[1]


def test_dialog_remove_and_undo(dlg):
    dlg.remove_folder(SAMPLE)
    assert dlg._passed == [] and dlg.notice.isVisibleTo(dlg)
    dlg._undo()
    assert len(dlg._passed) == 351


def test_dialog_excel_needs_photos(dlg):
    picked, _numbers = dlg.excel_picks()
    assert picked == {}                                   # 저장소 샘플은 사진 실물이 없다 — 저장할 것 없음
    dlg.save_excel()
    assert dlg.notice_label.text()


def test_dialog_cap_and_conder_alert(styled_qapp, tmp_path):
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    d = ZoneOriginDialog()
    try:
        w = B.WaferItems(tmp_path / "W1", (_item(),), cap=600, slot="3")
        d._no_flt[tmp_path / "LOT"] = [(tmp_path / "LOT" / "C1", True)]
        d.set_wafers([tmp_path / "LOT"], {tmp_path / "LOT": [w]})
        text = d.alert_label.text()
        assert "600" in text and "C1" in text
        assert d.wafer_label(0) == "#3 · W1"
    finally:
        d.close()
        d.deleteLater()


def test_dialog_has_no_removed_filters(dlg):
    """현택님 요청(2026-11-24): '잡힌 곳' · '그 밖에' 칸과 사진 위 분류 표시는 없앴다."""
    from PyQt6.QtWidgets import QLabel
    assert "caught" not in dict(B.FACETS)
    assert not any(k == "caught" for k, _v in dlg.facet_rows)
    texts = [lab.text() for lab in dlg.findChildren(QLabel)]
    assert not any(t.startswith("→ ") for t in texts)
    heads = [lab.text() for lab in dlg.side_scroll.widget().findChildren(QLabel)]
    assert not any(t.startswith(("그 밖에", "잡힌 곳")) for t in heads)
    assert "Bin" in heads and "Recipe" in heads           # '분류 이름 · 첫 분류' · '레시피' 를 바꾼 이름
    assert not any(t in ("검토로 분류가 바뀐 것만", "사진 있는 것만", "EDC Good 도 보기") for t in texts)


def test_detail_shows_first_and_re_class_lines(dlg):
    """값 칸 분류는 '첫 분류: …' · '재 분류: …' 두 줄 — '검토로 바뀜' · '같음' 문구 없이 이름만(현택님 예시)."""
    from PyQt6.QtWidgets import QLabel
    it = dlg.current()
    texts = [lab.text() for lab in dlg.kv_host.findChildren(QLabel)]
    i, j = texts.index("첫 분류"), texts.index("재 분류")
    assert texts[i + 1] == it.first_name.split(" · ", 1)[-1] and " · " not in texts[i + 1]
    assert texts[j + 1] == it.cur_name.split(" · ", 1)[-1]
    assert not any(t in ("검토로 바뀜", "같음", "지금 분류", "분류 이름") or t.startswith("검토로 바뀜") for t in texts)


def test_left_bar_has_re_class_facet(dlg):
    """왼쪽 바에서 재 분류(검토 뒤 분류)도 고른다 — Bin 바로 아래."""
    assert [k for k, _t in B.FACETS][:2] == ["first_name", "cur_name"]
    v = dlg._order["cur_name"][0]
    dlg.toggle("cur_name", v)
    assert dlg._passed and all(i.cur_name == v for i in dlg._passed)


def test_params_fold_and_four_limits(dlg):
    """Area · Width · Length · Contrast 하한은 [파라미터 열기] 안에 접혀 있다가 편다 — 입력칸은 낮게(24px)."""
    assert dlg.min_edits == {} and dlg.param_btn.text().endswith("파라미터 열기")
    dlg.toggle_params()
    assert list(dlg.min_edits) == ["min_area", "min_width", "min_length", "min_contrast"]
    assert all(ed.height() == 24 for ed in dlg.min_edits.values())
    dlg.set_flag("min_contrast", 1e9)
    assert dlg._passed == [] and dlg.chips_host.isVisibleTo(dlg)
    dlg.toggle_params()
    assert dlg.min_edits == {} and dlg._flags.min_contrast == 1e9          # 접어도 값은 그대로(칩으로 보인다)


def test_caught_not_shown_anywhere(dlg):
    """'잡힌 곳'(기준 묶음 · 검출 단계 …)은 값 칸 · 표 · 사진 설명 어디에도 안 나온다(현택님 요청 2026-11-24)."""
    from PyQt6.QtWidgets import QLabel
    from aoi_verification.app.ui.widgets import zone_origin_dialog as Z
    assert "caught" not in [k for k, _n, _d in Z.TABLE_COLS]
    kv = [lab.text() for lab in dlg.kv_host.findChildren(QLabel)]
    assert "잡힌 곳" not in kv and not any(t.startswith(("검출 단계", "기준 묶음")) for t in kv)
    assert all("검출 단계" not in t.toolTip() and "기준 묶음" not in t.toolTip() for t in dlg._tiles.values())


# ---------------------------------------------------------------- 좌표 · LOT (현택님 요청 2026-11-24)
def _defects_csv(folder: Path) -> list:
    import csv
    with open(folder / "Defects.csv", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def test_col_row_match_equipment_defects_csv(sample):
    """골든(관측): 장비 Export ``Defects.csv`` 의 Col · Row 와 Surface · Height 351/351 이 정확히 같다(오차 0).
    Coplanarity 1건은 이 화면이 읽지 않는 결과라 뺀다."""
    want = Counter((int(r["Col"]), int(r["Row"])) for r in _defects_csv(SAMPLE)
                   if not r["Type"].startswith("Coplanarity"))
    got = Counter((i.col, i.row) for i in sample.items if not i.good)
    assert sum(want.values()) == 351 and got == want
    assert sample.number_warn == ""                  # 번호표([ExportData])로 정한 번호 — 경고 없음


def test_col_row_uses_display_shift(tmp_path):
    """합성 Recipe.ini(골든 아님, R4): 실물 FS UBM Defects.csv 14장은 화면 col 이 실제 die 보다 1 작다 —
    잘라 둔 샘플엔 Recipe.ini 가 없어 시프트 −1 · 방향 2 를 적어 넣으면 col · row 가 14/14 같아야 한다."""
    import shutil
    src = ROOT / "dev" / "Threshold 확인" / "T254 FS UBM" / "27125535EWA5"
    if not (src / "Surface.flt").is_file():
        pytest.skip("샘플 없음")
    want = Counter((int(r["Col"]), int(r["Row"])) for r in _defects_csv(src))
    plain = tmp_path / "plain" / src.name
    shutil.copytree(src, plain)
    assert Counter((i.col, i.row) for i in B.load_folder(plain).items) != want    # 시프트 없으면 어긋난다
    dst = tmp_path / "shift" / src.name                    # display_shift 는 폴더마다 캐시 — 새 폴더로
    shutil.copytree(src, dst)
    (dst / "Recipe.ini").write_text("[Display]\nDisplayColShift=-1\nDisplayRowShift=0\nWaferDisplayOrientation=2\n")
    got = Counter((i.col, i.row) for i in B.load_folder(dst).items)
    assert got == want and min(c for c, _r in got) == -1      # 맨 왼쪽 열은 화면 col −1


def test_no_geometry_leaves_col_row_empty():
    """die 격자를 못 구하면 번호를 비운다 — 절대좌표 폴백 번호를 지어내지 않는다."""
    src = ROOT / "dev" / "Threshold 확인" / "T254 BS CUP BS_2" / "GX57010487"
    if not (src / "Surface.flt").is_file():
        pytest.skip("샘플 없음")
    w = B.load_folder(src)
    assert w.items and all(i.col is None and i.row is None for i in w.items)


def test_lot_and_wafer_ids(sample):
    assert (sample.lot, sample.wafer_id, sample.slot) == ("FLU-1122N", "GX57527979", "1")
    assert {i.lot for i in sample.items} == {"FLU-1122N"}


def test_flags_col_row():
    it = _item(col=-1, row=5)
    assert B.Flags(col=-1).ok(it) and B.Flags(col=-1, row=5).ok(it)
    assert not B.Flags(col=0).ok(it) and not B.Flags(row=4).ok(it)
    assert not B.Flags(col=1).ok(_item())                  # 번호 모름 → 좌표를 넣으면 빠진다


def test_dialog_coord_filter_and_detail(dlg):
    it = dlg._passed[0]
    dlg.set_flag("col", it.col)
    dlg.set_flag("row", it.row)
    assert dlg._passed and all((i.col, i.row) == (it.col, it.row) for i in dlg._passed)
    assert dlg.coord_edits["col"].text() == str(it.col) and dlg.coord_warn is None
    from PyQt6.QtWidgets import QLabel
    texts = [lab.text() for lab in dlg.kv_host.findChildren(QLabel)]
    for key, val in (("LOT", "FLU-1122N"), ("Wafer", "GX57527979"), ("슬롯#", "1"),
                     ("col, row", f"{dlg.current().col}, {dlg.current().row}")):
        assert texts[texts.index(key) + 1] == val


def test_dialog_lot_groups_wafers(dlg):
    """LOT 한 줄 + ▸/▾ 로 그 LOT 웨이퍼를 펴고 접는다(LOT 이 하나면 처음부터 펼침)."""
    assert ("lot", "FLU-1122N") in dlg.facet_rows and dlg.lot_open("FLU-1122N")
    assert any(k == "wafer" for k, _v in dlg.facet_rows)
    dlg.toggle_lot("FLU-1122N")
    assert not dlg.lot_open("FLU-1122N") and not any(k == "wafer" for k, _v in dlg.facet_rows)
    dlg.toggle("lot", "FLU-1122N")
    assert len(dlg._passed) == 351 and dlg.chips_host.isVisibleTo(dlg)
