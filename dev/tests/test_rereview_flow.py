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
def test_setup_rereview_mode_uses_two_folders(styled_qapp, tmp_path):
    from aoi_verification.app.ui.pages import setup_page as sp

    scan, maps = tmp_path / "scan", tmp_path / "maps"
    scan.mkdir()
    maps.mkdir()
    page = sp.SetupPage()
    try:
        page.ref_path_edit.setText("매칭 기준")
        page.val_path_edit.setText("매칭 검증")
        page.set_rereview_mode(True)
        assert page.is_rereview_mode()
        assert page._title_label.text() == i18n.KO.REREVIEW_TITLE
        assert page.start_btn.text() == i18n.KO.BTN_REREVIEW_START
        assert all(c.isHidden() for c in page._setting_cards)
        assert page.val_machine_edit.isHidden() and page.extract_btn.isHidden()
        page.ref_path_edit.setText(str(scan))
        page.val_path_edit.setText(str(maps))
        assert page._validate() is True
        page.ref_machine_edit.setText("8")
        inp = page._collect_input()
        assert inp.rereview is True and not inp.extract
        assert (inp.ref_root, inp.val_root, inp.ref_machine) == (scan, maps, "8")
        # 되돌리면 매칭 입력이 그대로 돌아온다(모드별로 따로 기억).
        page.set_rereview_mode(False)
        assert page.ref_path_edit.text() == "매칭 기준"
        assert page.val_path_edit.text() == "매칭 검증"
        assert not page._setting_cards[1].isHidden()
        assert not page.extract_btn.isHidden()
    finally:
        page.deleteLater()


def test_rereview_button_stays_out_of_the_action_bar(styled_qapp):
    """액션바에 붙이면 800px 창에서 페이지가 창보다 넓어진다(실측 967px) — 위 줄에 둔다.

    액션바 자리 계약(주 액션이 맨 끝)도 그대로다."""
    from aoi_verification.app.ui.pages import setup_page as sp
    page = sp.SetupPage()
    try:
        bar = page._action_bar
        widgets = [bar.itemAt(i).widget() for i in range(bar.count())]
        assert page.rereview_btn not in widgets
        assert widgets[-1] is page.start_btn
    finally:
        page.deleteLater()


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


def test_result_page_shows_rereview_counts(styled_qapp, lot):
    from PyQt6.QtWidgets import QLabel

    from aoi_verification.app.ui.pages.result_page import ResultPage

    page = ResultPage()
    try:
        page.show_result(_result(lot))
        texts = [w.text() for w in page._summary_card.findChildren(QLabel)]
        assert i18n.KO.REREVIEW_STAT_NEW_DIES in texts
        # 사진 5 · 제외 1 · 재리뷰 4 · Reject 3 / die 신규 2 · Map 4 · 합계 6
        line = i18n.KO.REREVIEW_WAFER_LINE_FMT.format(
            wafer=WAFER, total=5, excluded=1, reviewed=4, reject=3, new=2, map=4, sum=6)
        assert line in texts
        assert page.title.text() == i18n.KO.REREVIEW_RESULT_TITLE
        assert page.wafer_map_btn.isHidden() and page.review_unmatched_btn.isHidden()
    finally:
        page.deleteLater()


def test_excel_has_verdict_summary_and_map(styled_qapp, lot, tmp_path):
    from aoi_verification.app.ui.widgets.wafer_map_view import render_reject_map_png
    from aoi_verification.app.workers.exporter import ExcelExporter

    dst = tmp_path / "out" / "재리뷰.xlsx"
    ex = ExcelExporter(_result(lot), dst,
                       template_path=Path("dev/양식.xlsx"),
                       reject_map_renderer=render_reject_map_png)
    errors: list = []
    ex.signals.failed.connect(errors.append)
    ex.run()                                  # 같은 스레드에서 — 결과만 본다
    assert not errors, errors
    wb = openpyxl.load_workbook(dst)
    assert wb.sheetnames[0] == i18n.KO.REREVIEW_SUMMARY_SHEET
    assert i18n.KO.WAFER_MAP_SHEET in wb.sheetnames
    summ = wb[i18n.KO.REREVIEW_SUMMARY_SHEET]
    assert [c.value for c in summ[2]][1:9] == [5, 1, 4, 1, 3, 2, 4, 6]
    photos = wb[wb.sheetnames[1]]
    head = [c.value for c in photos[1]]
    v = head.index(i18n.KO.REREVIEW_VERDICT_HEADER)
    verdicts = [photos.cell(row=r, column=v + 1).value for r in range(3, 7)]
    assert verdicts == ["Reject", "Reject", "Reject", "Good"]
    dies = [photos.cell(row=r, column=v + 2).value for r in range(3, 7)]
    assert dies[0] == dies[1] != dies[2]              # a1·a2 는 같은 die
    assert len(wb[i18n.KO.WAFER_MAP_SHEET]._images) == 1


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
