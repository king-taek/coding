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
from ..models.result import FinalResult
from ..utils import image_io, paths


# ---------------------------------------------------------------------------
# Fallback layout (양식이 없거나 헤더 자동 감지가 실패할 때 적용)
# ---------------------------------------------------------------------------
COL_SLOT = "B"
COL_REF = "C"
COL_VAL = "D"
COL_DIR = "E"
HEADER_ROW = 1
DATA_START_ROW = 2
ROW_HEIGHT_PT = 120          # 약 800px 사진이 들어갈 행 높이
IMG_COL_WIDTH = 28           # 컬럼 너비 (문자 단위, openpyxl)


# ---------------------------------------------------------------------------
# 헤더 키워드 → 어느 컬럼이 무엇인지 자동 감지 (#1 — 사용자 양식 호환)
# ---------------------------------------------------------------------------
_KEYWORDS = {
    "slot": ("slot", "슬롯"),
    "ref":  ("기준", "ref", "reference"),
    "val":  ("검증", "val", "validation", "타겟", "target"),
    "dir":  ("방향", "direction", "매칭 방향"),
}


def _detect_layout(ws) -> dict:
    """``양식`` 워크시트의 1~5 행을 훑어 헤더 위치를 자동 감지.

    전략: 각 행마다 키워드를 몇 개나 찾았는지 점수를 매겨, **가장 점수가 높은
    행 하나를 헤더로 확정**. 페이지 제목 같은 단일 셀이 키워드를 포함해도
    헤더 행과 헷갈리지 않는다.
    """
    layout = {
        "slot": COL_SLOT, "ref": COL_REF, "val": COL_VAL, "dir": COL_DIR,
        "header_row": HEADER_ROW, "data_start_row": DATA_START_ROW,
    }
    max_r = min(5, ws.max_row or 1)
    max_c = min(15, ws.max_column or 1)

    candidates: list[tuple[int, int, dict]] = []     # (score, row, mapping)
    for r in range(1, max_r + 1):
        found: dict[str, str | None] = {
            "slot": None, "ref": None, "val": None, "dir": None,
        }
        for c in range(1, max_c + 1):
            cell = ws.cell(row=r, column=c)
            if cell.value is None:
                continue
            text = str(cell.value).strip().lower()
            if not text or len(text) > 25:        # 너무 긴 문장은 헤더 아님
                continue
            for key, kws in _KEYWORDS.items():
                if found[key] is not None:
                    continue
                if any(kw.lower() in text for kw in kws):
                    found[key] = cell.column_letter
                    break
        score = sum(1 for v in found.values() if v)
        if score > 0:
            candidates.append((score, r, found))

    if not candidates:
        return layout
    # 동점이면 더 위쪽 행을 선호 (-row 가 큰 쪽)
    best_score, best_row, best_found = max(candidates, key=lambda x: (x[0], -x[1]))
    # 단일 키워드만 잡힌 행은 페이지 제목일 가능성이 높음 — fallback 유지
    if best_score < 2:
        return layout
    layout["header_row"] = best_row
    layout["data_start_row"] = best_row + 1
    for k, v in best_found.items():
        if v:
            layout[k] = v
    return layout


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
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        # 템플릿 확보 — 양식.xlsx 가 있으면 그대로 열고, 없으면 기본 헤더 ----
        template = self._template or paths.resource_path("양식.xlsx")
        if template and Path(template).exists():
            wb = load_workbook(str(template))
            ws = wb.active
            layout = _detect_layout(ws)
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
            layout = {
                "slot": COL_SLOT, "ref": COL_REF, "val": COL_VAL, "dir": COL_DIR,
                "header_row": HEADER_ROW, "data_start_row": DATA_START_ROW,
            }

        col_slot, col_ref, col_val, col_dir = (
            layout["slot"], layout["ref"], layout["val"], layout["dir"],
        )
        data_start = int(layout["data_start_row"])

        # 컬럼 폭/행 높이 조정 — 양식이 이미 너비를 지정해 두었다면 그 값 보존
        def _ensure_width(col_letter: str, min_w: float) -> None:
            cur = ws.column_dimensions[col_letter].width
            if not cur or cur < min_w:
                ws.column_dimensions[col_letter].width = min_w
        _ensure_width(col_ref, IMG_COL_WIDTH)
        _ensure_width(col_val, IMG_COL_WIDTH)
        _ensure_width(col_slot, 14)
        if self._result.mode == "cross":
            _ensure_width(col_dir, 14)

        # 매칭 결과 정렬 → Slot 오름차순, slot 내 입력 순서
        matches = sorted(
            self._result.matches,
            key=lambda m: (m.slot, str(m.ref_path).lower()),
        )

        total = len(matches)
        row = data_start
        tmp_dir = Path(tempfile.mkdtemp(prefix="aoi_export_"))
        try:
            for idx, m in enumerate(matches, start=1):
                # 양식이 정한 행 높이가 너무 작을 때만 키운다.
                cur_h = ws.row_dimensions[row].height
                if not cur_h or cur_h < ROW_HEIGHT_PT:
                    ws.row_dimensions[row].height = ROW_HEIGHT_PT
                ws[f"{col_slot}{row}"] = m.slot
                if self._result.mode == "cross":
                    ws[f"{col_dir}{row}"] = m.direction

                ref_mid = image_io.get_mid_path(m.ref_path)
                val_mid = image_io.get_mid_path(m.val_path)

                xli_ref = XLImage(str(ref_mid))
                xli_val = XLImage(str(val_mid))
                _shrink(xli_ref, max_px=560)
                _shrink(xli_val, max_px=560)
                ws.add_image(xli_ref, f"{col_ref}{row}")
                ws.add_image(xli_val, f"{col_val}{row}")

                row += 1
                self.signals.progress.emit(idx, total, m.slot)

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
