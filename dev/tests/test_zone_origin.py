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


def test_arrow_keys_after_clicking_a_photo(dlg):
    """사진을 누른 뒤 화살표 — 키보드가 스크롤 칸으로 가 화살표를 스크롤이 먹어 사진이 안 옮겨졌다(현택님 2026-11-25)."""
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    dlg.show()
    dlg.activateWindow()
    QApplication.processEvents()
    dlg._relayout_timer.stop()                 # 보인 뒤 폭이 정해진 열 수로 한 번 맞추고 시작한다(열 수가 바뀌면 아래 기대 칸이 달라진다)
    dlg._relayout_now()
    QApplication.processEvents()
    k = dlg._tile_order[5]
    QTest.mouseClick(dlg._tiles[k], Qt.MouseButton.LeftButton, pos=QPoint(30, 30))
    QApplication.processEvents()
    assert dlg._open == k
    cols = len(dlg._tile_rows[0])
    for key, want in ((Qt.Key.Key_Right, 6), (Qt.Key.Key_Down, 6 + cols), (Qt.Key.Key_Left, 5 + cols),
                      (Qt.Key.Key_Up, 5)):
        QTest.keyClick(QApplication.focusWidget() or dlg, key)
        QApplication.processEvents()
        assert dlg._open == dlg._tile_order[want], key


def test_arrow_past_last_photo_shows_more(dlg):
    """보이는 사진 끝에서 → — [더 보기] 처럼 다음 묶음을 붙이고 그 첫 사진으로 간다."""
    from aoi_verification.app.ui.widgets import zone_origin_dialog as Z
    dlg.open_item(dlg._tile_order[-1])
    assert dlg.move_current(1, 0)
    assert len(dlg._tiles) == 2 * Z.PAGE_TILES
    assert dlg._open == dlg._tile_order[Z.PAGE_TILES]


def test_arrows_drop_passed_detail_photos(dlg):
    """화살표로 지나간 Defect 의 큰 사진은 원하는 목록에서 빠진다(만들러 가지 않는다) — 멈춘 Defect 것만 남는다."""
    for _ in range(5):
        dlg.move_current(1, 0)
    mids = {w for w in dlg._wanted if w[2] == "mid"}
    it = dlg.current()
    assert mids <= {(str(it.photo), 136, "mid")}


@pytest.fixture
def dlg_photos(dlg, tmp_path):
    """샘플은 사진 실물이 없어(이름만) 사진 경로가 비어 있다 — Defect 마다 가짜 사진 경로를 붙여 다시 그린다."""
    from dataclasses import replace
    for f, w in list(dlg._data.items()):
        dlg._data[f] = replace(w, items=tuple(replace(it, photo=tmp_path / f"{it.kind}_{it.index}.c.jpeg")
                                             for it in w.items))
    dlg._open = None
    dlg._render()
    return dlg


def test_thumb_queue_takes_visible_photo_first(dlg, monkeypatch):
    """일꾼은 줄 맨 앞이 아니라 화면에 보이는 칸의 사진부터 꺼낸다(스크롤 · [더 보기] 뒤에도 보이는 것부터)."""
    done = []
    monkeypatch.setattr(dlg, "_thumb_job", lambda path, size, kind: done.append(str(path)))
    with dlg._q_lock:
        dlg._queue[:] = [("a", 118, "thumb"), ("b", 118, "thumb"), ("c", 118, "thumb")]
    dlg._hot = frozenset({"c"})
    dlg._thumb_next()
    dlg._hot = frozenset()
    dlg._thumb_next()
    assert done == ["c", "a"]


def test_visible_tiles_are_hot_after_scroll(dlg_photos):
    from PyQt6.QtWidgets import QApplication
    dlg = dlg_photos
    dlg.show()
    QApplication.processEvents()
    dlg._update_hot()
    first = {str(dlg._tiles[k].wait_path) for k in dlg._tile_order[:3]}
    last = str(dlg._tiles[dlg._tile_order[-1]].wait_path)
    assert first <= dlg._hot and last not in dlg._hot
    dlg.grid_scroll.verticalScrollBar().setValue(dlg.grid_scroll.verticalScrollBar().maximum())
    assert dlg._hot_timer.interval() == 40              # 스크롤 위치가 기다림(ms)으로 들어가지 않는다
    dlg._update_hot()
    assert last in dlg._hot


def test_detail_photo_not_queued_behind_list(dlg_photos, monkeypatch, tmp_path):
    """오른쪽 큰 사진은 따로 1명이 받는다 — 목록 사진이 다 끝나기 전에 뜬다(실측: 줄 서면 5.7초, 따로면 0.7초)."""
    import time
    from PIL import Image
    from PyQt6.QtWidgets import QApplication
    from aoi_verification.app.utils import image_io
    jpg = tmp_path / "t.jpg"
    Image.new("RGB", (40, 30), (90, 90, 90)).save(jpg)
    list_done = []

    def slow_thumb(_p):
        time.sleep(0.05)
        list_done.append(1)
        return jpg
    monkeypatch.setattr(image_io, "get_map_thumb_path", slow_thumb)
    monkeypatch.setattr(image_io, "get_mid_path", lambda _p: jpg)
    image_io.clear_tile_cache()
    dlg = dlg_photos
    dlg._render()                                      # 목록 60칸 + 큰 사진을 다시 원한다
    end = time.monotonic() + 5
    while time.monotonic() < end:
        QApplication.processEvents()
        pm = dlg.color_pic.pixmap()
        if pm is not None and not pm.isNull():
            break
        time.sleep(0.01)
    assert dlg.color_pic.pixmap() is not None and not dlg.color_pic.pixmap().isNull()
    assert len(list_done) < 30                          # 60칸이 다 끝나기 훨씬 전에 큰 사진이 왔다


def test_detail_shows_small_photo_first(dlg_photos):
    """큰 사진이 아직이면 목록의 작은 사진을 바로 보인다(흐렸다가 선명해진다)."""
    from PyQt6.QtGui import QPixmap
    from aoi_verification.app.ui.widgets.threshold_preview_dialog import _tile_key
    from aoi_verification.app.ui.widgets import zone_origin_dialog as Z
    from aoi_verification.app.utils import image_io
    image_io.clear_tile_cache()
    dlg = dlg_photos
    it = dlg.current()
    pm = QPixmap(Z.TILE_PX, Z.TILE_PX)
    pm.fill()
    image_io._get_tile_cache().put(_tile_key(it.photo, Z.TILE_PX), pm)
    dlg._render_detail()
    assert dlg.color_pic.pixmap() is not None and not dlg.color_pic.pixmap().isNull()


def test_list_time_goes_to_log(dlg, caplog):
    """목록 사진 한 묶음이 다 뜨면 app.log 에 걸린 시간 한 줄(고친 뒤 실제로 빨라졌는지 보는 용도)."""
    import logging
    dlg._batch = None
    dlg._batch_add("p1")
    dlg._batch_add("p2")
    with caplog.at_level(logging.INFO, logger="aoi.zone_origin"):
        dlg._batch_done("p1")
        assert not [r for r in caplog.records if "목록 사진" in r.getMessage()]
        dlg._batch_done("p2")
    assert [r for r in caplog.records if "목록 사진 2장" in r.getMessage()]


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
    """값 칸 분류는 '첫 분류: …' · '재분류: …' 두 줄 — '검토로 바뀜' · '같음' 문구 없이 이름만(현택님 예시)."""
    from PyQt6.QtWidgets import QLabel
    it = dlg.current()
    texts = [lab.text() for lab in dlg.kv_host.findChildren(QLabel)]
    i, j = texts.index("첫 분류"), texts.index("재분류")
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
                     ("col / row", f"{dlg.current().col} / {dlg.current().row}")):
        assert texts[texts.index(key) + 1] == val


def test_dialog_lot_groups_wafers(dlg):
    """LOT 한 줄 + ▸/▾ 로 그 LOT 웨이퍼를 펴고 접는다(처음엔 접힘)."""
    assert ("lot", "FLU-1122N") in dlg.facet_rows and not dlg.lot_open("FLU-1122N")
    assert not any(k == "wafer" for k, _v in dlg.facet_rows)
    dlg.toggle_lot("FLU-1122N")
    assert dlg.lot_open("FLU-1122N") and any(k == "wafer" for k, _v in dlg.facet_rows)
    dlg.toggle_lot("FLU-1122N")
    assert not dlg.lot_open("FLU-1122N") and not any(k == "wafer" for k, _v in dlg.facet_rows)
    dlg.toggle("lot", "FLU-1122N")
    assert len(dlg._passed) == 351 and dlg.chips_host.isVisibleTo(dlg)


def test_typing_coord_then_moving_focus_does_not_crash(dlg):
    """좌표를 넣고 Enter → 다른 칸으로 옮기면 프로그램이 꺼졌다(신호를 낸 입력칸을 그리기가 지움, 2026-11-24).
    적용은 한 박자 뒤 · 옛 칸은 나중에 지운다.  커서는 쓰던 칸으로 돌아온다."""
    import time
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    def settle(until=None):
        # 바쁜 기계에서는 포커스 이동 · 한 박자 뒤 적용이 늦게 온다 — 조건이 맞을 때까지 최대 3초 기다린다.
        end = time.monotonic() + (3.0 if until else 0.05)
        while True:
            QApplication.processEvents()
            if (until() if until else False) or time.monotonic() > end:
                return
            time.sleep(0.01)
    dlg.show()
    dlg.activateWindow()                         # 포커스 이동(editingFinished)은 활성 창에서만 온다
    settle(dlg.isActiveWindow)
    ed = dlg.coord_edits["col"]
    ed.setFocus()
    QTest.keyClicks(ed, "4")
    QTest.keyClick(ed, Qt.Key.Key_Return)
    settle(lambda: dlg._flags.col == 4)
    assert dlg._flags.col == 4
    ed = dlg.coord_edits["row"]
    ed.setFocus()
    QTest.keyClicks(ed, "3")
    dlg.coord_edits["col"].setFocus()          # 포커스 이동 → Row 의 editingFinished
    settle(lambda: dlg._flags.row == 3)
    assert (dlg._flags.col, dlg._flags.row) == (4, 3)
    assert all((i.col, i.row) == (4, 3) for i in dlg._passed)
    for key in ("min_area", "min_width"):        # 파라미터 칸도 같은 길
        if not dlg._params_open:
            dlg.toggle_params()
            settle()
        e = dlg.min_edits[key]
        e.setFocus()
        QTest.keyClicks(e, "1")
        dlg.coord_edits["row"].setFocus()
        settle(lambda key=key: getattr(dlg._flags, key) == 1.0)
    assert dlg._flags.min_area == 1.0 and dlg._flags.min_width == 1.0


# ---------------------------------------------------------------- 전체 점검에서 나온 것 (2026-11-24)
@pytest.fixture
def dlg2(styled_qapp, sample, tmp_path):
    """LOT 둘(웨이퍼 하나씩) — 같은 샘플을 다른 폴더 · LOT 이름으로 한 번 더 넣는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from dataclasses import replace
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    other = tmp_path / "LOTB" / "W2"
    w2 = replace(sample, folder=other, lot="LOT-B", wafer_id="W2", slot="7",
                 items=tuple(replace(i, lot="LOT-B") for i in sample.items))
    d = ZoneOriginDialog()
    d.resize(1400, 900)
    d.set_wafers([SAMPLE, other.parent], {SAMPLE: [sample], other.parent: [w2]})
    yield d
    d.close()
    d.deleteLater()


def test_wafer_from_other_lot_replaces_lot_pick(dlg2):
    """LOT A 를 고른 채 LOT B 의 웨이퍼를 누르면 0 개가 됐다 — 웨이퍼를 고르면 다른 LOT 고름은 풀린다."""
    dlg2.toggle("lot", "FLU-1122N")
    wb = next(n for n in dlg2._order["wafer"] if dlg2.wafer_of(n).lot == "LOT-B")
    dlg2.toggle("wafer", wb)
    assert dlg2._sel["lot"] == set() and len(dlg2._passed) == 351
    dlg2.toggle("lot", "FLU-1122N")                    # 다시 LOT A → B 웨이퍼 고름이 풀린다
    assert dlg2._sel["wafer"] == set() and len(dlg2._passed) == 351


def test_more_appends_same_order_as_full_render(dlg2):
    """[더 보기] 는 이어 붙이기만 한다 — 결과(순서 · 줄 나눔)는 처음부터 그린 것과 같아야 한다."""
    from aoi_verification.app.ui.widgets import zone_origin_dialog as Z
    for _ in range(4):
        dlg2.more()
    inc_order, inc_rows = list(dlg2._tile_order), [list(r) for r in dlg2._tile_rows]
    assert len(inc_order) == min(5 * Z.PAGE_TILES, len(dlg2._passed)) == len(dlg2._tiles)
    dlg2._render()
    assert dlg2._tile_order == inc_order and dlg2._tile_rows == inc_rows
    assert [item_k for item_k in inc_order] == [Z.item_key(i) for i in dlg2._passed[:len(inc_order)]]


def test_default_order_follows_slot_not_load_order(dlg2):
    """기본 순서의 웨이퍼 차례는 읽은 순이 아니라 슬롯 순(왼쪽 목록과 같다)."""
    first_group = dlg2._passed[0].first_name
    group = [i for i in dlg2._passed if i.first_name == first_group]
    slots = [dlg2.wafer_of(i.wafer).slot for i in group]
    assert slots == sorted(slots, key=int)


def test_broken_xy_does_not_fail_whole_wafer(monkeypatch, sample):
    """flt 에 NaN 좌표가 한 건 있어도 그 Defect 번호만 비고 웨이퍼는 읽힌다."""
    import math
    orig = B.P.load_all

    def broken(folder):
        defects, goods, note = orig(folder)
        from dataclasses import replace
        return [replace(defects[0], x=math.nan)] + list(defects[1:]), list(goods), note
    monkeypatch.setattr(B.P, "load_all", broken)
    w = B.load_folder(SAMPLE)
    assert w.note == "" and len(w.items) == len(sample.items)
    assert sum(1 for i in w.items if i.col is None) == 1


def test_unfinished_text_survives_side_redraw(dlg):
    """쓰다 만 좌표('-' · Enter 전 숫자)는 왼쪽 칸을 다시 그려도 남는다 — [모두 지우기] 는 지운다."""
    dlg.coord_edits["col"].setText("-")
    dlg.coord_edits["row"].setText("7")
    dlg.toggle_params()                             # 왼쪽 칸 다시 그리기
    assert dlg.coord_edits["col"].text() == "-"
    assert dlg.coord_edits["row"].text() == "7"
    assert dlg._flags.row is None                   # 아직 적용 안 함
    dlg.clear_all()
    assert dlg.coord_edits["col"].text() == ""
    assert dlg.coord_edits["row"].text() == ""


def test_scan_find_runs_once_per_defect(dlg, monkeypatch):
    """같은 Defect 의 Scan 찾기는 찾는 중이면 다시 띄우지 않는다(목록 오가기 · 크게보기)."""
    import aoi_verification.app.ui.widgets.zone_origin_dialog as Z
    started = []
    monkeypatch.setattr(Z._ScanFind, "start", lambda self: started.append(self))
    k = ("k",)
    a = dlg._scan_thread(k, Path("."), (0.0, 0.0))
    b = dlg._scan_thread(k, Path("."), (0.0, 0.0))
    assert a is b and len(started) == 1
    dlg._on_scan(k, None)                           # 끝나면 다음엔 새로
    dlg._scan_thread(k, Path("."), (0.0, 0.0))
    assert len(started) == 2
    dlg._on_scan(k, None)


def test_resize_relayouts_only_when_columns_change(dlg, monkeypatch):
    """창 폭이 열 수를 바꿀 때만 타일을 다시 그린다 — 첫 묶음이 한 줄을 못 채워도 크기 바꿀 때마다 다시 그리지 않는다."""
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QResizeEvent
    from PyQt6.QtCore import QSize
    vp = dlg.grid_scroll.viewport()
    ev = QResizeEvent(QSize(800, 600), QSize(790, 600))
    monkeypatch.setattr(dlg, "_cols", lambda: dlg._last_cols)
    dlg._relayout_timer.stop()
    dlg.eventFilter(vp, ev)
    assert not dlg._relayout_timer.isActive()
    monkeypatch.setattr(dlg, "_cols", lambda: dlg._last_cols + 1)
    dlg.eventFilter(vp, ev)
    assert dlg._relayout_timer.isActive()
    dlg._relayout_timer.stop()
    assert ev.type() == QEvent.Type.Resize


def test_table_view_redraw_is_fast_when_visible(dlg):
    """보이는 표를 다시 그릴 때 칸마다 폭을 다시 재면 수백 줄에 몇십 초 걸렸다(2천 장에서 몇 분 멈춤, 2026-11-24)."""
    import time
    from PyQt6.QtWidgets import QApplication, QHeaderView
    dlg.show()
    dlg.set_view("table")
    QApplication.processEvents()
    t = time.monotonic()
    dlg.set_sort("area")                            # 보이는 표를 다시 그린다
    QApplication.processEvents()
    assert time.monotonic() - t < 5.0
    assert dlg.table.rowCount() == min(len(dlg._passed), 2000) > 100
    hh = dlg.table.horizontalHeader()
    assert hh.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents


def test_excel_labels_unique_when_parent_and_name_repeat(styled_qapp, tmp_path):
    """부모 · 이름이 같은 폴더 셋(다른 보관 위치)도 엑셀 slot 이 하나로 섞이지 않는다."""
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    d = ZoneOriginDialog()
    try:
        roots = [tmp_path / a / "LOT" / "W1" for a in ("A", "B", "C")]
        data = {r: [B.WaferItems(r, (_item(wafer=0, photo=r / "p.jpg"),), slot=str(i + 1))]
                for i, r in enumerate(roots)}
        d.set_wafers(roots, data)
        picked, numbers = d.excel_picks()
        assert len(picked) == 3 and all(len(v) == 1 for v in picked.values())
        assert sorted(numbers.values()) == ["1", "2", "3"]
    finally:
        d.close()
        d.deleteLater()


# ---------------------------------------------------------------- 2차 점검 (2026-11-24)
def test_nan_sizes_become_empty(monkeypatch, sample):
    """flt 크기 · Contrast 가 NaN 이면 빈 값 — NaN 은 하한을 통과하고 정렬 순서를 흔들었다."""
    import math
    orig = B.P.load_all

    def broken(folder):
        defects, goods, note = orig(folder)
        from dataclasses import replace
        return [replace(defects[0], area=math.nan, contrast=math.nan)] + list(defects[1:]), list(goods), note
    monkeypatch.setattr(B.P, "load_all", broken)
    w = B.load_folder(SAMPLE)
    first = next(i for i in w.items if i.kind == B.KIND_SURFACE and i.index == 0)
    assert first.area is None and first.contrast is None
    assert not B.Flags(min_contrast=1.0).ok(first)


def test_limit_text_keeps_two_decimals():
    """하한 칸 글자는 넣은 값 그대로 — 포커스만 옮겨도 12345.25 가 12345.2 로 바뀌었다."""
    from aoi_verification.app.ui.widgets.zone_origin_dialog import _num_text
    assert [_num_text(v) for v in (12345.25, 3.0, 0.5, 10.1)] == ["12345.25", "3", "0.5", "10.1"]


def test_undo_reloads_half_read_folder(dlg, monkeypatch):
    """읽는 중에 뺐다 [되돌리기] 하면 못 읽은 웨이퍼를 마저 읽는다(반만 보이고 멈추지 않게)."""
    root = dlg._roots[0]
    dlg.remove_folder(root)
    gone = dlg._expand[root][0]
    dlg._data.pop(gone)
    started = []
    monkeypatch.setattr(dlg, "_start_load", lambda: started.append(1))
    dlg._undo()
    assert started == [1] and root in dlg._roots


def test_excel_names_do_not_follow_screen_sort(dlg2):
    """엑셀 웨이퍼 이름은 화면 정렬과 상관없이 같다."""
    before = dlg2.excel_picks()
    dlg2.set_sort("area")
    after = dlg2.excel_picks()
    assert list(before[0]) == list(after[0]) and before[1] == after[1]
    assert {k: sorted(map(str, v)) for k, v in before[0].items()} == {k: sorted(map(str, v)) for k, v in after[0].items()}


def test_scan_waits_until_arrow_stops(dlg, monkeypatch):
    """화살표로 지나가는 Defect 는 Scan 을 찾지 않는다 — 멈춘 Defect 하나만."""
    import time
    from dataclasses import replace
    from PyQt6.QtWidgets import QApplication
    import aoi_verification.app.ui.widgets.zone_origin_dialog as Z
    for f, w in list(dlg._data.items()):
        dlg._data[f] = replace(w, has_scan=True)
    started = []
    monkeypatch.setattr(Z._ScanFind, "start", lambda self: started.append(self._item[0]))
    dlg._scans.clear()
    for _ in range(5):
        dlg.move_current(1, 0)
    assert started == []
    end = time.monotonic() + 2
    while not started and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)
    assert started == [Z.item_key(dlg.current())]
    dlg._on_scan(started[0], None)


def test_open_viewer_gets_scan_result(dlg):
    """크게보기를 연 뒤 끝난 Scan 찾기도 뷰어에 넘어간다(이미 끝난 스레드의 신호를 놓치지 않게)."""
    got = []

    class _V:
        def set_scan_crop(self, crop):
            got.append(crop)
    k = ("k",)
    dlg._scan_viewer = (k, _V())
    dlg._on_scan(k, None)
    dlg._scan_viewer = None
    assert got == [None]


def test_arrows_still_move_after_clicking_sort(dlg):
    """정렬 버튼을 누른 뒤에도 화살표가 사진을 옮긴다."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    dlg.show()
    btn = next(iter(dlg.sort_btns.values()))
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    first = dlg.current()
    QTest.keyClick(dlg.focusWidget() or dlg, Qt.Key.Key_Right)
    assert dlg.current() is not first


def test_closing_dialog_stops_running_excel_save(styled_qapp):
    """B3 — 창을 닫으면 도는 엑셀 저장을 멈추고(stop) 끝 안내가 닫힌 창에 뜨지 않게 신호를 끊는다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QObject, pyqtSignal
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog

    class _Sig(QObject):
        progress = pyqtSignal(int, int, str)
        done = pyqtSignal(str)
        failed = pyqtSignal(str)

    class _Fake:
        def __init__(self):
            self.signals, self.stopped = _Sig(), False

        def isRunning(self):
            return True

        def stop(self):
            self.stopped = True

    d = ZoneOriginDialog()
    try:
        fake, got = _Fake(), []
        fake.signals.done.connect(got.append)
        d._exporter = fake
        d.done(0)
        assert fake.stopped
        fake.signals.done.emit("x.xlsx")
        assert got == []
    finally:
        d.deleteLater()


def test_notice_and_alert_bars_are_rounded_inner_bars(dlg):
    """알림·경고 줄은 Overkill 처럼 둥글고 창 가장자리에서 떨어진 안쪽 줄(E2a)."""
    for bar in (dlg.notice, dlg.alert):
        sheet = bar.styleSheet()
        assert "border-radius: 6px" in sheet and "margin: 4px 12px 0 12px" in sheet, sheet


def test_unknown_slot_reads_dash_not_question_mark(styled_qapp, tmp_path):
    """슬롯 번호를 모르면 '#?' 가 아니라 엑셀과 같은 '—'(SM-E2f)."""
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    d = ZoneOriginDialog()
    try:
        w = B.WaferItems(tmp_path / "W1", (_item(),), slot="")
        d.set_wafers([tmp_path / "LOT"], {tmp_path / "LOT": [w]})
        assert d.wafer_label(0) == "— · W1"
        assert d.wafer_label(99) == "—"
        assert "?" not in d.wafer_label(0)
    finally:
        d.close()
        d.deleteLater()


# ---------------------------------------------------------------- 폴더 많을 때 폭 · 왼쪽 줄 ✕ (2026-11-25)
def _many(d, sample, n=25):
    from dataclasses import replace
    roots, data = [], {}
    for i in range(n):
        f = Path(f"/tmp/L{i}/GX{i}")
        data[f] = [replace(sample, folder=f, wafer_id=f.name, slot=str(i + 1))]
        roots.append(f)
    d.set_wafers(roots, data)
    return roots


@pytest.fixture
def dlg25(styled_qapp, sample):
    from aoi_verification.app.ui.widgets.zone_origin_dialog import ZoneOriginDialog
    d = ZoneOriginDialog()
    d.resize(1400, 900)
    _many(d, sample)
    d.show()
    yield d
    d.close()
    d.deleteLater()


def test_many_folders_do_not_widen_dialog(dlg25):
    assert dlg25.minimumSizeHint().width() <= 1200
    assert dlg25.loaded_label.isVisibleTo(dlg25) and "불러온 폴더 25" in dlg25.loaded_label.text()
    assert not hasattr(dlg25, "_chip_lay")                  # 폴더별 칩은 없다


def test_many_selected_wafers_wrap_chips(dlg25):
    dlg25.toggle_lot("FLU-1122N")
    for i in range(12):
        dlg25.toggle("wafer", i)
    assert dlg25.minimumSizeHint().width() <= 1200
    from PyQt6.QtWidgets import QApplication
    for _ in range(3):                                     # 새 칩이 보이게 된 뒤에야 크기가 잡힌다
        QApplication.processEvents()
    assert dlg25._sel_lay.heightForWidth(600) > dlg25._sel_lay.heightForWidth(2400)


def test_lot_group_first_and_collapsed(dlg):
    from PyQt6.QtWidgets import QLabel
    heads = [lab.text() for lab in dlg.side_scroll.widget().findChildren(QLabel)
             if lab.property("role") == "ovGroupHead"]
    assert heads[0] == dict(B.FACETS)["lot"]
    assert not dlg.lot_open("FLU-1122N")


def test_wafer_root_x_only_for_roots_and_does_not_select(dlg25):
    dlg25.toggle_lot("FLU-1122N")
    row = dlg25.facet_rows[("wafer", 2)]
    assert row.x_btn is not None and not row.x_btn.isVisibleTo(row)
    row.set_hover(True)
    assert row.x_btn.isVisibleTo(row)
    row.x_btn.click()
    assert Path("/tmp/L2/GX2") not in dlg25._roots and len(dlg25._roots) == 24
    assert dlg25._sel["wafer"] == set()


def test_wafer_from_lot_folder_has_no_x(dlg):
    """LOT 폴더 안 웨이퍼(고른 폴더 != 웨이퍼 폴더)는 ✕ 가 없다."""
    root = Path("/tmp/LOTDIR")
    w = dlg.wafer_of(0)
    dlg._roots[0] = root
    dlg._expand[root] = [w.folder]
    assert dlg.wafer_root(0) is None
    dlg.toggle_lot("FLU-1122N")
    assert dlg.facet_rows[("wafer", 0)].x_btn is None


def test_lot_x_removes_its_roots_and_one_undo_restores(dlg25):
    lot = "FLU-1122N"                                      # 25 폴더가 모두 같은 LOT(샘플 복제)
    before = list(dlg25._roots)
    row = dlg25.facet_rows[("lot", lot)]
    row.set_hover(True)
    row.x_btn.click()
    assert dlg25._roots == [] and dlg25.notice.isVisibleTo(dlg25)
    dlg25._undo()
    assert dlg25._roots == before

