"""엑셀(`양식.xlsx`) 출력 워커.

- 양식.xlsx 를 그대로 템플릿으로 로드해서 셀 서식/병합/열폭/행높이를 보존한다.
- 헤더(C2/D2)의 ‘AOI-N’ 만 검증 세션의 호기 번호로 교체.
- 데이터: A=번호, B=Slot, C=기준(낮은 호기) 이미지, D=검증(높은 호기) 이미지.
- E~H 컬럼(Escape Defect Camtek/KLA)은 사용자가 수기로 채울 영역이라 비워 둔다.
- ‘매칭 방향’ 컬럼은 사용자 요청으로 더 이상 쓰지 않는다 (#4).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .. import i18n
from ..models.result import FinalResult, MatchResult, MissEntry
from ..utils import image_io


# ---------------------------------------------------------------------------
# 양식.xlsx 의 컬럼 레이아웃 (사용자 양식과 1:1)
#   A = No / B = slot# / C = 기준(낮은 호기) / D = 검증(높은 호기)
# Header 는 두 줄: row 1 (그룹) + row 2 (호기 번호 = ‘AOI-N’). 데이터는 row 3 부터.
# ---------------------------------------------------------------------------
COL_NO = "A"
COL_SLOT = "B"
COL_REF = "C"
COL_VAL = "D"
DATA_START_ROW = 3
HEADER_AOI_ROW = 2

# 셀 ↔ 사진 크기 정합:
#   · 양식.xlsx 의 데이터 행 높이 (165.75pt) 와 일치시켜 템플릿 안팎의 행 높이를
#     맞춘다. 양식이 없을 때(폴백) 도 동일 값으로 통일.
#   · 이미지 max 변 = 150 px. 1pt ≈ 1.333 px 이므로 165pt ≈ 220 px → 150 px
#     이미지가 셀 안에 여유 있게 들어간다.
ROW_HEIGHT_PT = 165.75
IMG_COL_WIDTH = 22
IMG_MAX_PX = 150


def _machine_label(raw: str) -> str:
    """‘1호기’ / ‘AOI-1’ / ‘1’ 같은 입력을 모두 ‘AOI-N’ 형식으로 정규화."""
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.search(r"\d+", s)
    if not m:
        return s
    return f"AOI-{m.group(0)}"


class ExporterSignals(QObject):
    progress = pyqtSignal(int, int, str)
    done = pyqtSignal(str)               # 결과 파일 경로
    failed = pyqtSignal(str)


class ExcelExporter(QThread):
    """`FinalResult` 를 받아 양식.xlsx 템플릿에 채워 저장."""

    def __init__(self,
                 result: FinalResult,
                 dst_path: Path,
                 template_path: Optional[Path] = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._result = result
        self._dst = Path(dst_path)
        self._template = Path(template_path) if template_path else None
        self.signals = ExporterSignals()

    # ------------------------------------------------------------------
    def run(self) -> None:        # type: ignore[override]
        try:
            self._do_export()
            self.signals.done.emit(str(self._dst))
        except Exception as exc:
            self.signals.failed.emit(str(exc))

    # ------------------------------------------------------------------
    def _do_export(self) -> None:
        from openpyxl import Workbook, load_workbook

        # 양식이 있으면 그대로 로드해서 셀 서식을 모두 보존.
        # 없으면 동일 컬럼 구조의 워크북을 빈 상태로 만든다.
        if self._template is not None and self._template.exists():
            wb = load_workbook(str(self._template))
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "AOI 검증 결과"
            self._build_minimal_headers(ws)

        # row 2 의 ‘AOI-N’ 헤더를 실제 호기 번호로 교체 (#3).
        ref_label = _machine_label(self._result.ref_machine)
        val_label = _machine_label(self._result.val_machine)
        if ref_label:
            ws[f"{COL_REF}{HEADER_AOI_ROW}"] = ref_label
        if val_label:
            ws[f"{COL_VAL}{HEADER_AOI_ROW}"] = val_label

        # 기본 폭 보장 — 템플릿에 폭이 지정 안 된 컬럼만.
        def _ensure_width(col_letter: str, min_w: float) -> None:
            cur = ws.column_dimensions[col_letter].width
            if not cur or cur < min_w:
                ws.column_dimensions[col_letter].width = min_w
        _ensure_width(COL_REF, IMG_COL_WIDTH)
        _ensure_width(COL_VAL, IMG_COL_WIDTH)
        _ensure_width(COL_SLOT, 14)
        _ensure_width(COL_NO, 6)

        # 매칭/미매칭 통합 정렬 → Slot 오름차순, 그 안에서 기준 파일명 오름차순.
        rows_input: list[tuple[str, str, object]] = []
        for m in self._result.matches:
            rows_input.append((m.slot, str(m.ref_path.name).lower(), m))
        for u in self._result.unmatched_refs:
            rows_input.append((u.slot, str(u.path.name).lower(), u))
        rows_input.sort(key=lambda x: (x[0], x[1]))

        self._fill_rows(ws, rows_input)

        # 양식.xlsx 는 A3..A22 에 1..20 행번호를 미리 박아두었다.  데이터가
        # 그보다 적게 들어가면 ‘빈 행에 번호만 떠 있는’ 모양이 되어 혼란스러우니
        # 우리가 채우지 않은 모든 데이터 행의 A 컬럼을 비운다.
        data_end_row = DATA_START_ROW + len(rows_input) - 1
        for r in range(max(data_end_row + 1, DATA_START_ROW), ws.max_row + 1):
            a = ws.cell(row=r, column=1)
            if isinstance(a.value, (int, float)):
                a.value = None

        # 교차 검증 미탐 시트 ----------------------------------------
        if self._result.mode == "cross":
            if self._result.miss_fast:
                self._write_miss_sheet(
                    wb, i18n.KO.SHEET_MISS_FAST, self._result.miss_fast,
                )
            if self._result.miss_slow:
                self._write_miss_sheet(
                    wb, i18n.KO.SHEET_MISS_SLOW, self._result.miss_slow,
                )

        # Slot 불일치 ---------------------------------------------------
        if self._result.slot_only_ref or self._result.slot_only_val:
            self._write_slot_mismatch_sheet(wb)

        self._dst.parent.mkdir(parents=True, exist_ok=True)
        wb.save(str(self._dst))
        # SharePoint / MIP 메타데이터 제거 — 회사 Excel 에서 ‘읽기 전용’ /
        # ‘보호 보기’ 로 열리는 것을 방지.
        try:
            _strip_corporate_metadata(self._dst)
        except Exception:
            # 메타데이터 정리 실패는 치명적이지 않다 — 결과 파일은 이미 저장됨.
            pass

    # ------------------------------------------------------------------
    def _build_minimal_headers(self, ws) -> None:
        """양식.xlsx 가 없을 때 사용할 최소 헤더 (양식의 구조를 흉내)."""
        from openpyxl.styles import Alignment, Font, PatternFill
        yellow = PatternFill("solid", fgColor="FFFF00")
        center = Alignment(horizontal="center", vertical="center")
        ws["A1"] = "No"
        ws["B1"] = "slot#"
        ws["C1"] = "Scan Defect"
        ws.merge_cells("A1:A2")
        ws.merge_cells("B1:B2")
        ws.merge_cells("C1:D1")
        for coord in ("A1", "B1", "C1"):
            c = ws[coord]
            c.font = Font(bold=True)
            c.fill = yellow
            c.alignment = center
        # row 2 의 AOI-N 자리는 _do_export 에서 채움.
        for coord in ("C2", "D2"):
            c = ws[coord]
            c.font = Font(bold=True)
            c.fill = yellow
            c.alignment = center
        ws.row_dimensions[1].height = 21.75
        ws.row_dimensions[2].height = 19.5

    # ------------------------------------------------------------------
    def _fill_rows(self, ws, rows_input: list[tuple[str, str, object]]) -> None:
        from openpyxl.comments import Comment
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font

        total = len(rows_input)
        row = DATA_START_ROW
        red_font = Font(color="FFFF2D55", bold=True)
        center = Alignment(horizontal="center", vertical="center")
        # 템플릿 데이터 행의 ‘기준 높이’ 를 한 번만 측정 — 보통 165.75pt.
        # 양식이 없거나 데이터 행에 높이가 안 잡혀 있으면 ROW_HEIGHT_PT 사용.
        template_row_h = ws.row_dimensions[DATA_START_ROW].height or ROW_HEIGHT_PT
        for idx, (_slot, _key, payload) in enumerate(rows_input, start=1):
            # 새 행은 템플릿의 데이터 행과 같은 높이로 통일 → 양식 안팎 일관성.
            cur_h = ws.row_dimensions[row].height
            if not cur_h or cur_h < template_row_h:
                ws.row_dimensions[row].height = template_row_h

            # A 열: 행 번호 (사용자 양식의 ‘No’).
            no_cell = ws[f"{COL_NO}{row}"]
            no_cell.value = idx
            no_cell.alignment = center

            if isinstance(payload, MatchResult):
                m = payload
                ws[f"{COL_SLOT}{row}"] = m.slot
                ws[f"{COL_SLOT}{row}"].alignment = center
                ref_mid = image_io.get_mid_path(m.ref_path)
                val_mid = image_io.get_mid_path(m.val_path)
                xli_ref = XLImage(str(ref_mid))
                xli_val = XLImage(str(val_mid))
                _shrink(xli_ref, max_px=IMG_MAX_PX)
                _shrink(xli_val, max_px=IMG_MAX_PX)
                ws.add_image(xli_ref, f"{COL_REF}{row}")
                ws.add_image(xli_val, f"{COL_VAL}{row}")
                self.signals.progress.emit(idx, total, m.slot)
            else:
                u: MissEntry = payload
                ws[f"{COL_SLOT}{row}"] = u.slot
                ws[f"{COL_SLOT}{row}"].alignment = center
                # 기준 이미지: 정상 임베드.
                try:
                    ref_mid = image_io.get_mid_path(Path(u.path))
                    xli_ref = XLImage(str(ref_mid))
                    _shrink(xli_ref, max_px=IMG_MAX_PX)
                    ws.add_image(xli_ref, f"{COL_REF}{row}")
                except Exception:
                    ws[f"{COL_REF}{row}"] = str(Path(u.path).name)
                # 검증 컬럼에 파일명 텍스트 (빨강).
                cell_val = ws[f"{COL_VAL}{row}"]
                cell_val.value = Path(u.path).name
                cell_val.font = red_font
                cell_val.alignment = Alignment(
                    horizontal="center", vertical="center", wrap_text=True,
                )
                cell_val.comment = Comment("미매칭", "AOI")
                self.signals.progress.emit(idx, total, u.slot)

            row += 1

    # ------------------------------------------------------------------
    def _write_miss_sheet(self, wb, title: str, entries) -> None:
        from openpyxl.drawing.image import Image as XLImage
        ws = wb.create_sheet(title=title)
        ws["B1"] = "Slot"
        ws["C1"] = "이미지"
        ws["D1"] = "비고"
        ws.column_dimensions["B"].width = 14
        ws.column_dimensions["C"].width = IMG_COL_WIDTH
        ws.column_dimensions["D"].width = 30
        for i, e in enumerate(entries, start=2):
            ws.row_dimensions[i].height = ROW_HEIGHT_PT
            ws[f"B{i}"] = e.slot
            ws[f"D{i}"] = e.note or ""
            try:
                mid = image_io.get_mid_path(Path(e.path))
                xli = XLImage(str(mid))
                _shrink(xli, max_px=IMG_MAX_PX)
                ws.add_image(xli, f"C{i}")
            except Exception:
                ws[f"C{i}"] = str(e.path)

    def _write_slot_mismatch_sheet(self, wb) -> None:
        ws = wb.create_sheet(title=i18n.KO.SLOT_MISMATCH_SHEET)
        ws["A1"] = "구분"
        ws["B1"] = "Slot 명"
        r = 2
        for s in self._result.slot_only_ref:
            ws.cell(row=r, column=1, value="기준 전용")
            ws.cell(row=r, column=2, value=s)
            r += 1
        for s in self._result.slot_only_val:
            ws.cell(row=r, column=1, value="검증 전용")
            ws.cell(row=r, column=2, value=s)
            r += 1


def _shrink(xli, max_px: int) -> None:
    """openpyxl 의 Image 객체를 셀에 들어갈 만한 크기로 축소."""
    try:
        w = xli.width
        h = xli.height
    except Exception:
        return
    if max(w, h) <= max_px:
        return
    if w >= h:
        xli.width = max_px
        xli.height = int(h * max_px / w)
    else:
        xli.height = max_px
        xli.width = int(w * max_px / h)


# ---------------------------------------------------------------------------
# SharePoint / MIP 메타데이터 청소
# ---------------------------------------------------------------------------
# 양식.xlsx 가 SharePoint 에서 다운로드된 파일이라 다음 메타데이터를 가지고
# 있다 — 회사 Excel 이 이 파일을 ‘기밀/보호 보기/읽기 전용’ 으로 여는 원인:
#
#   - customXml/*               : SharePoint Media Service 메타데이터
#   - docMetadata/LabelInfo.xml : Microsoft Information Protection 라벨
#   - docProps/custom.xml       : ContentTypeId 등 SharePoint content type 바인딩
#
# 저장 직후 zip 안에서 이 항목들을 제거하고, 참조하는 [Content_Types].xml /
# _rels 파일에서도 해당 라인을 삭제한다.
import re as _re
import zipfile as _zip

_STRIP_PREFIXES = ("customXml/", "docMetadata/")
_STRIP_REL_TARGETS = _re.compile(
    r'(?i)Target="(?:[^"]*/)?(?:customXml|docMetadata)[^"]*"'
)
_STRIP_CONTENT_TYPE_OVERRIDES = _re.compile(
    r'<Override[^>]*PartName="/(?:customXml|docMetadata)[^"]*"[^>]*/>'
)
_STRIP_RELATIONSHIP_LINE = _re.compile(
    r'<Relationship[^/]*?Target="(?:[^"]*/)?(?:customXml|docMetadata)[^"]*"[^/]*?/>'
)


def _strip_corporate_metadata(xlsx_path: Path) -> None:
    """저장된 xlsx 에서 SharePoint / MIP 메타데이터를 제거한다.

    실패해도 결과 파일 자체는 손상되지 않도록 임시 파일에 다시 쓴 뒤 atomic
    rename 으로 교체한다.
    """
    xlsx_path = Path(xlsx_path)
    tmp_out = xlsx_path.with_suffix(xlsx_path.suffix + ".clean.tmp")

    with _zip.ZipFile(xlsx_path, "r") as src:
        names = src.namelist()
        has_metadata = any(
            n.startswith(_STRIP_PREFIXES) for n in names
        )
        if not has_metadata:
            return        # 청소할 게 없으면 그대로 둠

        with _zip.ZipFile(tmp_out, "w", _zip.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                name = info.filename
                if name.startswith(_STRIP_PREFIXES):
                    continue                # 메타데이터 파일은 통째로 제외
                data = src.read(name)
                # 참조 라인 제거 — text XML 만 정리
                if name in (
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "xl/_rels/workbook.xml.rels",
                ):
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        dst.writestr(info, data)
                        continue
                    text = _STRIP_CONTENT_TYPE_OVERRIDES.sub("", text)
                    text = _STRIP_RELATIONSHIP_LINE.sub("", text)
                    data = text.encode("utf-8")
                elif name == "docProps/custom.xml":
                    # ContentTypeId 만 가진 custom.xml 은 통째로 비워도 무방.
                    # Excel 이 ContentTypeId 를 보면 SharePoint 문서로 인식.
                    try:
                        text = data.decode("utf-8")
                        if 'name="ContentTypeId"' in text:
                            # 빈 properties 로 대체.
                            data = (
                                b'<?xml version="1.0" encoding="UTF-8" '
                                b'standalone="yes"?>\n'
                                b'<Properties xmlns='
                                b'"http://schemas.openxmlformats.org/'
                                b'officeDocument/2006/custom-properties"/>'
                            )
                    except UnicodeDecodeError:
                        pass
                dst.writestr(info, data)

    tmp_out.replace(xlsx_path)
