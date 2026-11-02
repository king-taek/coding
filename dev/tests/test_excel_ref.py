"""이전 결과 엑셀을 기준으로 쓰기 — `utils/excel_ref` · `coords/excel_ref` 회귀 가드.

엑셀은 exporter 와 같은 모양(A=No · B=슬롯 · C=기준 그림 + 셀 글자)으로 openpyxl 로 직접 만든다.
줄 문구는 ko.py 의 *_FMT 를 그대로 써서, 표기가 바뀌면 이 테스트가 먼저 깨진다.
"""
from __future__ import annotations

import io

import pytest

pytest.importorskip("openpyxl")
PIL = pytest.importorskip("PIL")
from openpyxl import Workbook                         # noqa: E402
from openpyxl.drawing.image import Image as XLImage   # noqa: E402
from PIL import Image                                  # noqa: E402

from aoi_verification.app import coords, i18n          # noqa: E402
from aoi_verification.app.utils import excel_ref       # noqa: E402

K = i18n.KO


def _png(color=(200, 10, 10)) -> io.BytesIO:
    b = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(b, "PNG")
    b.seek(0)
    return b


def _book(path, rows, *, header_d="AOI-23"):
    """rows: (slot, 파일명, 글자 줄들, 그림 열 'C'|'D')."""
    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    ws["C2"], ws["D2"] = "AOI-17", header_d
    for i, (slot, name, lines, col) in enumerate(rows, start=3):
        ws[f"A{i}"], ws[f"B{i}"] = i - 2, slot
        ws[f"{col}{i}"] = "\n".join([name, *lines])
        ws.add_image(XLImage(_png()), f"{col}{i}")
    wb.save(path)


CAMTEK = [K.DEFECT_COLROW_FMT.format(col=3, row=5), K.DEFECT_XY_FMT.format(x=1234.4, y=567.8)]
KLA = [K.DEFECT_COLROW_FMT.format(col=2, row=4), K.DEFECT_XY_FMT.format(x=100, y=200),
       K.EXPORT_KLA_NATIVE_FMT.format(x=11, y=22)]


def test_parse_camtek_kla_abs_and_none():
    assert excel_ref.parse_coord_lines(CAMTEK) == {
        "col": 3, "row": 5, "x": 1234.0, "y": 568.0, "source": "camtek_live"}
    k = excel_ref.parse_coord_lines(KLA)
    assert (k["source"], k["native_x"], k["native_y"]) == ("kla", 11.0, 22.0)
    a = excel_ref.parse_coord_lines([K.DEFECT_COL_UNKNOWN_FMT,
                                     K.DEFECT_ABS_XY_FMT.format(x=9000, y=8000)])
    assert (a["source"], a["x"], a["y"]) == ("camtek_abs", 9000.0, 8000.0)
    assert excel_ref.parse_coord_lines(["면적 3 ㎛²"]) is None


def test_load_unpacks_only_reference_column(tmp_path):
    x = tmp_path / "prev.xlsx"
    _book(x, [("SLOT_A", "p1.jpg", CAMTEK, "C"),
              ("SLOT_A", "p2.jpg", [], "C"),
              ("SLOT_B", "p3.jpg", KLA, "C"),
              ("SLOT_B", "val.jpg", CAMTEK, "D")])           # D = 검증 → 풀지 않는다
    ref = excel_ref.load_excel_ref(x)
    assert (ref.photos, ref.slots, ref.with_coord, ref.machine) == (3, 2, 2, "AOI-17")
    assert sorted(p.name for p in ref.root.glob("*/*.jpg")) == ["p1.jpg", "p2.jpg", "p3.jpg"]
    assert not list(ref.root.glob("*/val.jpg"))


def test_coords_resolve_from_sidecar(tmp_path):
    x = tmp_path / "prev.xlsx"
    _book(x, [("SLOT_A", "p1.jpg", CAMTEK, "C"), ("SLOT_B", "p3.jpg", KLA, "C")])
    ref = excel_ref.load_excel_ref(x)
    c = coords.resolve(ref.root / "SLOT_A" / "p1.jpg")
    assert (c.col, c.row, c.source) == (3, 5, "camtek_live")
    k = coords.resolve(ref.root / "SLOT_B" / "p3.jpg")
    assert (k.source, k.native_x) == ("kla", 11.0)
    assert coords.resolve(ref.root / "SLOT_B" / "없는.jpg") is None


def test_same_photo_in_two_sheets_counts_once(tmp_path):
    x = tmp_path / "prev.xlsx"
    _book(x, [("S", "p1.jpg", CAMTEK, "C")])
    from openpyxl import load_workbook
    wb = load_workbook(x)
    ws2 = wb.copy_worksheet(wb.active)
    ws2.title = "미매칭"
    for img in wb.active._images:
        ws2.add_image(XLImage(_png()), "C3")
    wb.save(x)
    ref = excel_ref.load_excel_ref(x)
    assert (ref.photos, ref.skipped) == (1, 1)


def test_reuses_unpacked_folder_and_rejects_empty(tmp_path):
    x = tmp_path / "prev.xlsx"
    _book(x, [("S", "p1.jpg", CAMTEK, "C")])
    a = excel_ref.load_excel_ref(x)
    assert excel_ref.load_excel_ref(x).root == a.root
    e = tmp_path / "empty.xlsx"
    Workbook().save(e)
    with pytest.raises(excel_ref.ExcelRefError):
        excel_ref.load_excel_ref(e)
    with pytest.raises(excel_ref.ExcelRefError):
        excel_ref.load_excel_ref(tmp_path / "없음.xlsx")


def test_wafer_map_sheet_is_skipped(tmp_path):
    x = tmp_path / "prev.xlsx"
    _book(x, [("S", "p1.jpg", CAMTEK, "C")])
    from openpyxl import load_workbook
    wb = load_workbook(x)
    wm = wb.create_sheet(K.WAFER_MAP_SHEET)
    wm["A3"], wm["C3"] = 1, "맵"
    wm.add_image(XLImage(_png()), "C3")
    wb.save(x)
    assert excel_ref.load_excel_ref(x).photos == 1


def test_roundtrip_with_real_exporter(qapp, isolated_cache, tmp_path):
    """exporter 가 실제로 쓴 엑셀(매칭 결과)을 다시 읽어도 기준 사진·좌표가 그대로 돌아온다."""
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.models.result import FinalResult, MatchResult, MissEntry
    from aoi_verification.app.workers import exporter as ex

    ref_f, val_f = tmp_path / "ref", tmp_path / "val"
    ref_f.mkdir()
    val_f.mkdir()

    def live(folder, col, row, x, y, tag):
        p = folder / f"AOI_RDL4_W75483_{col}_{row}_{x}_{y}_{tag}.jpg"
        Image.new("RGB", (60, 40), (30, 90, 160)).save(p)
        return p

    a, b = live(ref_f, 5, 4, 31863, 26908, "Bump"), live(ref_f, 2, 3, 100, 200, "Dent")
    v = live(val_f, 5, 4, 31860, 26910, "Bump")
    result = FinalResult(
        mode="single", ref_machine="17", val_machine="23",
        matches=[MatchResult(slot="W75483", ref_path=a, val_path=v, score=0.9)],
        unmatched_refs=[MissEntry(slot="W75483", side="ref", path=b, note="")])
    dst = tmp_path / "prev.xlsx"
    ex.ExcelExporter(result, dst_path=dst, template_path=tmp_path / "none.xlsx").run()

    ref = excel_ref.load_excel_ref(dst)
    assert (ref.photos, ref.with_coord, ref.machine) == (2, 2, "AOI-17")
    for orig in (a, b):                              # 검증 사진(v)은 풀리지 않는다
        got = coords.resolve(ref.root / "W75483" / orig.name)
        want = coords.resolve(orig)
        assert (got.col, got.row) == (want.col, want.row)
        assert abs(got.x - want.x) <= 1 and abs(got.y - want.y) <= 1
    assert len(list(ref.root.glob("*/*.jpg"))) == 2
    from aoi_verification.app.models.slot import scan
    sc = scan(ref.root, val_f.parent / "val")          # 풀어 둔 폴더가 기준 폴더로 스캔된다
    assert sc.ref_only or sc.slots or sc.val_only


def test_apply_excel_ref_resets_slot_selection(styled_qapp):
    pytest.importorskip("PyQt6.QtWidgets")
    from aoi_verification.app.ui.pages.setup_page import SetupPage
    w = SetupPage()
    w._selected_slots = {"옛슬롯"}
    w.apply_excel_ref("/x/a.xlsx", str(__import__("tempfile").gettempdir()), "AOI-17", 3, 1, 3)
    assert w._selected_slots is None
