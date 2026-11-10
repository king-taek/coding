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
    assert (ref.photos, ref.slots, ref.with_coord, ref.machine) == (3, 2, 2, "17")
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
    assert (ref.photos, ref.with_coord, ref.machine) == (2, 2, "17")
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


@pytest.mark.parametrize("layout", ["single", "sheets", "columns"])
def test_roundtrip_defect_extract_layouts(qapp, isolated_cache, tmp_path, layout):
    """Defect 추출 엑셀(세 가지 Recipe 배치)도 다시 읽힌다 — 사진 C · 정보 D(또는 [사진|정보] 열)."""
    from aoi_verification.app.models.result import extract_result
    from aoi_verification.app.workers import exporter as ex

    f = tmp_path / "w"
    f.mkdir()
    paths = []
    for i, (c, r) in enumerate([(1, 2), (3, 4), (5, 6)]):
        p = f / f"AOI_RDL4_W1_{c}_{r}_{1000 + i}_{2000 + i}_Bump.jpg"
        Image.new("RGB", (50, 40), (10 * i, 80, 80)).save(p)
        paths.append(p)
    dst = tmp_path / "ext.xlsx"
    ex.ExcelExporter(extract_result("3", {"S1": paths}), dst_path=dst,
                     template_path=tmp_path / "none.xlsx",
                     recipe_layout=layout).run()
    ref = excel_ref.load_excel_ref(dst)
    assert (ref.photos, ref.with_coord, ref.machine) == (3, 3, "3")
    for p in paths:
        got, want = coords.resolve(ref.root / "S1" / p.name), coords.resolve(p)
        assert (got.col, got.row) == (want.col, want.row)


def test_machine_input_inverts_exporter_label():
    from aoi_verification.app.workers.exporter import _machine_label
    for raw in ("17", "K-2", "KLA"):
        assert excel_ref.machine_input(_machine_label(raw)) == raw


def test_resolve_batch_mixes_excel_ref_and_live_folder(tmp_path):
    """기준(엑셀에서 푼 폴더)과 검증(LIVE 파일명 폴더)의 좌표가 한 실행 안에서 같은 프레임으로 풀린다."""
    x = tmp_path / "prev.xlsx"
    _book(x, [("S", "AOI_RDL4_W1_5_4_31863_26908_Bump.jpg", CAMTEK, "C")])
    ref = excel_ref.load_excel_ref(x)
    val = tmp_path / "v"
    val.mkdir()
    vp = val / "AOI_RDL4_W1_5_4_31860_26910_Bump.jpg"
    vp.write_bytes(b"x")
    rp = ref.root / "S" / "AOI_RDL4_W1_5_4_31863_26908_Bump.jpg"
    out = coords.resolve_batch([rp, vp])
    assert out[rp].source == out[vp].source == "camtek_live"
    assert (out[rp].col, out[rp].row) == (3, 5)        # 엑셀 셀에 적힌 번호가 우선한다


def test_kla_reference_excel_behaves_like_a_kla_folder(tmp_path):
    """KLA 기준 엑셀 — 정보파일이 없어도 KLA 로 인식(WaferID=슬롯 폴더명 · 카세트 번호)되고,
    같은 이름의 Camtek 검증 폴더와 짝지어지며, KLA↔Camtek 판별이 선다(좌표 보정이 돈다)."""
    from aoi_verification.app.coords import kla_info
    from aoi_verification.app.models.slot import scan

    x = tmp_path / "kla_prev.xlsx"
    _book(x, [("W6459076XYG1\n(#6)", "k1.jpg", KLA, "C"),
              ("W6459076XYG1\n(#6)", "k2.jpg", KLA, "C"),
              ("CAMTEKONLY", "c1.jpg", CAMTEK, "C")])
    ref = excel_ref.load_excel_ref(x)
    kla_dir, cam_dir = ref.root / "W6459076XYG1", ref.root / "CAMTEKONLY"
    assert kla_info.read_wafer_id(kla_dir) == "W6459076XYG1"
    assert kla_info.read_slot_no(kla_dir) == "6"
    assert kla_info.read_wafer_id(cam_dir) is None          # Camtek 좌표 폴더는 KLA 가 아니다
    val = tmp_path / "val"
    (val / "W6459076XYG1").mkdir(parents=True)
    Image.new("RGB", (40, 30)).save(val / "W6459076XYG1" / "v.jpg")
    sc = scan(ref.root, val)
    assert "W6459076XYG1" in sc.common_slot_names             # 이름이 같아 KLA 해석 없이 바로 짝
    assert len(sc.slots["W6459076XYG1"].ref_images) == 2


def _two_lot_extract(tmp_path, layout="single"):
    """LOT 둘에 같은 이름의 슬롯 S1 — 여러 LOT Defect 추출 엑셀(시트가 LOT 마다)."""
    from aoi_verification.app.models.result import extract_result
    from aoi_verification.app.workers import exporter as ex

    slots = {}
    for lot, base in (("LOT_A", 0), ("LOT_B", 10)):
        f = tmp_path / lot / "S1"
        f.mkdir(parents=True)
        ps = []
        for i, (c, r) in enumerate([(1 + base, 2), (3 + base, 4)]):
            p = f / f"AOI_RDL4_W1_{c}_{r}_{1000 + i}_{2000 + i}_Bump.jpg"
            Image.new("RGB", (50, 40), (10 * i, 80, 80)).save(p)
            ps.append(p)
        slots[f"{lot}/S1"] = ps
    dst = tmp_path / "ext2.xlsx"
    ex.ExcelExporter(extract_result("3", slots), dst_path=dst,
                     template_path=tmp_path / "none.xlsx",
                     recipe_layout=layout).run()
    return dst, slots


def test_multi_lot_extract_asks_which_sheet(qapp, isolated_cache, tmp_path):
    """★ LOT 마다 같은 슬롯 이름 — 그냥 합치면 다른 LOT 사진이 한 웨이퍼로 섞인다.
    시트를 고르라고 묻고, 고른 시트(LOT)의 사진만 푼다."""
    dst, slots = _two_lot_extract(tmp_path)
    with pytest.raises(excel_ref.ExcelRefNeedsSheets) as e:
        excel_ref.load_excel_ref(dst)
    titles = [t for t, _n in e.value.sheets]
    assert "LOT_A" in titles and "LOT_B" in titles
    assert dict(e.value.sheets)["LOT_A"] == 2

    ref = excel_ref.load_excel_ref(dst, sheets=["LOT_B"])
    assert ref.photos == 2 and ref.slot_names == ["S1"]
    got = sorted(p.name for p in (ref.root / "S1").glob("*.jpg"))
    assert got == sorted(p.name for p in slots["LOT_B/S1"])
    # 다른 시트를 고르면 다른 폴더로 푼다(앞서 푼 것을 재사용하지 않는다).
    ref_a = excel_ref.load_excel_ref(dst, sheets=["LOT_A"])
    assert ref_a.root != ref.root
    assert sorted(p.name for p in (ref_a.root / "S1").glob("*.jpg")) == \
        sorted(p.name for p in slots["LOT_A/S1"])


def test_summary_subset_sheets_do_not_ask(tmp_path):
    """매칭 결과처럼 한 시트가 다른 시트에 포함되면(요약·미매칭) 묻지 않는다."""
    assert not excel_ref._sheets_conflict(
        {"전체": {"S": {"a", "b"}}, "미매칭": {"S": {"b"}}})
    assert excel_ref._sheets_conflict(
        {"LOT_A": {"S": {"a"}}, "LOT_B": {"S": {"b"}}})


def test_sheet_picker_reuses_slot_tiles_and_blocks_empty(styled_qapp):
    """시트 고르기 창 — 슬롯 선택 창을 그대로 쓰되 문구·둘째 줄은 시트용, 처음엔 아무것도 안 골라 확인이 잠긴다."""
    from aoi_verification.app.ui.widgets.slot_select_dialog import SlotSelectDialog
    dlg = SlotSelectDialog(["LOT_A", "LOT_B"], preselected=(),
                           title=K.EXCEL_REF_PICK_SHEETS_TITLE,
                           hint=K.EXCEL_REF_PICK_SHEETS_HINT,
                           notes={"LOT_A": "사진 2장", "LOT_B": "사진 3장"})
    assert dlg.windowTitle() == K.EXCEL_REF_PICK_SHEETS_TITLE
    assert dlg._tiles["LOT_B"].count_text() == "사진 3장"
    assert not dlg._ok.isEnabled()
    dlg._tiles["LOT_B"].setChecked(True)
    dlg._refresh_counts()
    assert dlg._ok.isEnabled() and dlg.selected == {"LOT_B"}
    dlg._tiles["LOT_A"].setChecked(True)
    dlg._refresh_counts()
    assert "모든 슬롯" not in dlg._head.text()
    dlg.deleteLater()
