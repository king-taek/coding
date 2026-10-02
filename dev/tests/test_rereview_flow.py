"""AVAGO 재리뷰 — 설정 화면 · 선별 화면 이름 · 결과 화면 · 엑셀을 헤드리스로.

좌표·맵은 ``test_rereview`` 의 합성 폴더(실물 맵 + 합성 Camtek)를 그대로 쓴다."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")
openpyxl = pytest.importorskip("openpyxl")

from aoi_verification.app import i18n                                  # noqa: E402
from aoi_verification.app.coords import rereview as rr                 # noqa: E402
from aoi_verification.app.models.result import (REREVIEW_MODE,         # noqa: E402
                                                VERDICT_GOOD, VERDICT_REJECT,
                                                rereview_result)

from .test_rereview import WAFER, _clear_caches, _map_dir, _wafer_folder  # noqa: E402,F401


def _real_jpegs(folder: Path) -> None:
    """엑셀 임베드용 — 합성 폴더의 빈 사진을 작은 실제 JPEG 로 바꾼다."""
    from PIL import Image
    for p in folder.glob("*.jpeg"):
        Image.new("RGB", (40, 30), (120, 120, 120)).save(p, "JPEG")


@pytest.fixture
def lot(tmp_path):
    defects = [("on_007", 27, 9), ("a1", 30, 30), ("a2", 30, 30), ("b", 31, 30),
               ("g", 40, 40)]
    folder = _wafer_folder(tmp_path / "NHW", WAFER, defects)
    _real_jpegs(folder)
    return tmp_path, folder, _map_dir(tmp_path)


# ── 설정 화면 ────────────────────────────────────────────────────────────────
def test_setup_rereview_mode_uses_two_folders(styled_qapp, tmp_path, monkeypatch):
    """왼쪽 = Map 폴더, 오른쪽 = Scanresult + LOT명(S/M) — 사용자 지정 배치."""
    from aoi_verification.app.ui.pages import setup_page as sp

    scan, maps = tmp_path / "scan", tmp_path / "maps"
    scan.mkdir()
    maps.mkdir()
    warned: list = []
    monkeypatch.setattr(sp.sheets, "warn", lambda *a, **k: warned.append(a))
    page = sp.SetupPage()
    try:
        page.ref_path_edit.setText("매칭 기준")
        page.val_path_edit.setText("매칭 검증")
        page.val_machine_edit.setText("3")
        page.set_rereview_mode(True)
        assert page.is_rereview_mode()
        assert page._title_label.text() == i18n.KO.REREVIEW_TITLE
        assert page.start_btn.text() == i18n.KO.BTN_REREVIEW_START
        assert page.ref_group.property("_headLabel").text() == i18n.KO.REREVIEW_MAP_GROUP
        assert page.val_group.property("_headLabel").text() == i18n.KO.REREVIEW_SCAN_GROUP
        assert (page.val_group.property("_machineLabel").text()
                == i18n.KO.REREVIEW_LOT_LABEL)
        assert page.ref_machine_edit.isHidden() and not page.val_machine_edit.isHidden()
        assert page.val_machine_edit.text() == ""          # LOT 은 매번 새로 적는다
        assert all(c.isHidden() for c in page._setting_cards)
        assert page.extract_btn.isHidden()
        page.ref_path_edit.setText(str(maps))
        page.val_path_edit.setText(str(scan))
        assert page._validate() is True
        assert page._collect_input() is None and warned     # LOT명 필수
        page.val_machine_edit.setText("PH3Q42.00")
        inp = page._collect_input()
        assert inp.rereview is True and not inp.extract
        assert (inp.ref_root, inp.val_root, inp.ref_machine) == (scan, maps, "PH3Q42.00")
        # 되돌리면 매칭 입력이 그대로 돌아온다(모드별로 따로 둔다).
        page.set_rereview_mode(False)
        assert page.ref_path_edit.text() == "매칭 기준"
        assert page.val_path_edit.text() == "매칭 검증"
        assert page.val_machine_edit.text() == "3"
        assert page.val_group.property("_machineLabel").text() == i18n.KO.SETUP_MACHINE_LABEL
        assert not page._setting_cards[1].isHidden()
        assert not page.extract_btn.isHidden()
    finally:
        page.deleteLater()


def test_file_name_uses_lot_name():
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    inp = SetupInput(mode="single", ref_root=Path("s"), val_root=Path("m"),
                     ref_machine="PH3Q42/00", val_machine="", threshold=0.7, rereview=True)
    assert mw.MainWindow._suggest_result_name(inp) == "PH3Q42_00 AVAGO 재리뷰.xlsx"


def test_lot_name_is_filled_from_map_path_but_stays_editable(styled_qapp):
    """Map 경로의 '288. PH3Q42.00 (FSX)' → LOT명(S/M).  사용자가 고친 값은 덮지 않는다."""
    from aoi_verification.app.ui.pages import setup_page as sp

    base = r"\\k5cifsn2\k5tsvdata$\1. Conder Scan\540. AVAGO TECH\14. 15966PA0-BW2"
    page = sp.SetupPage()
    try:
        page.set_rereview_mode(True)
        page.ref_path_edit.setText(base + r"\288. PH3Q42.00 (FSX)\2. FVI\1. OR")
        assert page.val_machine_edit.text() == "PH3Q42.00 (FSX)"
        # 자동값 그대로면 경로를 바꿀 때 따라 바뀐다.
        page.ref_path_edit.setText(base + r"\290. PH8Q66.00 (NHW)\2. FVI\1. OR")
        assert page.val_machine_edit.text() == "PH8Q66.00 (NHW)"
        # 고쳐 쓴 값은 그대로 둔다.
        page.val_machine_edit.setText("PH8Q66.00-수정")
        page.ref_path_edit.setText(base + r"\288. PH3Q42.00 (FSX)\2. FVI\1. OR")
        assert page.val_machine_edit.text() == "PH8Q66.00-수정"
    finally:
        page.deleteLater()


def test_scanresult_slots_can_be_picked(styled_qapp, tmp_path, monkeypatch):
    """Scanresult 카드의 [슬롯 선택] — 매칭과 같은 선택 창, 고른 슬롯만 진행."""
    from aoi_verification.app.ui.pages import setup_page as sp
    from aoi_verification.app.ui.widgets import slot_select_dialog as ssd

    scan, maps = tmp_path / "NHW", tmp_path / "maps"
    for w in ("PH3Q42-01A5", "PH3Q42-03G6", "PH3Q42-04G1"):
        (scan / w).mkdir(parents=True)
    maps.mkdir()

    class _Dlg:
        def __init__(self, names, **kw):
            self.accepted_ok, self.selected = True, {names[0], names[2]}

    monkeypatch.setattr(ssd, "SlotSelectDialog", _Dlg)
    monkeypatch.setattr(sp.sheets, "run", lambda dlg, **k: True)
    page = sp.SetupPage()
    try:
        assert page._rr_slot_btn.isHidden()            # 매칭 모드에는 없다
        page.set_rereview_mode(True)
        assert not page._rr_slot_btn.isHidden()
        page.ref_path_edit.setText(str(maps))
        page.val_path_edit.setText(str(scan))
        page.val_machine_edit.setText("PH3Q42.00 (FSX)")
        page._rr_slot_btn.click()
        assert page._rr_slot_btn.text() == i18n.KO.EXTRACT_SLOT_BTN_FMT.format(n=2, total=3)
        inp = page._collect_input()
        assert inp.selected_slots == {"PH3Q42-01A5", "PH3Q42-04G1"}
        page.val_path_edit.setText(str(scan) + "x")     # 폴더가 바뀌면 전체로
        assert page._rr_slots is None
    finally:
        page.deleteLater()


def test_rereview_button_sits_right_of_extract(styled_qapp):
    """[AVAGO 재리뷰] 는 [Defect 추출] 바로 오른쪽(사용자 지정) — 보조 묶음 안."""
    from aoi_verification.app.ui.pages import setup_page as sp
    page = sp.SetupPage()
    try:
        aux = page._aux_row.buttons()
        assert aux.index(page.rereview_btn) == aux.index(page.extract_btn) + 1
        bar = page._action_bar
        assert bar.itemAt(bar.count() - 1).widget() is page.start_btn
    finally:
        page.deleteLater()


@pytest.mark.parametrize("width", [800, 1024, 1280])
def test_action_bar_fits_the_window(styled_qapp, width):
    """보조 버튼 5개가 한 줄이면 967px — 좁은 창에서는 두 줄로 접혀 창 안에 든다."""
    from PyQt6.QtWidgets import QApplication
    from aoi_verification.app.ui.pages import setup_page as sp
    page = sp.SetupPage()
    try:
        page.resize(width, 700)
        page.show()
        for _ in range(10):
            QApplication.processEvents()
        assert page.width() == width
        assert page.start_btn.geometry().right() <= page._action_bar.parentWidget().width()
        if width == 800:
            assert page._aux_row._rows == 2          # 한 줄(967px)은 들어가지 않는다
        elif width == 1280:
            assert page._aux_row._rows == 1          # 넓으면 예전처럼 한 줄
    finally:
        page.close()


# ── 선별 화면 ────────────────────────────────────────────────────────────────
def test_select_page_relabels_and_keeps_direction(styled_qapp):
    """→ Reject(오른쪽 패널) / ← Good — 동작(verify/exclude)은 그대로, 이름만 바뀐다."""
    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages.select_page import SelectPage

    page = SelectPage()
    try:
        items = [ImageItem(slot="W", path=Path(f"/x/{i}.jpeg"), side="ref")
                 for i in range(3)]
        page.set_rereview(True, die_of={items[0].path: (12, 34)})
        page.load_state(queue=items)
        page.show()
        assert page.right_panel._title_label.text() == i18n.KO.REREVIEW_PANEL_RIGHT
        assert i18n.KO.REREVIEW_BTN_REJECT in page.btn_verify.text()
        assert "die (12, 34)" in page.slot_label.text()
        page._decide("verify")                      # → = Reject
        page._decide("exclude")                     # ← = Good
        st = page.get_state()
        assert [it.path for it in st.targets["W"]] == [items[0].path]
        assert [it.path for it in st.excluded["W"]] == [items[1].path]
        page._undo()
        assert not st.excluded["W"]
        page.set_rereview(False)
        assert page.right_panel._title_label.text() == i18n.KO.PANEL_RIGHT_TARGETS
    finally:
        page.deleteLater()


# ── 결과 화면 · 엑셀 ─────────────────────────────────────────────────────────
def _result(lot):
    _root, folder, maps = lot
    imgs = sorted(folder.glob("*.jpeg"))
    plan = rr.plan_wafer(WAFER, folder, imgs, maps)
    rejects = {WAFER: [folder / "a1.jpeg", folder / "a2.jpeg", folder / "b.jpeg"]}
    goods = {WAFER: [folder / "g.jpeg"]}
    return rereview_result("8", {WAFER: plan}, rejects, goods)


def test_result_rows_reject_first(lot):
    res = _result(lot)
    assert res.mode == REREVIEW_MODE
    assert [(u.path.stem, u.note) for u in res.unmatched_refs] == [
        ("a1", VERDICT_REJECT), ("a2", VERDICT_REJECT), ("b", VERDICT_REJECT),
        ("g", VERDICT_GOOD)]


def test_result_page_shows_only_new_reject_dies(styled_qapp, lot):
    """결과 화면은 **추가된 Reject die 만** — 웨이퍼별 나열 없이 die 목록(사용자 결정)."""
    from PyQt6.QtWidgets import QLabel

    from aoi_verification.app.coords import camtek_ini
    from aoi_verification.app.ui.pages.result_page import ResultPage

    _root, folder, _maps = lot
    page = ResultPage()
    try:
        page.show_result(_result(lot))
        labels = page.findChildren(QLabel)
        hero = [w.text() for w in labels if w.property("role") == "rrHeroValue"]
        assert hero == ["2"]                                     # a1·a2 같은 die + b
        a1 = camtek_ini.resolve(folder / "a1.jpeg")
        b = camtek_ini.resolve(folder / "b.jpeg")
        cells = [w.text() for w in labels if w.property("role") == "rrCell"]
        fmt = i18n.KO.REREVIEW_DIE_CELL_FMT
        assert sorted(zip(cells[::2], cells[1::2])) == sorted([
            (fmt.format(col=a1.col, row=a1.row), "2"),
            (fmt.format(col=b.col, row=b.row), "1")])
        names = [w.text() for w in labels if w.property("role") == "rrCellName"]
        assert names == [WAFER, WAFER]                          # die 마다 한 줄
        assert page.title.text() == i18n.KO.REREVIEW_RESULT_TITLE
        assert page.wafer_map_btn.isHidden() and page.review_unmatched_btn.isHidden()
        assert not page.include_good_chk.isHidden()
    finally:
        page.deleteLater()


def test_select_page_center_crop(styled_qapp, tmp_path, monkeypatch):
    """선별 '가운데 확대' — 원본 사진의 가운데 50%·30% 만 크게(결함은 정중앙)."""
    from PIL import Image

    from aoi_verification.app.models.slot import ImageItem
    from aoi_verification.app.ui.pages import select_page as sp
    from aoi_verification.app.utils import prefs

    saved: dict = {}
    monkeypatch.setattr(prefs, "patch", lambda **kw: saved.update(kw))
    img = tmp_path / "d.jpg"
    Image.new("RGB", (1380, 1036), (100, 100, 100)).save(img, "JPEG")
    page = sp.SelectPage()
    try:
        page.load_state(queue=[ImageItem(slot="W", path=img, side="ref")])
        for key, w, h in (("30", 414, 311), ("50", 690, 518)):
            page.crop_group.set_current_key(key)
            page._on_crop_changed(key)
            pix = page.center_img._pix_orig
            assert (pix.width(), pix.height()) == (w, h)        # 원본에서 잘랐다
            assert saved["select_center_crop"] == key
        page._on_crop_changed("100")
        assert page.center_img.center_crop() == 1.0
    finally:
        page.deleteLater()


def _export(lot, tmp_path, include_good: bool):
    from aoi_verification.app.ui.widgets.wafer_map_view import render_reject_map_png
    from aoi_verification.app.workers.exporter import ExcelExporter

    dst = tmp_path / f"out{int(include_good)}" / "재리뷰.xlsx"
    ex = ExcelExporter(_result(lot), dst,
                       template_path=Path("dev/양식.xlsx"),
                       reject_map_renderer=render_reject_map_png,
                       include_good=include_good)
    errors: list = []
    ex.signals.failed.connect(errors.append)
    ex.run()                                  # 같은 스레드에서 — 결과만 본다
    assert not errors, errors
    return openpyxl.load_workbook(dst)


def _column(ws, header: str, rows: range) -> list:
    head = [c.value for c in ws[1]]
    v = head.index(header)
    return [ws.cell(row=r, column=v + 1).value for r in rows]


def test_excel_reject_only_by_default(styled_qapp, lot, tmp_path):
    """시트 순서(사용자 지정): Reject → Wafer Map.  Good 제외면 '전체' 없음, 요약 시트 없음."""
    K = i18n.KO
    wb = _export(lot, tmp_path, include_good=False)
    assert wb.sheetnames == [K.REREVIEW_SHEET_REJECT, K.WAFER_MAP_SHEET]
    rej = wb[K.REREVIEW_SHEET_REJECT]
    assert _column(rej, K.REREVIEW_VERDICT_HEADER, range(3, 6)) == ["Reject"] * 3
    assert rej.cell(row=6, column=1).value is None          # Good 행은 없다
    assert rej["C2"].value == "8"                            # LOT명 그대로(AOI-8 아님)
    dies = _column(rej, K.REREVIEW_DIE_HEADER, range(3, 6))
    assert dies[0] == dies[1] != dies[2]                     # a1·a2 는 같은 die
    # 기존 Map · 수정된 Map 두 장.
    assert len(wb[K.WAFER_MAP_SHEET]._images) == 2


def test_excel_with_good_adds_all_sheet(styled_qapp, lot, tmp_path):
    K = i18n.KO
    wb = _export(lot, tmp_path, include_good=True)
    assert wb.sheetnames == [K.REREVIEW_SHEET_REJECT, K.REREVIEW_SHEET_ALL,
                             K.WAFER_MAP_SHEET]
    assert _column(wb[K.REREVIEW_SHEET_ALL], K.REREVIEW_VERDICT_HEADER,
                   range(3, 7)) == ["Reject", "Reject", "Reject", "Good"]


def test_reject_map_paints_both_kinds(styled_qapp):
    """기존 Map 은 1차 Reject 만, 수정된 Map 은 신규 Reject 도 — 색이 서로 다르다."""
    from PyQt6.QtGui import QImage

    from aoi_verification.app.ui.widgets import wafer_map_view as wmv
    from .test_rereview import _real_map_text

    rm = rr.parse_map(_real_map_text(), Path("m"), WAFER)
    new = {(30, 30)}
    a = QImage.fromData(wmv.render_reject_map_png(rm, frozenset(), 400))
    b = QImage.fromData(wmv.render_reject_map_png(rm, new, 400))
    cols = wmv._colors()
    red = cols["unmatched"].rgb() & 0xFFFFFF
    blue = cols["map_reject"].rgb() & 0xFFFFFF

    def count(img, rgb):
        return sum(1 for y in range(img.height()) for x in range(img.width())
                   if img.pixel(x, y) & 0xFFFFFF == rgb)
    assert count(a, red) == 0 and count(a, blue) > 0
    assert count(b, red) > 0


# ── 메인 창: 설정 → 스캔 → Map 대조 → 선별 → 결과 화면 ─────────────────────────
def _pump_until(cond, timeout_s=30.0):
    import time

    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_end_to_end_rereview(qapp, lot, monkeypatch):
    """Map Reject die 사진(on_007)은 선별에 나오지 않고, 결과가 판정대로 나온다."""
    from aoi_verification.app.models import session as session_mod
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    root, folder, maps = lot
    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async", lambda self: None)
    results = root / "결과"
    results.mkdir()
    monkeypatch.setattr(mw.paths, "results_dir", lambda: results)
    win = mw.MainWindow()
    win._on_backend_loaded()
    summaries: list = []

    def fake_choose(_parent, title, text, options, **kw):
        summaries.append(text)
        return "go"

    monkeypatch.setattr(mw.sheets, "choose", fake_choose)
    win.show()
    try:
        win._on_start(SetupInput(mode="single", ref_root=folder.parent, val_root=maps,
                                 ref_machine="8", val_machine="", threshold=0.7,
                                 rereview=True))
        assert _pump_until(lambda: win._stack.currentWidget() is win._select_page)
        assert "1장을 빼고 4장을 재리뷰" in summaries[0]
        page = win._select_page
        assert page.is_rereview()
        seen = []
        for _ in range(4):
            cur = page._current
            seen.append(cur.path.stem)
            page._decide("verify" if cur.path.stem in ("a1", "b") else "exclude")
            assert _pump_until(lambda: page._current is not cur
                               or not page.get_state().queue, 5)
        assert "on_007" not in seen and sorted(seen) == ["a1", "a2", "b", "g"]
        assert _pump_until(lambda: win._stack.currentWidget() is win._result_page)
        res = win._result_page._result
        assert res.mode == REREVIEW_MODE
        st = rr.wafer_stats(res.rereview[WAFER], res.rereview_rejects(WAFER))
        assert (st.excluded, st.reject, st.new_reject_dies) == (1, 2, 2)
        # 결과 → '선별로' 는 선별 화면(판정 그대로)으로 돌아가고 [선별 종료] 로 다시 결과로.
        win._result_page.review_btn.click()
        assert win._stack.currentWidget() is win._select_page
        assert page.btn_end_selection.isEnabled()
        page.btn_end_selection.click()
        assert _pump_until(lambda: win._stack.currentWidget() is win._result_page)
        assert session_mod.load() is None          # 이어하기에 남지 않는다
    finally:
        win.close()


def test_notch_label_sits_below_the_dies(styled_qapp):
    """'Notch' 글자는 원판 **아래**에 있다 — die 격자를 덮지 않는다(사용자 지적)."""
    from PyQt6.QtGui import QImage

    from aoi_verification.app.ui.widgets import wafer_map_view as wmv
    from .test_rereview import _real_map_text

    rm = rr.parse_map(_real_map_text(), Path("m"), WAFER)
    img = QImage.fromData(wmv.render_reject_map_png(rm, frozenset(), 480,
                                                    label="Reject die 4 (신규 0)"))
    cols = wmv._colors()
    grid = cols["grid"].rgb() & 0xFFFFFF
    text = cols["text"].rgb() & 0xFFFFFF
    mid = range(img.width() // 2 - 40, img.width() // 2 + 40)
    grid_rows = [y for y in range(img.height())
                 if any(img.pixel(x, y) & 0xFFFFFF == grid for x in mid)]
    text_rows = [y for y in range(img.height())
                 if any(img.pixel(x, y) & 0xFFFFFF == text for x in mid)]
    assert grid_rows and text_rows
    assert min(text_rows) > max(grid_rows)        # 글자는 die 격자보다 아래
