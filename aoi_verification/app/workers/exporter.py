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
# Fallback layout (양식이 없거나 헤더 자동 감지가 실패할 때 적용)
# ---------------------------------------------------------------------------
COL_SLOT = "B"
COL_REF = "C"
COL_VAL = "D"
COL_DIR = "E"
HEADER_ROW = 1
DATA_START_ROW = 2
# 셀 ↔ 사진 크기 정합 (사용자 요청):
#   · 사진을 작게 두어 프린트 / 부분 확대 모두에 유리하게.
#   · 1 column width unit ≈ 7 px (Calibri 11pt) → 22 chars ≈ 154 px
#   · 1 row height pt ≈ 1.333 px → 115 pt ≈ 153 px
#   · 이미지 max 150 px → 양쪽 모두 약간의 여백을 두고 정사각형 영역에 안착.
ROW_HEIGHT_PT = 115
IMG_COL_WIDTH = 22
IMG_MAX_PX = 150             # 셀 안에 들어갈 사진의 최대 변 길이


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
        from openpyxl.comments import Comment
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        # 항상 깨끗한 새 워크북 생성 — 회사 SharePoint 출신 ‘양식.xlsx’ 가
        # (a) MIP/SharePoint 메타데이터로 ‘읽기 전용’ 으로 열리고,
        # (b) 병합 셀 + 우리 스키마와 다른 컬럼 구조라 직접 채울 수 없는 문제를
        # 한 번에 해결.  사용자가 보던 Slot/기준/검증/(방향) 헤더는 그대로.
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
            cell.font = Font(bold=True, color="FFFFFFFF")
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

        # 매칭/미매칭 통합 정렬 → Slot 오름차순, 그 안에서 기준 파일명 오름차순.
        # 미매칭 reference 도 같은 정렬 키로 끼워넣어 ‘기준 사진 순서’ 가 유지되게.
        rows_input: list[tuple[str, str, object]] = []
        for m in self._result.matches:
            rows_input.append((m.slot, str(m.ref_path.name).lower(), m))
        for u in self._result.unmatched_refs:
            rows_input.append((u.slot, str(u.path.name).lower(), u))
        rows_input.sort(key=lambda x: (x[0], x[1]))

        total = len(rows_input)
        row = data_start
        red_font = Font(color="FFFF2D55", bold=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="aoi_export_"))
        try:
            for idx, (_slot, _key, payload) in enumerate(rows_input, start=1):
                # 양식이 정한 행 높이가 너무 작을 때만 키운다.
                cur_h = ws.row_dimensions[row].height
                if not cur_h or cur_h < ROW_HEIGHT_PT:
                    ws.row_dimensions[row].height = ROW_HEIGHT_PT

                if isinstance(payload, MatchResult):
                    m = payload
                    ws[f"{col_slot}{row}"] = m.slot
                    if self._result.mode == "cross":
                        ws[f"{col_dir}{row}"] = m.direction

                    ref_mid = image_io.get_mid_path(m.ref_path)
                    val_mid = image_io.get_mid_path(m.val_path)
                    xli_ref = XLImage(str(ref_mid))
                    xli_val = XLImage(str(val_mid))
                    _shrink(xli_ref, max_px=IMG_MAX_PX)
                    _shrink(xli_val, max_px=IMG_MAX_PX)
                    ws.add_image(xli_ref, f"{col_ref}{row}")
                    ws.add_image(xli_val, f"{col_val}{row}")
                    self.signals.progress.emit(idx, total, m.slot)
                else:
                    u: MissEntry = payload
                    ws[f"{col_slot}{row}"] = u.slot
                    # 기준 이미지: 정상 임베드.
                    try:
                        ref_mid = image_io.get_mid_path(Path(u.path))
                        xli_ref = XLImage(str(ref_mid))
                        _shrink(xli_ref, max_px=IMG_MAX_PX)
                        ws.add_image(xli_ref, f"{col_ref}{row}")
                    except Exception:
                        ws[f"{col_ref}{row}"] = str(Path(u.path).name)
                    # 검증 컬럼에 파일명 텍스트 (빨강).
                    cell_val = ws[f"{col_val}{row}"]
                    cell_val.value = Path(u.path).name
                    cell_val.font = red_font
                    cell_val.alignment = Alignment(
                        horizontal="center", vertical="center", wrap_text=True,
                    )
                    cell_val.comment = Comment("미매칭", "AOI")
                    if self._result.mode == "cross":
                        ws[f"{col_dir}{row}"] = "미매칭"
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
            # SharePoint / MIP 메타데이터 제거 — 회사 Excel 에서 ‘읽기 전용’
            # / ‘보호 보기’ 로 열리는 것을 방지 (양식.xlsx 가 SharePoint
            # 출신이라 본 메타데이터가 결과 파일에 상속되는 문제).
            try:
                _strip_corporate_metadata(self._dst)
            except Exception:
                # 메타데이터 정리 실패는 치명적이지 않다 — 결과 파일은 이미 저장됨.
                pass
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
