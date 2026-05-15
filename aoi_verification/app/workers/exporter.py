"""엑셀(`양식.xlsx`) 출력 워커.

- 양식.xlsx 를 템플릿으로 로드한다. 없으면 빈 워크북에 헤더만 적는다.
- B = Slot 명 / C = 기준 이미지 / D = 검증 이미지
- 교차 검증 모드에서는 E = 매칭 방향, 추가 시트(미탐, Slot 불일치) 작성.
- 임베드 이미지는 800px(JPEG q85) 중간 크기로 변환되어 임시 파일을 통해 삽입.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .. import i18n
from ..models.result import FinalResult, MatchResult, MissEntry
from ..utils import image_io, paths


# ---------------------------------------------------------------------------
# Layout constants — keep in sync with the customer's 양식.xlsx
# ---------------------------------------------------------------------------
COL_SLOT = "B"
COL_REF = "C"
COL_VAL = "D"
COL_DIR = "E"
HEADER_ROW = 1
DATA_START_ROW = 2
ROW_HEIGHT_PT = 120          # 약 800px 사진이 들어갈 행 높이
IMG_COL_WIDTH = 28           # 컬럼 너비 (문자 단위, openpyxl)


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
        from openpyxl.comments import Comment
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        # 템플릿 확보 ---------------------------------------------------
        template = self._template or paths.resource_path("양식.xlsx")
        if template and Path(template).exists():
            wb = load_workbook(str(template))
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "AOI 검증 결과"
            ws[f"{COL_SLOT}{HEADER_ROW}"] = "Slot"
            ws[f"{COL_REF}{HEADER_ROW}"] = "기준 장비 이미지"
            ws[f"{COL_VAL}{HEADER_ROW}"] = "검증 장비 이미지"
            if self._result.mode == "cross":
                ws[f"{COL_DIR}{HEADER_ROW}"] = "매칭 방향"
            for c in (COL_SLOT, COL_REF, COL_VAL, COL_DIR):
                cell = ws[f"{c}{HEADER_ROW}"]
                cell.font = Font(bold=True)
                cell.fill = PatternFill("solid", fgColor="111827")
                cell.alignment = Alignment(horizontal="center", vertical="center")

        # 컬럼 폭/행 높이 조정 ----------------------------------------------
        for col in (COL_REF, COL_VAL):
            ws.column_dimensions[col].width = IMG_COL_WIDTH
        ws.column_dimensions[COL_SLOT].width = 14
        if self._result.mode == "cross":
            ws.column_dimensions[COL_DIR].width = 14

        # 매칭/미매칭 통합 정렬 → Slot 오름차순, 그 안에서 기준 파일명 오름차순.
        # 미매칭 reference 도 같은 정렬 키로 끼워넣어 ‘기준 사진 순서’ 가 유지되게.
        rows_input: list[tuple[str, str, object]] = []
        for m in self._result.matches:
            rows_input.append((m.slot, str(m.ref_path.name).lower(), m))
        for u in self._result.unmatched_refs:
            rows_input.append((u.slot, str(u.path.name).lower(), u))
        rows_input.sort(key=lambda x: (x[0], x[1]))

        total = len(rows_input)
        row = DATA_START_ROW
        red_font = Font(color="FFFF2D55", bold=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="aoi_export_"))
        try:
            for idx, (_slot, _key, payload) in enumerate(rows_input, start=1):
                ws.row_dimensions[row].height = ROW_HEIGHT_PT

                if isinstance(payload, MatchResult):
                    m = payload
                    ws[f"{COL_SLOT}{row}"] = m.slot
                    if self._result.mode == "cross":
                        ws[f"{COL_DIR}{row}"] = m.direction

                    ref_mid = image_io.get_mid_path(m.ref_path)
                    val_mid = image_io.get_mid_path(m.val_path)
                    xli_ref = XLImage(str(ref_mid))
                    xli_val = XLImage(str(val_mid))
                    _shrink(xli_ref, max_px=560)
                    _shrink(xli_val, max_px=560)
                    ws.add_image(xli_ref, f"{COL_REF}{row}")
                    ws.add_image(xli_val, f"{COL_VAL}{row}")
                    self.signals.progress.emit(idx, total, m.slot)
                else:
                    u: MissEntry = payload
                    ws[f"{COL_SLOT}{row}"] = u.slot
                    # 기준 이미지: 정상 임베드.
                    try:
                        ref_mid = image_io.get_mid_path(Path(u.path))
                        xli_ref = XLImage(str(ref_mid))
                        _shrink(xli_ref, max_px=560)
                        ws.add_image(xli_ref, f"{COL_REF}{row}")
                    except Exception:
                        ws[f"{COL_REF}{row}"] = str(Path(u.path).name)
                    # D 열에 파일명 텍스트 (빨강).
                    cell_val = ws[f"{COL_VAL}{row}"]
                    cell_val.value = Path(u.path).name
                    cell_val.font = red_font
                    cell_val.alignment = Alignment(
                        horizontal="center", vertical="center", wrap_text=True,
                    )
                    cell_val.comment = Comment("미매칭", "AOI")
                    if self._result.mode == "cross":
                        ws[f"{COL_DIR}{row}"] = "미매칭"
                    self.signals.progress.emit(idx, total, u.slot)

                row += 1

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
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

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
                _shrink(xli, max_px=560)
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
