"""Defect 추출 — 매칭 없이 wafer/LOT 폴더에서 고른 사진과 그 정보를 엑셀로.

사용자 결정(요약):
· 입력: wafer 폴더·LOT 폴더 모두(자동 판별), 한 번에 호기 1개, Camtek·KLA 모두.
· 진입: 설정 화면의 [Defect 추출] — 같은 화면을 한쪽 폴더만 받는 모드로 바꾼다.
· 선별: 매칭의 후보 선별 화면·조작 그대로.
· 엑셀: 양식.xlsx, 시트 1개(세로), A=No · B=Slot · C=사진 · D=정보 — **미매칭 사진
  정보를 적듯이**(D열 글자로 보이게) + Wafer Map 시트.  정렬 slot → 파일명.
· KLA slot명: 매칭과 같다(정보파일 우선, OCR 폴백, KLA 여부는 묻는다).
· 저장: 결과 엑셀과 같은 방식, 이름에 '추출'.  간단한 요약 후 저장.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aoi_verification.app.models.result import (EXTRACT_MODE, FinalResult,  # noqa: E402
                                                MissEntry, extract_result)
from aoi_verification.app.models.slot import (rename_slots_by_wafer_id,   # noqa: E402
                                              scan_one_side)


def _touch_jpeg(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        Image.new("RGB", (40, 30), color=(80, 120, 200)).save(str(p), "JPEG")
    except ImportError:
        p.write_bytes(b"")
    return p


# ── 폴더 판별 ────────────────────────────────────────────────────────────────
def test_lot_folder_gives_one_slot_per_subfolder(tmp_path):
    for slot, names in {"S1": ["b.jpg", "a.jpg"], "S2": ["c.jpg"]}.items():
        for n in names:
            _touch_jpeg(tmp_path / slot / n)
    (tmp_path / "S3").mkdir()                          # 사진 없는 슬롯도 목록엔 있다
    sr = scan_one_side(tmp_path)
    assert sorted(sr.slots) == ["S1", "S2", "S3"]
    assert [it.filename for it in sr.slots["S1"].ref_images] == ["a.jpg", "b.jpg"]
    assert all(it.side == "ref" and it.slot == "S1"
               for it in sr.slots["S1"].ref_images)
    assert sr.slots["S2"].ref_dir == tmp_path / "S2"
    assert not sr.slots["S1"].val_images
    assert sr.ref_only == [] and sr.val_only == []


def test_lot_folder_honours_subset(tmp_path):
    for slot in ("S1", "S2", "S3"):
        _touch_jpeg(tmp_path / slot / "a.jpg")
    assert sorted(scan_one_side(tmp_path, only={"S1", "S3"}).slots) == ["S1", "S3"]


def test_wafer_folder_is_one_slot_even_with_subfolders(tmp_path):
    """★ Camtek wafer 폴더에는 `_recipe_files_…` 같은 하위 폴더가 함께 있다 —
    '하위 폴더가 있으면 LOT' 으로 판정하면 그 폴더들이 슬롯이 되고 사진은 사라진다."""
    wafer = tmp_path / "GIX5703110"
    _touch_jpeg(wafer / "1_2_d.jpg")
    (wafer / "_recipe_files_GX57001305").mkdir()
    sr = scan_one_side(wafer, only={"_recipe_files_GX57001305"})   # only 는 무시된다
    assert list(sr.slots) == ["GIX5703110"]
    assert [it.filename for it in sr.slots["GIX5703110"].ref_images] == ["1_2_d.jpg"]
    assert sr.slots["GIX5703110"].ref_dir == wafer


def test_ignored_photos_do_not_make_a_wafer_folder(tmp_path):
    """웨이퍼 전경 사진만 있는 폴더는 wafer 폴더가 아니다(열거가 걸러 내는 사진)."""
    _touch_jpeg(tmp_path / "CognexInSight17xx_Bottom_Slot21.jpg")
    _touch_jpeg(tmp_path / "S1" / "a.jpg")
    assert list(scan_one_side(tmp_path).slots) == ["S1"]


def test_scan_reports_progress(tmp_path):
    for slot in ("S1", "S2"):
        _touch_jpeg(tmp_path / slot / "a.jpg")
    seen = []
    scan_one_side(tmp_path, progress=lambda d, t: seen.append((d, t)))
    assert seen == [(1, 2), (2, 2)]


# ── KLA slot명 ───────────────────────────────────────────────────────────────
def test_kla_folders_are_renamed_to_wafer_id(tmp_path):
    for slot in ("2026-08-02-23-09_4", "2026-08-03-20-28_2"):
        _touch_jpeg(tmp_path / slot / "x.jpg")
    sr = scan_one_side(tmp_path)
    kla = rename_slots_by_wafer_id(sr, {"2026-08-02-23-09_4": "a1033abqewg3"})
    # 표기는 매칭과 같은 대문자 정규화
    assert sorted(sr.slots) == ["2026-08-03-20-28_2", "A1033ABQEWG3"]
    assert kla == {"A1033ABQEWG3": "2026-08-02-23-09_4"}
    s = sr.slots["A1033ABQEWG3"]
    assert s.name == "A1033ABQEWG3"
    assert all(it.slot == "A1033ABQEWG3" for it in s.ref_images)
    assert s.ref_dir == tmp_path / "2026-08-02-23-09_4"      # 원본 폴더는 그대로


def test_kla_rename_never_overwrites_another_slot(tmp_path):
    """★ 두 폴더가 같은 WaferID 를 내면 두 번째는 폴더명 그대로 남는다 — 덮으면
    한쪽 웨이퍼의 사진이 조용히 사라진다."""
    for slot in ("K1", "K2"):
        _touch_jpeg(tmp_path / slot / "x.jpg")
    sr = scan_one_side(tmp_path)
    kla = rename_slots_by_wafer_id(sr, {"K1": "W1", "K2": "W1"})
    assert sorted(sr.slots) == ["K2", "W1"]
    assert kla == {"W1": "K1"}


# ── 결과 묶음 ────────────────────────────────────────────────────────────────
def test_extract_result_carries_only_picked_photos():
    r = extract_result("3", {"S2": [Path("/x/S2/b.jpg")], "S1": [Path("/x/S1/a.jpg")],
                             "S3": []},
                       kla_folders={"S1": "k"}, slot_numbers={"S1": "6"})
    assert r.mode == EXTRACT_MODE and r.matches == []
    assert [(u.slot, u.path.name, u.side) for u in r.unmatched_refs] == [
        ("S1", "a.jpg", "ref"), ("S2", "b.jpg", "ref")]
    # Wafer map 에는 고른 사진만 — 빈 슬롯은 행을 만들지 않는다.
    assert r.slot_images == {"S1": ([Path("/x/S1/a.jpg")], []),
                             "S2": ([Path("/x/S2/b.jpg")], [])}
    assert r.kla_folders == {"S1": "k"} and r.slot_numbers == {"S1": "6"}
    assert r.val_machine == ""


def test_wafer_map_has_no_match_state_for_extraction(monkeypatch, tmp_path):
    """추출은 매칭을 하지 않았다 — 점을 '미매치' 로 칠하면 거짓이다."""
    from aoi_verification.app.coords import wafer_map as wm
    photo = _touch_jpeg(tmp_path / "S1" / "a.jpg")
    seen = []
    monkeypatch.setattr(wm, "build_map", lambda coords, matched=None:
                        seen.append(matched) or wm.MapData(None, (), ()))
    wm.slot_maps(extract_result("3", {"S1": [photo]}), "S1")
    assert seen == [None, None]
    single = FinalResult(mode="single", ref_machine="1", val_machine="2",
                         slot_images={"S1": ([photo], [])})
    seen.clear()
    wm.slot_maps(single, "S1")
    assert seen == [set(), set()]                     # 매칭 결과는 예전 그대로


# ── 엑셀 ─────────────────────────────────────────────────────────────────────
def _export(result, dst, tpl=None, map_renderer=None):
    from aoi_verification.app.workers.exporter import ExcelExporter
    exp = ExcelExporter(result, dst_path=dst,
                        template_path=tpl or dst.parent / "no_template.xlsx",
                        map_renderer=map_renderer)
    failed = []
    exp.signals.failed.connect(failed.append)
    exp.run()
    assert not failed, failed
    return dst


def _png(_data, size):
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (size, size), "white").save(buf, "PNG")
    return buf.getvalue()


def test_excel_layout(qapp, tmp_path):
    pytest.importorskip("openpyxl")
    pytest.importorskip("PIL.Image")
    from openpyxl import load_workbook

    from aoi_verification.app import i18n
    from aoi_verification.app.utils import paths
    from aoi_verification.app.workers import exporter as ex

    a = _touch_jpeg(tmp_path / "src" / "S2" / "a.jpg")
    b = _touch_jpeg(tmp_path / "src" / "S1" / "b.jpg")
    c = _touch_jpeg(tmp_path / "src" / "S1" / "a.jpg")
    result = extract_result("3호기", {"S2": [a], "S1": [b, c]},
                            slot_numbers={"S1": "6"})
    tpl = paths.template_path()
    dst = _export(result, tmp_path / "out.xlsx",
                  tpl=tpl if tpl.exists() else None, map_renderer=_png)

    wb = load_workbook(str(dst), rich_text=True)
    # 시트 1개(요약) + Wafer Map — '미매칭 사진' 시트는 없다.
    assert wb.sheetnames == ["out", i18n.KO.WAFER_MAP_SHEET]
    ws = wb["out"]
    assert ws["C2"].value == "AOI-3"
    assert ws["D2"].value == i18n.KO.EXTRACT_INFO_HEADER
    # 정렬: slot → 파일명
    rows = [(ws[f"A{r}"].value, str(ws[f"B{r}"].value).split("\n")[0],
             str(ws[f"D{r}"].value).split("\n")[0]) for r in (3, 4, 5)]
    assert rows == [(1, "S1", "a.jpg"), (2, "S1", "b.jpg"), (3, "S2", "a.jpg")]
    assert ws["B3"].value == "S1\n(#6)"
    assert len(ws._images) == 3                        # C열 사진
    for r in (3, 4, 5):
        d = ws[f"D{r}"]
        assert d.comment is None                       # '미매칭' 메모 없음
        assert ex.UNMATCHED_FILL not in str(d.fill.fgColor.rgb)   # 미매칭 틴트 없음
    # Wafer Map — 장비가 하나라 맵 칸도 하나.
    wm = wb[i18n.KO.WAFER_MAP_SHEET]
    assert wm["B1"].value.startswith(i18n.KO.EXTRACT_MAP_SHEET_COL)
    assert "AOI-3" in wm["B1"].value
    assert wm["C1"].value is None
    assert [wm.cell(row=r, column=1).value for r in (2, 3, 4)] == [
        i18n.KO.WAFER_MAP_SHEET_ALL, "S1\n(#6)", "S2"]


def test_info_column_is_written_like_an_unmatched_row(qapp, tmp_path):
    """★ 사용자 결정: '마치 미매칭 사진 정보 출력하듯이'.  D열 글자는 매칭 결과의
    미매칭 행이 적는 것과 **한 글자도 다르지 않아야** 한다(같은 생산자)."""
    pytest.importorskip("openpyxl")
    pytest.importorskip("PIL.Image")
    from openpyxl import load_workbook

    p = _touch_jpeg(tmp_path / "src" / "S1" / "a.jpg")
    ext = _export(extract_result("3", {"S1": [p]}), tmp_path / "ext.xlsx")
    ref = _export(FinalResult(mode="single", ref_machine="3", val_machine="4",
                              unmatched_refs=[MissEntry("S1", "ref", p)]),
                  tmp_path / "ref.xlsx")
    d_ext = load_workbook(str(ext), rich_text=True)["ext"]["D3"].value
    d_ref = load_workbook(str(ref), rich_text=True)["ref"]["D3"].value
    assert str(d_ext) == str(d_ref)
    assert "a.jpg" in str(d_ext)


# ── 설정 화면 ────────────────────────────────────────────────────────────────
def test_setup_extract_mode_takes_one_folder(qapp, tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.pages import setup_page as sp

    page = sp.SetupPage()
    try:
        page.ref_path_edit.setText(str(tmp_path))
        page.val_path_edit.setText("")
        assert page._validate() is False               # 매칭은 두 폴더가 필요
        page.set_extract_mode(True)
        assert page.is_extract_mode()
        assert page._validate() is True                # 추출은 한 폴더면 된다
        assert page.val_group.isHidden()
        assert page._setting_cards[1].isHidden()       # 매칭 설정
        assert page._mode_badge_card.isHidden()
        assert not page._setting_cards[0].isHidden()   # 실행 옵션은 남는다
        assert page.start_btn.text() == i18n.KO.BTN_EXTRACT_START
        assert page._title_label.text() == i18n.KO.EXTRACT_TITLE
        assert page.extract_btn.text() == i18n.KO.EXTRACT_BACK_BUTTON
        page.ref_machine_edit.setText("17")
        inp = page._collect_input()
        assert inp.extract is True
        assert inp.ref_root == tmp_path and inp.val_root == tmp_path
        assert inp.ref_machine == "17" and inp.val_machine == ""
        # 되돌리면 원래 화면 그대로(입력값 보존)
        page.set_extract_mode(False)
        assert not page.val_group.isHidden()
        assert not page._setting_cards[1].isHidden()
        assert page.start_btn.text() == i18n.KO.BTN_START
        assert page.ref_path_edit.text() == str(tmp_path)
        assert page._validate() is False
    finally:
        page.deleteLater()


def test_extract_button_sits_in_the_auxiliary_group(qapp):
    """액션바 자리 계약 — 보조 버튼은 stretch 앞, 주 액션은 맨 끝."""
    from aoi_verification.app.ui.pages import setup_page as sp
    page = sp.SetupPage()
    try:
        bar = page._action_bar
        widgets = [bar.itemAt(i).widget() for i in range(bar.count())]
        stretch_at = next(i for i in range(bar.count())
                          if bar.itemAt(i).spacerItem() is not None)
        assert widgets.index(page.extract_btn) < stretch_at
        assert widgets[-1] is page.start_btn
    finally:
        page.deleteLater()


# ── 메인 창 ──────────────────────────────────────────────────────────────────
def test_file_name_follows_result_rule_with_extract(qapp, tmp_path):
    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.pages.setup_page import SetupInput

    lot = tmp_path / "LOT"
    (lot / "S1").mkdir(parents=True)
    (lot / "S1" / "WaferInfo.ini").write_text("InputLot=GFW-RDL4\n", encoding="utf-8")
    inp = SetupInput(mode="single", ref_root=lot, val_root=lot, ref_machine="17",
                     val_machine="", threshold=0.7, extract=True)
    assert mw.MainWindow._suggest_result_name(inp) == "17 RDL4_GFW Defect 추출.xlsx"
    inp.ref_root = inp.val_root = tmp_path / "없음"
    assert mw.MainWindow._suggest_result_name(inp) == "AOI 17 Defect 추출.xlsx"


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


def test_end_to_end_select_then_save(qapp, tmp_path, monkeypatch):
    """설정(추출) → 스캔 → 썸네일 → 후보 선별(실제 SelectPage) → 요약 → 저장.

    매칭·검토·결과 화면은 거치지 않고, 저장 뒤에는 설정 화면으로 돌아온다."""
    pytest.importorskip("openpyxl")
    pytest.importorskip("PIL.Image")
    from openpyxl import load_workbook

    from aoi_verification.app.ui import main_window as mw
    from aoi_verification.app.ui.pages.setup_page import SetupInput
    from aoi_verification.app.ui.widgets import save_name_dialog

    lot = tmp_path / "LOT"
    for slot, names in {"S1": ["a.jpg", "b.jpg"], "S2": ["c.jpg"]}.items():
        for n in names:
            _touch_jpeg(lot / slot / n)

    monkeypatch.setattr(mw.MainWindow, "_start_backend_import_async",
                        lambda self: None)
    # 개발 모드의 결과 폴더는 저장소 안(`결과/`)이다 — 테스트가 거기에 쓰면 안 된다.
    results = tmp_path / "결과"
    results.mkdir()
    monkeypatch.setattr(mw.paths, "results_dir", lambda: results)
    win = mw.MainWindow()
    win._on_backend_loaded()                  # 나머지 페이지를 지금 만든다
    choices: list = []

    def fake_choose(_parent, title, text, options, **kw):
        keys = [o[0] for o in options]
        choices.append((title, text, keys))
        if "kla" in keys:
            return "none"                     # KLA 아님
        if "save" in keys:
            return "save"
        return "close"                        # 저장 완료 → 닫기

    monkeypatch.setattr(mw.sheets, "choose", fake_choose)

    class _Dlg:
        def __init__(self, name, _folder, parent=None):
            self.chosen = name

    monkeypatch.setattr(save_name_dialog, "SaveNameDialog", _Dlg)
    monkeypatch.setattr(mw.sheets, "run", lambda dlg, **k: True)
    win.show()                                # 선별은 보이는 화면에서만 결정을 받는다
    try:
        win._on_start(SetupInput(mode="single", ref_root=lot, val_root=lot,
                                 ref_machine="17", val_machine="", threshold=0.7,
                                 extract=True))
        assert _pump_until(lambda: win._stack.currentWidget() is win._select_page)
        # 매칭과 같은 선별 화면 — 3장 중 a, c 를 고르고 b 를 뺀다(방향키와 같은 경로).
        page = win._select_page
        for _ in range(3):
            cur = page._current
            page._decide("exclude" if cur.filename == "b.jpg" else "verify")
            assert _pump_until(lambda: page._current is not cur
                               or not page.get_state().queue, 5)
        assert _pump_until(lambda: win._stack.currentWidget() is win._setup_page)
        summary = [c for c in choices if "save" in c[2]]
        assert summary and "2" in summary[0][1]       # defect 2건
        saved = list(results.glob("*추출*.xlsx"))
        assert len(saved) == 1
        ws = load_workbook(str(saved[0]))[saved[0].stem[:31]]
        names = [str(ws[f"D{r}"].value).split("\n")[0] for r in (3, 4)]
        assert names == ["a.jpg", "c.jpg"]
        # 추출 세션은 이어하기에 남지 않는다
        from aoi_verification.app.models import session as session_mod
        assert session_mod.load() is None
    finally:
        win.close()
