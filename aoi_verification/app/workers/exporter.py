"""엑셀(`양식.xlsx`) 출력 워커.

- 양식.xlsx 를 그대로 템플릿으로 로드해서 셀 서식/병합/열폭/행높이를 보존한다.
- 헤더(C2/D2)의 ‘AOI-N’ 만 검증 세션의 호기 번호로 교체.
- 데이터: A=번호, B=Slot, C=기준(낮은 호기) 이미지, D=검증(높은 호기) 이미지.
  매치 행은 두 사진 칸의 **셀 값**으로도 캡션(파일명 + 계측·좌표)을 적는다 — 미매칭
  행이 D열에 적던 것과 같은 정보다.  평소에는 사진이 그 위를 덮어 보이지 않고, 필요할
  때 엑셀에서 사진을 치우면 드러난다(사용자 요청: 특별한 경우가 아니면 볼 일이 없다).
- E~H 컬럼(Escape Defect Camtek/KLA)은 사용자가 수기로 채울 영역이라 비워 둔다.
- ‘매칭 방향’ 컬럼은 사용자 요청으로 더 이상 쓰지 않는다 (#4).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .. import i18n
from ..models.result import (EXTRACT_MODE, REREVIEW_MODE, VERDICT_REJECT, FinalResult,
                             MatchResult, MissEntry)
from ..models.slot import lot_of, slot_label
from ..utils import image_io

# 저장 단계별 소요 시간(app.log) — 느린 단계를 실측으로 가리기 위해.  화면에는 안 나간다.
_LOG = logging.getLogger("aoi.export")


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
# 시트 분리 (#사용자 요청): 1번째=요약(A~D, 파일명), 2번째=전체 양식(E~H 포함).
SHEET_FULL_NAME = "전체 양식"
# 슬롯 구분선이 그려지는 컬럼 — 사용자 요청 (#6): A~D 만, 두껍게.
BORDER_COLS = ["A", "B", "C", "D"]

# Scan image 열 — 이번 저장의 사진 중 하나라도 Scan 이 확인될 때만 생긴다(사용자 결정:
# '묶어서 판단').  기존 열 뒤에 붙인다: 매칭은 E=기준 Scan·F=검증 Scan, 추출은 E=Scan.
# 전체 양식의 수기 칸(E~H)은 그만큼 오른쪽으로 밀린다.  Scan 이 없는 저장은 지금과
# 똑같은 열 구성이다(양식.xlsx 도 그대로 — 열은 저장할 때 끼운다).
SCAN_COL_START = 5                       # E
SCAN_JPEG_QUALITY = 92

# 카세트 슬롯 번호(`WaferInfo.ini` 의 `ActiveSlot`) 표기 — slot명 **아래 줄**에 붙는다.
# 요약/미매칭 시트의 B열과 Wafer Map 시트의 슬롯 칸이 같은 표기를 쓴다(두 벌로 갈라지면
# 같은 슬롯이 시트마다 다르게 적힌다).
SLOT_NUMBER_FMT = "(#{num})"

# Defect 추출의 Recipe 나누기(사용자 결정 — 저장 직전 요약 창에서 고른다).
#   single  : 시트 1개, 세로(기본 — 예전 그대로)
#   sheets  : Recipe 마다 시트 1개(시트 안 구성은 single 과 같다)
#   columns : 시트 1개, Recipe 마다 [사진 | 정보] 열을 옆으로 나란히
RECIPE_LAYOUT_SINGLE = "single"
RECIPE_LAYOUT_SHEETS = "sheets"
RECIPE_LAYOUT_COLUMNS = "columns"

# 셀 ↔ 사진 크기 정합:
#   · 양식.xlsx 의 데이터 행 높이 (165.75pt) 와 일치시켜 템플릿 안팎의 행 높이를
#     맞춘다. 양식이 없을 때(폴백) 도 동일 값으로 통일.
#   · 이미지 max 변 = 150 px. 1pt ≈ 1.333 px 이므로 165pt ≈ 220 px → 150 px
#     이미지가 셀 안에 여유 있게 들어간다.
ROW_HEIGHT_PT = 165.75
IMG_COL_WIDTH = 22

# ---------------------------------------------------------------------------
# 양식 서식 — 치수·색의 **단일 출처**
# ---------------------------------------------------------------------------
# ★ 여기가 원본이고 `dev/양식.xlsx` 는 `scripts/internal/make_template.py` 가
#   이 값들로 구워낸 산출물이다.  두 벌로 갈라지면 '빈 양식' 과 '실제 출력' 의
#   생김새가 달라지므로, 서식을 바꿀 땐 여기를 고치고 그 스크립트를 다시 돌린다.
TEMPLATE_FONT = "맑은 고딕"

# 열 폭 — 사진 열(C·D)은 현행 유지, 수기 열(E~H)과 A 는 좁혔다(사용자 지정).
#   A 를 좁힌 이유: 'No' 는 3자리인데 사진 열만큼 자리를 먹어 가로가 불필요하게 길었다.
#   ★ B(slot#)는 9.5 였다가 15 로 넓혔다 — slot 명이 짧다는 전제가 틀렸다.  실물
#     slot 명은 WaferID 12자(`A1033ABQEWG3`·`25195007EWF6`)라 9.5 에서는 뒤가 잘려
#     보였다(사용자 지적).  폭 단위는 기준 글꼴 '0' 자 기준이고 대문자가 그보다
#     넓으므로 12자 + 여유 → 15.
COL_WIDTHS = {"A": 4.5, "B": 15, "C": 31.8, "D": 31.8,
              "E": 13, "F": 13, "G": 13, "H": 13}

# 헤더 1행 — 진한 남색 띠(흰 글씨).  표 상단이 하나로 읽힌다.
HEADER_NAVY = "FF44546A"
# 헤더 2행 — 그룹별 색.  가로로 넓은 표에서 지금 어느 그룹 칸인지 구분된다.
GROUP_FILLS = {"C": "FFBDD7EE", "D": "FFBDD7EE",     # Scan Defect — 파랑
               "E": "FFC6E0B4", "F": "FFC6E0B4",     # Camtek      — 초록
               "G": "FFFBE2D5", "H": "FFFBE2D5"}     # KLA         — 주황
# 데이터 영역 배경 (짝수행, 홀수행) — 줄무늬로 행을 따라가기 쉽게 하고,
# 수기 열은 그룹색을 옅게 깔아 세로로도 그룹이 이어지게 한다.
BODY_FILLS = {"A": ("FFFFFFFF", "FFF2F5F9"), "B": ("FFFFFFFF", "FFF2F5F9"),
              "C": ("FFFFFFFF", "FFF2F5F9"), "D": ("FFFFFFFF", "FFF2F5F9"),
              "E": ("FFF4F9EF", "FFEDF4E5"), "F": ("FFF4F9EF", "FFEDF4E5"),
              "G": ("FFFDF6F1", "FFFBEFE7"), "H": ("FFFDF6F1", "FFFBEFE7")}
BODY_GRID_COLOR = "FFB0B0B0"     # 데이터 영역 얇은 격자
# 미매칭 행 배경 — 줄무늬 대신 이 색으로 덮는다(구조개편 28안 ①).
# ★ 왜 필요한가.  요약 시트는 매치와 미매칭이 **한 표에 섞여** 슬롯·파일명 순으로
#   정렬된다.  지금까지 미매칭 표시는 D열 빨간 글씨와 셀 메모뿐이라, '총 몇 건 중
#   몇 건이 미매칭인지' 를 사람이 한 줄씩 세어야 했다.  행이 물들면 눈으로 세어지고,
#   **흑백 인쇄에서도** 명도 차로 구분된다(빨간 글씨는 흑백에서 사라진다).
# ★ 기존 KLA 그룹색(FFFBE2D5) 계열의 옅은 주황 — 표에 이미 있는 색어휘를 쓴다.
UNMATCHED_FILL = "FFFDF1EA"
# 머리 2행은 표의 이름표다 — 스크롤하면 사라지고, 인쇄하면 첫 장에만 있었다.
FREEZE_AT = "A3"                 # DATA_START_ROW 바로 위까지 얼린다(28안 ③)
PRINT_TITLE_ROWS = "1:2"         # 인쇄 매 장에 머리 행 반복(28안 ④)


def _machine_label(raw: str) -> str:
    """호기 입력을 엑셀 헤더 라벨로 정규화.

    - 순수 숫자(``2``) 또는 ``N호기``(``2호기``, 공백 허용) → ``AOI-N``
    - 그 외 문자가 포함되면(``K-2`` 등) → ``AOI(원본값)``
    - 빈 입력 → ``""``
    """
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.fullmatch(r"(\d+)(\s*호기)?", s)
    if m:
        return f"AOI-{m.group(1)}"
    return f"AOI({s})"


class _Cancelled(Exception):
    """저장 취소 — 실패가 아니므로 사용자에게 오류로 보고하지 않는다."""


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
                 include_full_template: bool = False,
                 original_quality: bool = False,
                 unmatched_original_quality: bool = False,
                 map_renderer=None,
                 recipe_layout: str = RECIPE_LAYOUT_SINGLE,
                 reject_map_renderer=None,
                 include_good: bool = False,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._result = result
        # AVAGO 재리뷰 — 행은 재리뷰한 사진 전부(추출과 같은 C=사진·D=정보 모양)에 판정·die
        # 열을 덧붙이고, Reject die 맵 시트를 만든다(요약 시트는 두지 않는다 — 사용자 결정).
        self._rereview = result.mode == REREVIEW_MODE
        # ``(RejectMap, 신규 Reject 칸, size_px) -> PNG`` — UI 계층이 넘긴다(맵 렌더러와 같은 이유).
        self._reject_map_renderer = reject_map_renderer
        # 재리뷰 — Good 사진까지 '전체' 시트로 넣을지(끄면 Reject 시트만, 사용자 결정).
        self._include_good = bool(include_good)
        # Defect 추출 — 행이 전부 '고른 사진'(unmatched_refs)이다.  미매칭 행과 같은 모양
        # (C=사진, D=파일명·계측·좌표 글자)으로 적되 '미매칭' 표시(행 틴트·메모·시트)는
        # 붙이지 않는다 — 매칭을 하지 않았으므로 미매칭도 아니다.
        self._extract = result.mode == EXTRACT_MODE
        # 한쪽 장비 사진만 싣는 모드 — 행이 '미매칭' 이 아니다(틴트·메모를 붙이지 않는다).
        self._one_side = self._extract or self._rereview
        # Recipe 나누기는 추출에만 있다 — 매칭 결과는 언제나 예전 배치다.
        self._recipe_layout = (recipe_layout if self._extract
                               else RECIPE_LAYOUT_SINGLE)
        # Wafer map PNG 렌더러 ``(MapData, size_px) -> PNG bytes``.  UI 계층이 넘긴다
        # (workers 는 ui 를 import 하지 않는다).  None 이면 Wafer Map 시트를 만들지 않는다.
        self._map_renderer = map_renderer
        self._dst = Path(dst_path)
        self._template = Path(template_path) if template_path else None
        # 전체 양식(E~H 수기 영역 포함) 시트 생성 여부 — 기본 off(가볍고 빠른 출력).
        self._include_full_template = (bool(include_full_template)
                                       and not self._rereview)
        # 사진을 원본 화질로 임베드할지 — 기본 off(중간 화질 캐시로 가볍게).
        self._original_quality = bool(original_quality)
        # 미매칭 사진만 원본 화질로 — 전체 원본 옵션이 켜져 있으면 어차피 전부 원본.
        self._unmatched_original = bool(unmatched_original_quality)
        self.signals = ExporterSignals()
        # 진행률은 **저장 전체 기준**으로 단조 증가해야 한다 (CLAUDE.md 로딩 계약).
        # `_fill_rows` 는 시트마다 불리는데 시트별로 1..N 을 새로 세면 바가 매번
        # 0 으로 돌아간다 — LoadingOverlay 는 값이 줄면 tween 없이 즉시 스냅하므로
        # 사용자에겐 '다 찼다가 처음부터 다시' 로 보인다(실측: 미매칭 있는 결과에
        # 전체 양식까지 켜면 세 번 반복).  `_do_export` 가 총량을 미리 정하고
        # `_fill_rows` 는 여기에 이어서 센다.
        self._prog_done = 0
        self._prog_total = 0
        # ★ 앱을 닫는데 저장이 돌고 있으면, 기다리기만 해서는 반쯤 쓰인 xlsx 가 남는다
        #   (openpyxl 의 save 는 원자적이지 않다).  중간에 빠져나올 수 있게 한다.
        self._stop = threading.Event()
        # Scan image — `_scan_prepass` 가 채운다.  비어 있으면 Scan 열이 없는 예전 배치.
        self._scan_cols: list[str] = []
        self._scan_cache: dict[str, tuple[str, Optional[bytes]]] = {}

    def stop(self) -> None:
        """진행 중인 저장을 취소한다 — 파일을 쓰기 **전에** 멈춘다."""
        self._stop.set()

    def is_cancelled(self) -> bool:
        return self._stop.is_set()

    # ------------------------------------------------------------------
    def run(self) -> None:        # type: ignore[override]
        try:
            self._do_export()
        except _Cancelled:
            return                       # 사용자가 앱을 닫았다 — 조용히 끝낸다
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.done.emit(str(self._dst))

    # ------------------------------------------------------------------
    def _do_export(self) -> None:
        from openpyxl import Workbook, load_workbook

        t_start = time.perf_counter()

        # 양식이 있으면 그대로 로드해서 셀 서식을 모두 보존.
        # 없으면 동일 컬럼 구조의 워크북을 빈 상태로 만든다.
        if self._template is not None and self._template.exists():
            wb = load_workbook(str(self._template))
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = "AOI 레시피 검증 결과"
            self._build_minimal_headers(ws)

        # 시트는 둘로 나눈다 (#사용자 요청):
        #   · 1번째 시트 = 결과 파일명과 같은 이름, A~D 열만(요약).
        #   · 2번째 시트 = 기존 양식 그대로(전체 — E~H 수기 영역 포함), 이름 '전체 양식'.
        # 구현: 템플릿(현재 ws)을 채워 '전체 양식' 으로 두고, 그 시트를 복제해 E~H 를
        #       지운 요약 시트를 앞쪽에 만든다(이미지/서식 보존을 위해 채운 뒤 복제).
        ws.title = SHEET_FULL_NAME

        # row 2 의 ‘AOI-N’ 헤더를 실제 호기 번호로 교체 (#3).
        # 재리뷰는 호기 대신 사용자가 적은 LOT명(S/M)을 그대로 쓴다.
        ref_label = (self._result.ref_machine if self._rereview
                     else _machine_label(self._result.ref_machine))
        val_label = _machine_label(self._result.val_machine)
        if ref_label:
            ws[f"{COL_REF}{HEADER_AOI_ROW}"] = ref_label
        if val_label:
            ws[f"{COL_VAL}{HEADER_AOI_ROW}"] = val_label
        if self._one_side:
            # 추출은 장비가 하나다 — D열은 두 번째 장비가 아니라 C열 사진의 정보 칸.
            ws[f"{COL_VAL}{HEADER_AOI_ROW}"] = i18n.KO.EXTRACT_INFO_HEADER

        # 컬럼 폭 보정 — 양식.xlsx 는 ‘Scan Defect (C1:D1)’ 같은 병합 헤더의
        # 왼쪽 셀에만 width 를 지정해 두어, 오른쪽 셀(D, F, H 등) 이 기본 폭
        # (~8) 으로 떨어져 사진이 작아 보이는 문제가 있다.  병합된 헤더 쌍의
        # 왼쪽 컬럼 width 를 오른쪽 컬럼에도 그대로 미러링한다.
        self._mirror_paired_column_widths(ws)
        # ★ 폭은 **양식이 정한 값을 그대로 쓴다** (`COL_WIDTHS`).
        #   예전엔 C~H 를 전부 같은 폭으로 통일하고 A·B 에 하한(6·14)을 걸었는데,
        #   그러면 좁게 잡은 수기 열(E~H=13)과 A(4.5)·B(9.5)가 매번 되돌려진다 —
        #   양식을 아무리 고쳐도 출력이 안 바뀌는 형태였다.
        #   사진이 들어가는 C·D 만 서로 같아야 하므로 그 둘만 맞춘다.
        self._equalize_column_group(ws, [COL_REF, COL_VAL],
                                    floor=IMG_COL_WIDTH)
        for _col, _w in COL_WIDTHS.items():
            if _col not in (COL_REF, COL_VAL):
                ws.column_dimensions[_col].width = _w
        # 머리 2행 고정·반복 — 채우든 지우든(옵션 off) 상관없이 여기서 한 번.
        self._apply_sheet_view(ws)

        # 매칭/미매칭 통합 정렬 → Slot 오름차순, 그 안에서 기준 파일명 오름차순.
        rows_input: list[tuple[str, str, object]] = []
        for m in self._result.matches:
            rows_input.append((m.slot, str(m.ref_path.name).lower(), m))
        for u in self._result.unmatched_refs:
            rows_input.append((u.slot, str(u.path.name).lower(), u))
        if self._rereview:
            # 재리뷰는 결과가 정한 순서(웨이퍼 → Reject 먼저 → 파일명)를 그대로 쓴다.
            rows_input.sort(key=lambda x: x[0])
        else:
            rows_input.sort(key=lambda x: (x[0], x[1]))

        # 진행률 총량 = 이번 저장이 채울 **모든 시트의 행 수 합** (아래 채우는
        # 순서와 같은 순서로 더한다).  시트가 몇 장이든 바는 0 → 100 을 한 번만
        # 지난다.
        unmatched_rows = ([] if self._one_side else
                          [r for r in rows_input if isinstance(r[2], MissEntry)])
        self._prog_done = 0
        map_rows = [] if self._rereview else self._wafer_map_rows()
        rr_map_rows = (sorted(s for s, p in self._result.rereview.items() if not p.no_map)
                       if self._rereview and self._reject_map_renderer else [])
        prewarm = ([u.path for u in self._result.unmatched_refs]
                   if self._one_side else [])
        sheet_rows = len(rows_input)
        if self._rereview:
            # 시트: Reject → (Good 포함이면) 전체 → Wafer Map (사용자 지정 순서).
            rr_reject_rows = [r for r in rows_input if r[2].note == VERDICT_REJECT]
            rr_all_rows = rows_input if self._include_good else []
            sheet_rows = len(rr_reject_rows) + len(rr_all_rows)
            prewarm = [r[2].path for r in (rr_all_rows or rr_reject_rows)]
        scan_paths = self._scan_paths()
        self._prog_total = (
            len(scan_paths)                                          # Scan 확인
            + len(prewarm)                                           # 사진 준비(추출)
            + (len(rows_input) if self._include_full_template else 0)   # 전체 양식
            + len(unmatched_rows)                                    # 미매칭 시트
            + sheet_rows                                             # 요약 시트(들)
            + len(map_rows)                                          # Wafer map 시트
            + len(rr_map_rows)                                       # Reject die 맵
        )
        self._scan_prepass(scan_paths)
        if self._scan_cols:
            self._insert_scan_columns(ws)
        if prewarm:
            self._prewarm_images(prewarm)

        # 전체 양식(E~H 포함) 시트는 옵션 — 기본 off 면 이미지 임베드를 1회만 하게
        # 요약 시트만 채운다(더 빠르고 가벼운 파일).  켜면 전체 양식도 채운다.
        if self._include_full_template:
            # 전체 양식만 E~H(수기 영역)가 있으므로 그 열까지 칠한다.
            self._fill_rows(ws, rows_input, style_cols=self._full_cols(),
                            sheet_label=SHEET_FULL_NAME)
            # 양식.xlsx 의 A3..A22 미리 박힌 1..20 행번호 중 안 채운 행은 비운다.
            data_end_row = DATA_START_ROW + len(rows_input) - 1
            for r in range(max(data_end_row + 1, DATA_START_ROW), ws.max_row + 1):
                a = ws.cell(row=r, column=1)
                if isinstance(a.value, (int, float)):
                    a.value = None

        # 시트 순서: 미매칭(첫 번째, 조건부) → 요약 → 전체 양식.
        lots = self._lot_units(rows_input) if self._extract else []
        if self._rereview:
            index = 0
            for title, rows in ((i18n.KO.REREVIEW_SHEET_REJECT, rr_reject_rows),
                                (i18n.KO.REREVIEW_SHEET_ALL, rr_all_rows)):
                if title == i18n.KO.REREVIEW_SHEET_ALL and not self._include_good:
                    continue                    # Reject 만 출력 — '전체' 시트 미생성
                self._build_ad_sheet(wb, title, index, rows)
                self._add_rereview_columns(wb[title], rows)
                index += 1
        elif self._extract and (self._recipe_layout != RECIPE_LAYOUT_SINGLE
                              or len(lots) > 1):
            self._write_extract_sheets(wb, lots)
        elif unmatched_rows:
            # 미매칭 시트를 index 0(첫 번째)에 만들고, 요약은 index 1.
            self._write_unmatched_sheet(wb, unmatched_rows)
            self._build_summary_sheet(wb, rows_input, index=1)
        else:
            self._build_summary_sheet(wb, rows_input, index=0)

        # Slot 불일치 ---------------------------------------------------
        if self._result.slot_only_ref or self._result.slot_only_val:
            self._write_slot_mismatch_sheet(wb)

        # Wafer map — 슬롯별 + LOT 합산 PNG.  그림 한 장의 실패가 저장 전체를
        # 막지 않는다(사진 임베드와 같은 원칙).
        if rr_map_rows:
            try:
                self._write_reject_map_sheet(wb, rr_map_rows)
            except Exception:
                _LOG.exception("Reject die 맵 시트 실패 — 건너뜀")
        if map_rows:
            t0 = time.perf_counter()
            try:
                self._write_wafer_map_sheet(wb, map_rows)
            except Exception:
                pass
            _LOG.info("저장 소요 [Wafer Map] %.2f초 %d장",
                      time.perf_counter() - t0, len(map_rows))

        # 전체 양식 미포함이면, 헤더 복사가 끝난 지금 전체 양식 시트를 제거.
        if not self._include_full_template:
            try:
                wb.remove(wb[SHEET_FULL_NAME])
            except Exception:
                pass

        self._dst.parent.mkdir(parents=True, exist_ok=True)
        # ★ 여기서 한 번 더 본다 — 저장을 시작한 뒤 취소되면 파일이 깨진다.
        if self._stop.is_set():
            raise _Cancelled
        t0 = time.perf_counter()
        wb.save(str(self._dst))
        _LOG.info("저장 소요 [파일 쓰기] %.2f초 · 전체 %.2f초",
                  time.perf_counter() - t0, time.perf_counter() - t_start)
        # SharePoint / MIP 메타데이터 제거 — 회사 Excel 에서 ‘읽기 전용’ /
        # ‘보호 보기’ 로 열리는 것을 방지.
        try:
            _strip_corporate_metadata(self._dst)
        except Exception:
            # 메타데이터 정리 실패는 치명적이지 않다 — 결과 파일은 이미 저장됨.
            pass

    # ------------------------------------------------------------------
    def _summary_sheet_name(self) -> str:
        """요약 시트 이름 = 결과 파일명(확장자 제외).  엑셀 시트명 제약(31자·금지문자)
        에 맞춰 정리하고, 비면 안전한 기본값을 쓴다."""
        import re as _re
        name = Path(self._dst).stem or "결과"
        name = _re.sub(r'[:\\/?*\[\]]', "_", name)   # 엑셀 시트명 금지문자 → _
        name = name.strip() or "결과"
        if name == SHEET_FULL_NAME:                  # 2번째 시트와 충돌 방지
            name = name + " "
        return name[:31]

    def _build_summary_sheet(self, wb, rows_input: list, *, index: int = 0) -> None:
        """요약 시트 — A~D 열만, 전체 데이터."""
        self._build_ad_sheet(wb, self._summary_sheet_name(), index, rows_input)

    def _write_unmatched_sheet(self, wb, unmatched_rows: list) -> None:
        """미매칭 사진만 모은 A~D 시트(이미지 포함) — 첫 번째 시트(index 0)."""
        self._build_ad_sheet(wb, i18n.KO.SHEET_UNMATCHED, 0, unmatched_rows)

    def _build_ad_sheet(self, wb, title: str, index: int, rows_input: list) -> None:
        """A~D(번호·slot·기준/검증 이미지) 전용 시트를 만든다.

        전체 양식 시트의 A~D 헤더/병합/폭/행높이를 복사하고 ``rows_input`` 으로
        이미지를 임베드한다.  요약(전체)·미매칭(부분) 시트가 공유한다."""
        full = wb[SHEET_FULL_NAME]
        ws = wb.create_sheet(title=title, index=index)

        # A~D(+Scan 열) 헤더(row 1~2) 값/서식 복사.  수기 칸은 만들지 않는다(요약 시트엔 없음).
        from copy import copy as _copy
        cols = self._ad_cols()
        for r in (1, 2):
            for col in cols:
                src = full[f"{col}{r}"]
                dst = ws[f"{col}{r}"]
                dst.value = src.value
                if src.has_style:
                    dst.font = _copy(src.font)
                    dst.fill = _copy(src.fill)
                    dst.border = _copy(src.border)
                    dst.alignment = _copy(src.alignment)
                    dst.number_format = src.number_format
        # 병합 헤더(A1:A2, B1:B2, C1:D1 — Scan 열이 있으면 C1 부터 그 끝까지) 재현.
        for rng in ("A1:A2", "B1:B2", f"C1:{cols[-1]}1"):
            try:
                ws.merge_cells(rng)
            except Exception:
                pass
        # 열 폭 — 이 시트의 열만 전체 시트와 동일하게.
        for col in cols:
            w = full.column_dimensions[col].width
            if w:
                ws.column_dimensions[col].width = w
        # 데이터 행 높이도 동일하게(이미지가 같은 크기로 들어가도록).
        h = full.row_dimensions[DATA_START_ROW].height or ROW_HEIGHT_PT
        ws.row_dimensions[DATA_START_ROW].height = h
        # 새로 만든 시트는 양식의 화면/인쇄 설정을 물려받지 않는다 — 여기서 준다.
        self._apply_sheet_view(ws)
        # A~D 데이터 채우기(이미지는 mid 캐시에서 다시 임베드 — 시트 간 공유 불가).
        self._fill_rows(ws, rows_input, style_cols=cols, sheet_label=title)
        data_end = DATA_START_ROW + len(rows_input) - 1
        for rr in range(max(data_end + 1, DATA_START_ROW), ws.max_row + 1):
            a = ws.cell(row=rr, column=1)
            if isinstance(a.value, (int, float)):
                a.value = None

    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_width(ws, col_letter: str, min_w: float) -> None:
        cur = ws.column_dimensions[col_letter].width
        if not cur or cur < min_w:
            ws.column_dimensions[col_letter].width = min_w

    @staticmethod
    def _apply_sheet_view(ws) -> None:
        """머리 2행 고정(화면) + 반복(인쇄) — openpyxl 기본 API 두 줄 (28안 ③④).

        ★ try/except 로 감싼다.  인쇄 설정이 실패해도 **파일은 저장돼야 한다** —
        이 저장은 사용자가 몇 분을 기다린 결과물이다(깨진 사진 한 장이 저장을 못
        막는 것과 같은 원칙)."""
        try:
            ws.freeze_panes = FREEZE_AT
            ws.print_title_rows = PRINT_TITLE_ROWS
        except Exception:
            pass

    @staticmethod
    def _equalize_column_group(ws, cols: list[str], floor: float) -> None:
        """주어진 열들의 width 를 모두 같은 값으로 통일.

        target = max(현재 지정된 width 중 최대, floor). 모든 입력 컬럼이
        target 으로 설정되어 D == C, E == F == G == H 가 보장됨 (#3).
        """
        widths: list[float] = []
        for c in cols:
            w = ws.column_dimensions[c].width
            if w:
                widths.append(float(w))
        target = max(widths + [float(floor)])
        for c in cols:
            # ColumnDimension.customWidth 는 property (no setter) — width 만
            # 세팅하면 openpyxl 이 자동으로 customWidth=True 처리.
            ws.column_dimensions[c].width = target

    @staticmethod
    def _mirror_paired_column_widths(ws) -> None:
        """병합된 헤더 (예: C1:D1) 의 오른쪽 컬럼이 width 미지정인 경우 왼쪽
        컬럼의 width 를 그대로 복사한다.  양식.xlsx 처럼 ‘왼쪽만 폭 지정’ 한
        템플릿에서 오른쪽 셀이 좁아 사진이 작게 임베드되는 문제 해결."""
        from openpyxl.utils import get_column_letter
        for rng in list(ws.merged_cells.ranges):
            # 헤더 행에 걸친 가로 병합만 대상 (단일 행, 가로 폭 ≥ 2)
            if rng.min_row != rng.max_row:
                continue
            if rng.max_col - rng.min_col < 1:
                continue
            left = get_column_letter(rng.min_col)
            left_w = ws.column_dimensions[left].width
            if not left_w:
                continue
            for c in range(rng.min_col + 1, rng.max_col + 1):
                col_letter = get_column_letter(c)
                cd = ws.column_dimensions[col_letter]
                if not cd.width:
                    cd.width = left_w

    # ------------------------------------------------------------------
    def _build_minimal_headers(self, ws) -> None:
        """양식.xlsx 가 없을 때 쓰는 최소 헤더.

        ★ 양식과 **같은 서식 상수**를 쓴다.  전에는 여기만 노란 헤더로 남아 있어,
        양식 파일이 빠진 PC 에서만 출력물 생김새가 달라졌다.
        """
        from openpyxl.styles import Alignment, Font, PatternFill
        center = Alignment(horizontal="center", vertical="center",
                           wrap_text=True)
        ws["A1"] = "No"
        ws["B1"] = "slot#"
        ws["C1"] = "Scan Defect"
        ws.merge_cells("A1:A2")
        ws.merge_cells("B1:B2")
        ws.merge_cells("C1:D1")
        for col in "ABCD":
            top = ws[f"{col}1"]
            top.font = Font(name=TEMPLATE_FONT, bold=True, color="FFFFFFFF")
            top.fill = PatternFill("solid", fgColor=HEADER_NAVY)
            top.alignment = center
        # row 2 의 AOI-N 자리는 _do_export 에서 채움.
        for coord in ("C2", "D2"):
            c = ws[coord]
            c.font = Font(name=TEMPLATE_FONT, bold=True, color="FF1F2937")
            c.fill = PatternFill("solid", fgColor=GROUP_FILLS[coord[0]])
            c.alignment = center
        ws.row_dimensions[1].height = 21.75
        ws.row_dimensions[2].height = 19.5

    # ------------------------------------------------------------------
    def _slot_with_number(self, slot: str, shown: Optional[str] = None) -> str:
        """slot명 (+ 번호를 읽었으면 아래 줄에 ``(#6)``).

        요약·미매칭 시트의 B열과 Wafer Map 시트의 슬롯 칸이 **같은 표기**를 쓰도록
        여기 한 곳에서 만든다.  여러 줄이 되므로 호출부는 wrap_text 를 줘야 한다.
        ``shown`` 은 화면에 쓸 이름(기본은 ``slot`` 그대로) — 번호는 ``slot`` 으로 찾는다.
        """
        text = shown or slot
        num = (self._result.slot_numbers or {}).get(slot)
        return f"{text}\n{SLOT_NUMBER_FMT.format(num=num)}" if num else text

    # ------------------------------------------------------------------
    def _write_slot_cell(self, ws, row: int, slot: str, center) -> None:
        """B열에 slot명을 쓴다.  아래 줄에 덧붙는 것이 둘 있다:

        · **카세트 슬롯 번호** — ``WaferInfo.ini`` 의 ``ActiveSlot`` (사용자 요청)::

              A1033ABQEWG3
              (#6)

          장비가 준 값이라 slot명과 같은 글씨로 찍는다(보조 정보가 아니다).
        · **KLA 하위폴더명** — KLA 장비일 때만, 그 아래 줄에 **회색 작은 글씨**로
          (#KLA).  rich text 미지원 시 plain 폴백.
        """
        from openpyxl.styles import Alignment

        cell = ws[f"{COL_SLOT}{row}"]
        num = (self._result.slot_numbers or {}).get(slot)
        kf = (self._result.kla_folders or {}).get(slot)
        # 여러 LOT 추출이면 slot 키가 'LOT/slot' 이다 — LOT 는 시트 이름이 말하므로
        # B열에는 slot 만 쓴다(매칭·LOT 하나면 그대로).
        shown = slot_label(slot)
        if not num and not kf:
            cell.value = shown
            cell.alignment = center
            return
        # 여러 줄을 쓰면 wrap_text 가 있어야 엑셀이 줄바꿈을 보여준다.
        wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)
        head = self._slot_with_number(slot, shown=shown)
        if not kf:
            cell.value = head
            cell.alignment = wrap
            return
        try:
            from openpyxl.cell.rich_text import CellRichText, TextBlock
            from openpyxl.cell.text import InlineFont
            cell.value = CellRichText(
                TextBlock(InlineFont(), f"{head}\n"),
                TextBlock(InlineFont(sz=8, color="808080"), str(kf)),
            )
        except Exception:
            cell.value = f"{head}\n{kf}"
        cell.alignment = wrap

    # ------------------------------------------------------------------
    def _embed_image_path(self, src: Path, *, force_original: bool = False) -> Path:
        """셀에 임베드할 이미지 경로를 고른다.

        원본 화질 옵션이 켜져 있으면(전체 옵션 또는 호출자의 ``force_original``)
        원본 파일을 그대로 쓰고(축소 없음), 꺼져 있으면 중간 화질 캐시
        (`get_mid_path`)를 쓴다 — 기본은 가볍고 빠름.
        표시 크기는 어느 쪽이든 ``_fit_to_cell`` 이 셀에 맞게 줄이므로, 차이는
        '저장되는 픽셀 데이터의 해상도'(=화질)뿐이다.
        """
        if self._original_quality or force_original:
            return Path(src)
        return image_io.get_mid_path(Path(src))

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Scan image
    # ------------------------------------------------------------------
    def _ad_cols(self) -> list[str]:
        """요약·미매칭(·추출 한 시트) 시트의 열 — A~D + Scan 열."""
        return ["A", "B", "C", "D"] + self._scan_cols

    def _full_cols(self) -> list[str]:
        """전체 양식 시트의 열 — 요약 열 + 수기 4칸(Scan 열만큼 밀린 자리)."""
        from openpyxl.utils import get_column_letter
        n = len(self._scan_cols)
        return self._ad_cols() + [get_column_letter(5 + n + k) for k in range(4)]

    def _body_fills(self) -> dict:
        """열 문자 → 줄무늬 색.  Scan 열은 사진 열(C)과, 밀린 수기 칸은 원래 칸과 같다."""
        from openpyxl.utils import get_column_letter
        n = len(self._scan_cols)
        if not n:
            return BODY_FILLS
        fills = {c: BODY_FILLS[c] for c in "ABCD"}
        fills.update({c: BODY_FILLS["C"] for c in self._scan_cols})
        for k, orig in enumerate("EFGH"):
            fills[get_column_letter(5 + n + k)] = BODY_FILLS[orig]
        return fills

    def _scan_paths(self) -> list[Path]:
        """Scan 을 확인할 Color 경로(중복 없이, 순서 고정).

        Scan 목록 파일이 있는 폴더의 사진만 — 목록이 없는 폴더는 확인할 것이 없고,
        그래야 Scan 자료가 없는 저장은 진행률까지 예전과 똑같다."""
        from ..coords import scan_image
        seen: dict[str, Path] = {}
        for m in self._result.matches:
            for p in (m.ref_path, m.val_path):
                seen.setdefault(str(p), Path(p))
        for u in self._result.unmatched_refs:
            seen.setdefault(str(u.path), Path(u.path))
        has_list: dict[Path, bool] = {}
        out = []
        for p in seen.values():
            folder = p.parent
            if folder not in has_list:
                has_list[folder] = (folder / scan_image.SCAN_LIST_NAME).is_file()
            if has_list[folder]:
                out.append(p)
        return out

    def _scan_one(self, path: Path) -> tuple[str, Optional[bytes]]:
        """사진 한 장 → (상태, Crop JPEG 바이트).  화면과 같은 `scan_image` 계산이다."""
        import io
        from ..coords import scan_image
        m = scan_image.resolve(path)
        if not m.ok:
            return m.status, None
        res = scan_image.load_crop(m)
        if res is None:
            return scan_image.UNREADABLE, None
        buf = io.BytesIO()
        crop = res[0]
        (crop if crop.mode in ("L", "RGB") else crop.convert("RGB")).save(
            buf, format="JPEG", quality=SCAN_JPEG_QUALITY)
        return scan_image.OK, buf.getvalue()

    def _scan_prepass(self, paths: list[Path]) -> None:
        """모든 사진의 Scan 을 미리 확인하고 Crop 을 만든다 → Scan 열을 넣을지 결정.

        하나라도 Scan 이 확인되면(못 읽은 경우 포함) 열을 넣는다(사용자 결정: 묶어서
        판단).  원본은 NAS 에 있어 스레드로 나눠 읽는다 — 디코드는 GIL 을 놓는다."""
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..coords import scan_image

        base, overall = self._prog_done, self._prog_total or len(paths)
        t0 = time.perf_counter()
        workers = max(2, min(4, (os.cpu_count() or 2) - 1))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._scan_one, p): p for p in paths}
            for done, f in enumerate(as_completed(futures), start=1):
                if self._stop.is_set():
                    for g in futures:
                        g.cancel()
                    raise _Cancelled
                try:
                    self._scan_cache[str(futures[f])] = f.result()
                except Exception:
                    self._scan_cache[str(futures[f])] = (scan_image.NO_CANDIDATE, None)
                self.signals.progress.emit(base + done, overall,
                                           i18n.KO.SCAN_EXPORT_PHASE)
        self._prog_done = base + len(paths)
        counts: dict[str, int] = {}
        for st, _b in self._scan_cache.values():
            counts[st] = counts.get(st, 0) + 1
        found = counts.get(scan_image.OK, 0) + counts.get(scan_image.UNREADABLE, 0)
        if found:
            self._scan_cols = ["E"] if self._one_side else ["E", "F"]
        _LOG.info("저장 소요 [Scan 확인] %.2f초 %d장 — 상태별 %s",
                  time.perf_counter() - t0, len(paths), counts)

    def _insert_scan_columns(self, ws) -> None:
        """전체 양식 시트 D 뒤에 Scan 열을 끼운다(수기 칸은 오른쪽으로).

        ★ ``insert_cols`` 는 병합·열 폭을 옮기지 않는다.  병합을 먼저 풀어 두었다가
        밀린 자리로 다시 걸고, 폭도 직접 옮긴다.  사진을 넣기 **전**에 부르므로 그림
        앵커는 아직 없다."""
        from copy import copy as _copy
        from openpyxl.utils import get_column_letter

        n = len(self._scan_cols)
        merges = [(r.min_row, r.min_col, r.max_row, r.max_col)
                  for r in ws.merged_cells.ranges]
        for r in list(ws.merged_cells.ranges):
            ws.unmerge_cells(str(r))
        widths = {c: ws.column_dimensions[get_column_letter(c)].width
                  for c in range(1, max(ws.max_column, 8) + 1)}
        ws.insert_cols(SCAN_COL_START, n)
        for c, w in sorted(widths.items(), reverse=True):
            if c >= SCAN_COL_START and w:
                ws.column_dimensions[get_column_letter(c + n)].width = w
        img_w = widths.get(3) or IMG_COL_WIDTH
        for col in self._scan_cols:
            ws.column_dimensions[col].width = img_w
        for r1, c1, r2, c2 in merges:
            if c1 >= SCAN_COL_START:
                c1, c2 = c1 + n, c2 + n
            elif c1 <= 3 <= c2 and c2 == 4:     # 'Scan Defect' 그룹이 Scan 열까지 덮는다
                c2 = 4 + n
            ws.merge_cells(start_row=r1, start_column=c1, end_row=r2, end_column=c2)
        for row in range(1, ws.max_row + 1):
            src = ws[f"D{row}"]
            for col in self._scan_cols:
                dst = ws[f"{col}{row}"]
                if src.has_style and type(dst).__name__ != "MergedCell":
                    dst.font = _copy(src.font)
                    dst.fill = _copy(src.fill)
                    dst.border = _copy(src.border)
                    dst.alignment = _copy(src.alignment)
        if self._one_side:
            ws[f"E{HEADER_AOI_ROW}"] = i18n.KO.SCAN_EXCEL_HEADER
        else:
            for col, src in zip(self._scan_cols, (COL_REF, COL_VAL)):
                ws[f"{col}{HEADER_AOI_ROW}"] = i18n.KO.SCAN_EXCEL_HEADER_FMT.format(
                    machine=ws[f"{src}{HEADER_AOI_ROW}"].value or "").strip()

    def _place_scan(self, ws, src, col: str, row: int,
                    cell_w_px: float, cell_h_px: float) -> None:
        """Scan Crop 을 셀 중앙에.  없으면 짧은 회색 문구(사용자 결정)."""
        import io
        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font
        from ..coords import scan_image

        status, data = self._scan_cache.get(str(src), (scan_image.NO_CANDIDATE, None))
        if data:
            try:
                # 시트마다 새 이미지 객체 — 같은 객체를 여러 시트에 쓰면 저장이 깨진다.
                xli = XLImage(io.BytesIO(data))
                _fit_to_cell(xli, cell_w_px, cell_h_px)
                _add_image_centered(ws, xli, col, row, cell_w_px, cell_h_px)
                return
            except Exception:
                status = scan_image.UNREADABLE
        cell = ws[f"{col}{row}"]
        cell.value = (i18n.KO.SCAN_EXCEL_UNREADABLE if status == scan_image.UNREADABLE
                      else i18n.KO.SCAN_EXCEL_NONE)
        cell.font = Font(size=8, color="FF808080")
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)

    def _place_image(self, ws, src, col: str, row: int,
                     cell_w_px: float, cell_h_px: float,
                     original: bool = False) -> bool:
        """사진을 셀 중앙에 배치.  실패 시 False — 손상/누락 이미지 1 장 때문에
        export 전체가 abort 되지 않도록 호출자가 파일명 텍스트로 대체한다 (Bug #3)."""
        from openpyxl.drawing.image import Image as XLImage
        try:
            xli = XLImage(str(self._embed_image_path(Path(src),
                                                     force_original=original)))
            _fit_to_cell(xli, cell_w_px, cell_h_px)
            _add_image_centered(ws, xli, col, row, cell_w_px, cell_h_px)
            return True
        except Exception:
            return False

    @staticmethod
    def _style_data_row(ws, row: int, cols, band_index: int,
                        *, unmatched: bool = False, fills=None,
                        tint_cols=BORDER_COLS) -> None:
        """데이터 행 한 줄에 그룹 배경 + 얇은 격자를 입힌다.

        ★ **템플릿에 미리 칠해 둔 20행에 기대지 않는다.**  실제 결함은 그보다 훨씬
        많아서(실측 100건 이상), 21행부터 배경·격자가 끊기면 표가 중간에서 끝난
        것처럼 보인다.  줄무늬는 시트 행 번호가 아니라 **데이터 순번**을 따라야
        슬롯이 몇 개든 무늬가 일정하다.
        ★ ``unmatched`` 면 줄무늬 대신 :data:`UNMATCHED_FILL` 로 행을 덮는다(28안 ①).
        겹쳐 칠하지 않는다 — 같은 행에 두 색이 섞이면 무늬가 깨진다.  여기가
        **행 배경을 칠하는 유일한 자리**라는 불변식은 그대로다.
        """
        from openpyxl.styles import Border, PatternFill, Side

        fills = fills or BODY_FILLS
        side = Side(style="thin", color=BODY_GRID_COLOR)
        box = Border(left=side, right=side, top=side, bottom=side)
        for col in cols:
            cell = ws[f"{col}{row}"]
            # ★ 미매칭 틴트는 **A~D 에만** 칠한다(시안 ① "미매칭 행은 A~D 배경을").
            #   전체 양식 시트는 style_cols 가 "ABCDEFGH" 라 그대로 두면 E~H
            #   **수기 입력 영역**까지 물든다 — 그 칸들은 사람이 직접 쓰는 자리라
            #   그룹색(연두·주황)이 세로로 이어지는 것이 정보다.
            tint = unmatched and col in tint_cols
            cell.fill = PatternFill(
                "solid",
                fgColor=(UNMATCHED_FILL if tint
                         else fills[col][band_index % 2]))
            cell.border = box

    def _fill_rows(self, ws, rows_input: list[tuple[str, str, object]],
                   *, style_cols=None, sheet_label: str = "") -> None:
        from openpyxl.styles import Alignment, Border, Side

        style_cols = style_cols or self._ad_cols()
        border_cols = self._ad_cols()
        fills = self._body_fills()
        total = len(rows_input)
        # 이 시트 이전까지 채운 행 수 — 진행률은 여기에 이어서 보고한다(단조 증가).
        # `_prog_total` 이 0 이면(이 메서드만 단독으로 부른 경우) 예전처럼 이 시트
        # 기준으로 센다 — 계약(총 > 0 = 결정형)은 어느 쪽이든 지켜진다.
        base = self._prog_done
        overall = self._prog_total or total
        # ★ 30안 — 진행 문구는 **어느 시트의 어느 슬롯**인지를 말한다.
        #   exporter 는 원래 슬롯명을 실어 보냈는데 화면이 그걸 버리고 고정 문구
        #   "엑셀로 저장 중…" 만 띄웠다.  이미지 수백 장 임베드는 수 분짜리이고,
        #   오래 걸리는 이유는 **시트를 두세 장 쓰기 때문**(요약·미매칭·전체 양식에
        #   사진을 각각 다시 임베드한다)인데 화면은 그 사실을 한 번도 말하지 않았다.
        #   행 카운터는 오버레이의 진행 라벨이 이미 담당하므로 여기엔 넣지 않는다.
        def _phase(slot: str) -> str:
            if not sheet_label:
                return slot
            return i18n.KO.EXPORT_PHASE_FMT.format(sheet=sheet_label, slot=slot)
        row = DATA_START_ROW
        center = Alignment(horizontal="center", vertical="center")
        # 슬롯이 바뀌는 첫 행 위에 굵은 가로 구분선 (#4).  같은 슬롯끼리
        # 시각적으로 묶이도록.
        slot_sep_side = Side(border_style="thick", color="FF333333")
        prev_slot: Optional[str] = None
        # 템플릿 데이터 행의 ‘기준 높이’ 를 한 번만 측정 — 보통 165.75pt.
        # 양식이 없거나 데이터 행에 높이가 안 잡혀 있으면 ROW_HEIGHT_PT 사용.
        template_row_h = ws.row_dimensions[DATA_START_ROW].height or ROW_HEIGHT_PT
        # C / D 컬럼 폭은 행마다 동일하므로 한 번만 계산.
        cell_w_px = _col_width_to_px(
            ws.column_dimensions[COL_REF].width or IMG_COL_WIDTH
        )
        cell_h_px = _row_height_to_px(template_row_h)
        t_sheet = time.perf_counter()
        t_info = t_img = 0.0           # 정보(좌표·계측) 조회 / 사진 임베드에 쓴 시간
        for idx, (cur_slot, _key, payload) in enumerate(rows_input, start=1):
            if self._stop.is_set():
                raise _Cancelled
            # 새 행은 템플릿의 데이터 행과 같은 높이로 통일 → 양식 안팎 일관성.
            cur_h = ws.row_dimensions[row].height
            if not cur_h or cur_h < template_row_h:
                ws.row_dimensions[row].height = template_row_h

            # 배경·격자를 **먼저** 입힌다 — 아래 슬롯 구분선(굵은 top)이 이 위에
            # 덧그려져야 살아남는다(순서를 바꾸면 구분선이 지워진다).
            self._style_data_row(ws, row, style_cols, idx - 1,
                                 unmatched=(isinstance(payload, MissEntry)
                                            and not self._one_side),
                                 fills=fills, tint_cols=border_cols)

            # 슬롯 변경 시 A~H 전 열에 top border 적용 (기존 좌/우/하 보존).
            if prev_slot is not None and cur_slot != prev_slot:
                for col in border_cols:
                    cell = ws[f"{col}{row}"]
                    old = cell.border
                    cell.border = Border(
                        top=slot_sep_side,
                        left=old.left, right=old.right, bottom=old.bottom,
                        diagonal=old.diagonal,
                        diagonal_direction=old.diagonal_direction,
                        outline=old.outline,
                        vertical=old.vertical,
                        horizontal=old.horizontal,
                    )
            prev_slot = cur_slot

            # A 열: 행 번호 (사용자 양식의 ‘No’).
            no_cell = ws[f"{COL_NO}{row}"]
            no_cell.value = idx
            no_cell.alignment = center

            if isinstance(payload, MatchResult):
                m = payload
                self._write_slot_cell(ws, row, m.slot, center)
                # ★ 매치 행의 사진 칸에도 미매칭 행과 같은 정보를 **셀 값**으로 적는다:
                #   파일명 + 계측(Surface.flt) + 좌표(col/row · x/y).  사진은 예전처럼
                #   셀을 가득 채우고 그 위를 덮으므로 **평소에는 보이지 않는다** —
                #   특별한 경우가 아니면 볼 일이 없고, 필요할 때 엑셀에서 사진을 치우면
                #   드러난다(사용자 결정).  그래서 행 높이는 양식 그대로다.  두 사진의
                #   값은 서로 다르다(기준·검증 장비가 각자 잰 것) — 각 칸에 **그 사진의**
                #   값이 간다.
                # 손상/누락 이미지 1 장 때문에 전체 export 가 abort 되지 않도록
                # 각 사진을 개별 try 로 감싼다 (Bug #3).  실패하면 사진이 없으니
                # 캡션이 그대로 보인다 — 예전의 파일명 대체와 같은 정보다.
                for src, col in ((m.ref_path, COL_REF), (m.val_path, COL_VAL)):
                    t0 = time.perf_counter()
                    self._write_caption(ws, col, row, src)
                    t1 = time.perf_counter()
                    self._place_image(ws, src, col, row, cell_w_px, cell_h_px)
                    t_info += t1 - t0
                    t_img += time.perf_counter() - t1
                for src, col in zip((m.ref_path, m.val_path), self._scan_cols):
                    self._place_scan(ws, src, col, row, cell_w_px, cell_h_px)
                self.signals.progress.emit(base + idx, overall, _phase(m.slot))
            else:
                u: MissEntry = payload
                self._write_slot_cell(ws, row, u.slot, center)
                # 기준 이미지: 정상 임베드.  '미매칭 사진만 원본 화질' 옵션은 여기만 탄다.
                t0 = time.perf_counter()
                if not self._place_image(ws, u.path, COL_REF, row,
                                         cell_w_px, cell_h_px,
                                         original=self._unmatched_original):
                    ws[f"{COL_REF}{row}"] = str(Path(u.path).name)
                t1 = time.perf_counter()
                self._write_info_cell(ws, COL_VAL, row, u.path,
                                      unmatched_note=not self._one_side)
                # 미매칭·추출 행의 Scan 은 **그 사진의 것**만, 첫 Scan 열(E)에 둔다.
                # 검증 Scan 칸(F)은 짝 사진이 없으니 비워 둔다.
                if self._scan_cols:
                    self._place_scan(ws, u.path, self._scan_cols[0], row,
                                     cell_w_px, cell_h_px)
                t_img += t1 - t0
                t_info += time.perf_counter() - t1
                self.signals.progress.emit(base + idx, overall, _phase(u.slot))

            row += 1

        _LOG.info("저장 소요 [시트 %s] %.2f초 %d행 (정보 조회 %.2f · 사진 %.2f)",
                  sheet_label, time.perf_counter() - t_sheet, total, t_info, t_img)
        # 다음 시트가 이어서 셀 수 있게 이 시트 몫을 확정한다.
        self._prog_done = base + total

    def _prewarm_images(self, paths: list) -> None:
        """임베드할 중간 화질 사진을 **병렬로** 미리 만든다(Defect 추출).

        ★ 결과는 같다 — 같은 함수(`_embed_image_path`)가 같은 캐시 파일을 만들 뿐이고,
        행을 채울 때는 그 파일을 그대로 쓴다.  보통은 로딩 단계의 썸네일 풀이 이미
        만들어 두어 즉시 끝난다.  그 단계를 [중지] 로 건너뛰었거나 캐시가 지워졌으면
        예전에는 행마다 **하나씩** 만들었다(실측: 1380×1036 사진 400장 15초/4코어 —
        그중 리사이즈가 12초).  Pillow 의 디코드·리사이즈는 GIL 을 놓으므로 스레드로
        나누면 코어 수만큼 빨라진다.  실패한 사진은 행을 채울 때 예전처럼 처리된다."""
        import os
        from concurrent.futures import ThreadPoolExecutor, as_completed

        base, overall = self._prog_done, self._prog_total or len(paths)
        t0 = time.perf_counter()

        def one(p):
            try:
                self._embed_image_path(Path(p), force_original=self._unmatched_original)
            except Exception:
                pass

        workers = max(2, (os.cpu_count() or 2) - 1)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, p) for p in paths]
            for done, _f in enumerate(as_completed(futures), start=1):
                if self._stop.is_set():
                    for f in futures:
                        f.cancel()
                    raise _Cancelled
                self.signals.progress.emit(base + done, overall,
                                           i18n.KO.EXPORT_PREPARE_IMAGES)
        self._prog_done = base + len(paths)
        _LOG.info("저장 소요 [사진 준비] %.2f초 %d장 (스레드 %d — 로딩 캐시가 있으면 0에 가깝다)",
                  time.perf_counter() - t0, len(paths), workers)

    # ------------------------------------------------------------------
    # Recipe 나누기 (Defect 추출)
    # ------------------------------------------------------------------
    def _recipe_row_groups(self, rows_input: list) -> list[tuple[str, list]]:
        """행들을 Recipe 이름별로 — ``[(이름, 행들)]``, 순서는 :func:`recipe_groups`."""
        by_path = {Path(r[2].path): r for r in rows_input}
        return [(label, [by_path[p] for p in paths])
                for label, paths in recipe_groups(list(by_path))]

    @staticmethod
    def _lot_units(rows_input: list) -> list[tuple[str, list]]:
        """행들을 LOT 별로 — ``[(LOT, 행들)]``, LOT 이름순.  LOT 가 하나면 ``[("", 전부)]``.

        LOT 는 slot 키의 접두(``"LOT/slot"``)에서 읽는다(`models.slot.scan_lots`)."""
        units: dict[str, list] = {}
        for r in rows_input:
            units.setdefault(lot_of(r[0]), []).append(r)
        return sorted(units.items())

    def _write_extract_sheets(self, wb, lots: list) -> None:
        """Defect 추출의 시트들 — LOT 마다(여러 LOT) × 저장 방식(Recipe 나누기).

        · 한 시트      : LOT 마다 시트 1개
        · Recipe별 열  : LOT 마다 시트 1개(Recipe 나란히)
        · Recipe별 시트: LOT × Recipe 마다 시트 1개(이름 'LOT Recipe')
        LOT 가 하나면 시트 이름은 예전과 같다(파일명 / Recipe 이름)."""
        taken = {SHEET_FULL_NAME, i18n.KO.WAFER_MAP_SHEET,
                 i18n.KO.SLOT_MISMATCH_SHEET}
        index = 0

        def title_of(name: str) -> str:
            t = _safe_sheet_title(name, taken)
            taken.add(t)
            return t

        for lot, rows in lots:
            base = lot or self._summary_sheet_name()
            if self._recipe_layout == RECIPE_LAYOUT_SINGLE:
                self._build_ad_sheet(wb, title_of(base), index, rows)
                index += 1
                continue
            t0 = time.perf_counter()
            groups = self._recipe_row_groups(rows)
            _LOG.info("저장 소요 [Recipe 분류 %s] %.2f초 %d장 → %d그룹", lot or "-",
                      time.perf_counter() - t0, len(rows), len(groups))
            if self._recipe_layout == RECIPE_LAYOUT_SHEETS:
                for label, grows in groups:
                    name = f"{lot} {label}" if lot else label
                    self._build_ad_sheet(wb, title_of(name), index, grows)
                    index += 1
            else:
                self._write_recipe_columns_sheet(wb, groups, title=title_of(base),
                                                 index=index)
                index += 1

    def _write_recipe_columns_sheet(self, wb, groups: list, *, title: str,
                                    index: int = 0) -> None:
        """한 시트에 Recipe 마다 [사진 | 정보] 열을 옆으로 나란히 (사용자 결정).

        머리 1행 = Recipe 이름(두 칸 병합), 2행 = AOI-N · 정보.  슬롯 안에서
        Recipe 끼리 위치 차가 150µm 미만인 사진(x20·x5 가 같은 결함을 찍은 것)은 같은
        행에 모아 **위쪽**에 두고, 짝이 없는 사진은 한 장이 한 행이다(:func:`pair_rows`,
        사용자 결정).  정보 칸은 D열과 같은 글자다."""
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        full = wb[SHEET_FULL_NAME]
        ws = wb.create_sheet(title=title, index=index)
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        head_font = Font(name=TEMPLATE_FONT, bold=True, color="FFFFFFFF")
        sub_font = Font(name=TEMPLATE_FONT, bold=True, color="FF1F2937")
        navy = PatternFill("solid", fgColor=HEADER_NAVY)
        sub_fill = PatternFill("solid", fgColor=GROUP_FILLS["C"])
        thin = Side(style="thin", color=BODY_GRID_COLOR)
        thick = Side(border_style="thick", color="FF333333")
        img_w = full.column_dimensions[COL_REF].width or IMG_COL_WIDTH
        row_h = full.row_dimensions[DATA_START_ROW].height or ROW_HEIGHT_PT

        for col, text in (("A", "No"), ("B", "slot#")):
            ws[f"{col}1"] = full[f"{col}1"].value or text
            ws.merge_cells(f"{col}1:{col}2")
            ws[f"{col}1"].font, ws[f"{col}1"].fill = head_font, navy
            ws[f"{col}1"].alignment = center
            ws.column_dimensions[col].width = COL_WIDTHS[col]
        machine = _machine_label(self._result.ref_machine)
        # Recipe 마다 (사진 열, 정보 열[, Scan 열]) — Scan 이 있으면 3열씩.
        stride = 3 if self._scan_cols else 2
        cols: list[tuple] = []
        for k, (label, _rows) in enumerate(groups):
            trip = tuple(get_column_letter(3 + stride * k + j) for j in range(stride))
            cols.append(trip)
            pc, ic = trip[0], trip[1]
            ws[f"{pc}1"] = label
            ws.merge_cells(f"{pc}1:{trip[-1]}1")
            ws[f"{pc}2"] = machine
            ws[f"{ic}2"] = i18n.KO.EXTRACT_INFO_HEADER
            if stride == 3:
                ws[f"{trip[2]}2"] = i18n.KO.SCAN_EXCEL_HEADER
            for c in trip:
                ws[f"{c}1"].font, ws[f"{c}1"].fill = head_font, navy
                ws[f"{c}2"].font, ws[f"{c}2"].fill = sub_font, sub_fill
                ws[f"{c}1"].alignment = ws[f"{c}2"].alignment = center
                ws.column_dimensions[c].width = img_w
        ws.row_dimensions[1].height = 21.75
        ws.row_dimensions[2].height = 19.5
        self._apply_sheet_view(ws)

        # {slot: [Recipe 별 사진 목록]} — 행은 slot → 파일명 순서 그대로 들어온다.
        slots = sorted({r[0] for _l, rows in groups for r in rows})
        per_slot = {s: [[r for r in rows if r[0] == s] for _l, rows in groups]
                    for s in slots}
        cell_w_px = _col_width_to_px(img_w)
        cell_h_px = _row_height_to_px(row_h)
        last_col = cols[-1][-1] if cols else "B"
        all_cols = ["A", "B"] + [c for pair in cols for c in pair]
        row, no, placed = DATA_START_ROW, 0, 0
        base, overall = self._prog_done, self._prog_total or 1
        t_sheet, t_info, t_img = time.perf_counter(), 0.0, 0.0
        for s_i, slot in enumerate(slots):
            lists = per_slot[slot]
            # ★ 서로 다른 Recipe 가 같은 결함을 찍은 사진(위치 차 < 150µm)은 같은 행에,
            #   그 행들을 위로(사용자 결정 — 따로 표시하지 않는다).  나머지는 순서대로.
            slot_rows = pair_rows([[r[2].path for r in x] for x in lists],
                                  defect_position)
            for i, row_paths in enumerate(slot_rows):
                if self._stop.is_set():
                    raise _Cancelled
                no += 1
                ws.row_dimensions[row].height = row_h
                band = BODY_FILLS["A"][(no - 1) % 2]
                for c in all_cols:
                    cell = ws[f"{c}{row}"]
                    cell.fill = PatternFill("solid", fgColor=band)
                    cell.border = Border(left=thin, right=thin, bottom=thin,
                                         top=thick if (i == 0 and s_i) else thin)
                ws[f"A{row}"] = no
                ws[f"A{row}"].alignment = Alignment(horizontal="center",
                                                    vertical="center")
                self._write_slot_cell(ws, row, slot,
                                      Alignment(horizontal="center",
                                                vertical="center"))
                for trip, path in zip(cols, row_paths):
                    if path is None:
                        continue
                    pc, ic = trip[0], trip[1]
                    t0 = time.perf_counter()
                    if not self._place_image(ws, path, pc, row, cell_w_px, cell_h_px):
                        ws[f"{pc}{row}"] = Path(path).name
                    if len(trip) > 2:
                        self._place_scan(ws, path, trip[2], row, cell_w_px, cell_h_px)
                    t1 = time.perf_counter()
                    self._write_info_cell(ws, ic, row, path, unmatched_note=False)
                    t_img += t1 - t0
                    t_info += time.perf_counter() - t1
                    placed += 1
                    self.signals.progress.emit(
                        base + placed, overall,
                        i18n.KO.EXPORT_PHASE_FMT.format(sheet=ws.title, slot=slot))
                row += 1
        _LOG.info("저장 소요 [Recipe 나란히 시트, 열 끝 %s] %.2f초 %d장 "
                  "(정보 조회 %.2f · 사진 %.2f)", last_col,
                  time.perf_counter() - t_sheet, placed, t_info, t_img)
        self._prog_done = base + placed

    def _write_info_cell(self, ws, col: str, row: int, path, *,
                         unmatched_note: bool) -> None:
        """미매칭 행의 정보 칸 — 파일명 + 계측·좌표 줄을 **보이는 글자**로.

        Defect 추출의 D열(정보)과 Recipe 나란히 배치의 정보 열도 같은 모양이다
        (사용자 결정: '마치 미매칭 사진 정보 출력하듯이')."""
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Font

        # 리치 텍스트를 못 쓰는 openpyxl 에서의 같은 등급(8pt, 본문 검정).
        name_font = Font(color="FF000000", size=8)
        # 검증 컬럼에 파일명 텍스트 (검정 8pt — 28안 ②).  결함 geometry(area/width/
        # length/contrast) 또는 명시적 마커를 파일명 아래 같은 등급으로 덧붙인다
        # (#geometry).  geometry 비활성(스키마 미충전) 이면 기존과 동일.
        cell_val = ws[f"{col}{row}"]
        name = Path(path).name
        # geometry(Surface.flt) + 좌표(col/row/x/y, 매칭단계 메커니즘 재사용).
        # 좌표는 Surface.flt 유무와 무관하므로 미지원 자재 행에도 붙는다.
        blocks = self._geometry_blocks(path) + self._coord_blocks(path)
        # ★ 28안 ② — 파일명을 **빨강 굵은 글씨에서** 아래 geometry·좌표
        #   줄과 같은 8pt 로 내린다.  '미매칭' 이라는 구분은 이제 **행
        #   틴트**가 담당하므로 글씨가 혼자 소리칠 이유가 없어졌다.
        #   ★ 색은 **검정**이다(사용자 결정) — 시안은 회색(#808080)이었지만
        #   인쇄물에서 8pt 회색은 실제로 읽기 어려웠다.  크기로 등급을
        #   낮추되 명도는 본문 그대로 둔다.
        if blocks:
            from openpyxl.cell.rich_text import CellRichText, TextBlock
            from openpyxl.cell.text import InlineFont
            name_inline = InlineFont(sz=8, color="FF000000")
            cell_val.value = CellRichText(
                TextBlock(name_inline, name), *blocks,
            )
        else:
            cell_val.value = name
            cell_val.font = name_font
        cell_val.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True,
        )
        if unmatched_note:
            cell_val.comment = Comment("미매칭", "AOI")

    # ------------------------------------------------------------------
    def _write_caption(self, ws, col: str, row: int, src) -> None:
        """매치 행 사진 칸의 셀 값 — 파일명 + 계측·좌표 줄.  **사진 뒤에 숨는다.**

        줄 문자열은 :func:`coords.single_info.defect_lines` 가 만든다 — 미매칭 행
        D열(`_geometry_blocks` + `_coord_blocks`)·단일 사진 정보 화면과 **같은 생산자**
        라 세 곳의 수치가 같다.  서식도 미매칭 D열과 같다(8pt 검정, 가운데, 줄바꿈)
        — 사진을 치우면 미매칭 칸과 같은 모양으로 드러난다.
        rich text 를 못 쓰는 openpyxl 에서는 파일명만 남는다(미매칭 행과 같은 폴백)."""
        from openpyxl.styles import Alignment, Font
        from ..coords import single_info

        name = Path(src).name
        blocks = self._info_blocks(single_info.defect_lines(Path(src)))
        cell = ws[f"{col}{row}"]
        if blocks:
            from openpyxl.cell.rich_text import CellRichText, TextBlock
            from openpyxl.cell.text import InlineFont
            cell.value = CellRichText(
                TextBlock(InlineFont(sz=8, color="FF000000"), name), *blocks)
        else:
            cell.value = name
            cell.font = Font(color="FF000000", size=8)
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)

    # ------------------------------------------------------------------
    @staticmethod
    def _info_blocks(lines) -> list:
        """줄 목록 → 파일명 아래에 덧붙일 8pt TextBlock 목록.

        ★ 색은 **검정**이다(사용자 결정).  예전에는 회색(#808080)이었는데 8pt
        회색은 인쇄물에서 실제로 읽기 어려웠다 — 등급은 크기로만 낮추고 명도는
        본문 그대로 둔다.

        줄바꿈은 여기서만 붙인다 — 문자열을 만드는
        :mod:`coords.single_info` 는 순수 텍스트만 돌려준다.
        best-effort: rich_text 미지원 openpyxl 등에서는 빈 목록(=plain 파일명).
        """
        try:
            from openpyxl.cell.rich_text import TextBlock
            from openpyxl.cell.text import InlineFont

            small = InlineFont(sz=8, color="FF000000")
            return [TextBlock(small, "\n" + t) for t in lines]
        except Exception:
            return []

    @staticmethod
    def _geometry_blocks(path) -> list:
        """미매칭 행 D열 파일명 아래에 덧붙일 결함 measurement 블록.

        표기 문자열은 :func:`coords.single_info.geometry_lines` 가 만든다 —
        단일 사진 정보 화면과 **같은 값**이 나오도록 생산자를 하나로 둔 것이다.
        """
        from ..coords import single_info
        return ExcelExporter._info_blocks(single_info.geometry_lines(Path(path)))

    # ------------------------------------------------------------------
    @staticmethod
    def _coord_blocks(path) -> list:
        """미매칭 행 D열에 덧붙일 좌표(col/row/x/y) 블록.

        표기 문자열은 :func:`coords.single_info.coord_lines` 가 만든다.
        """
        from ..coords import single_info
        return ExcelExporter._info_blocks(single_info.coord_lines(Path(path)))

    # ------------------------------------------------------------------
    # Wafer map 시트
    # ------------------------------------------------------------------
    _MAP_PX = 360          # 셀에 들어가는 그림 한 변(px)

    def _wafer_map_rows(self) -> list[str]:
        """시트에 실을 행 — ``""`` 은 LOT 합산, 나머지는 슬롯명(정렬).  사진이 없으면 빈 목록."""
        if self._map_renderer is None:
            return []
        names = sorted(n for n, (r, v) in self._result.slot_images.items() if r or v)
        if not names:
            return []
        return [""] + names if len(names) > 1 else names

    @staticmethod
    def _map_col_header(role: str, machine: str) -> str:
        """Wafer Map 시트의 기준/검증 머리칸 — ``기준`` 아래 줄에 ``AOI-17``.

        요약 시트는 머리 2행(그룹 / AOI-N)으로 호기를 밝히는데 이 시트는 머리가 한 줄이라
        **어느 호기의 맵인지 적혀 있지 않았다**(사용자 지적).  호기 입력이 비어 있으면
        역할만 적는다 — 빈 줄을 남기지 않는다.
        """
        label = _machine_label(machine)
        if not label:
            return role
        return i18n.KO.WAFER_MAP_SHEET_COL_MACHINE_FMT.format(role=role,
                                                              machine=label)

    def _write_wafer_map_sheet(self, wb, rows: list[str]) -> None:
        """A=슬롯(+카세트 번호), B=기준 맵, C=검증 맵.  그림은 화면과 같은
        렌더러(주입된 ``map_renderer``)."""
        import io

        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils.units import pixels_to_points

        from ..coords.wafer_map import slot_maps

        ws = wb.create_sheet(title=i18n.KO.WAFER_MAP_SHEET)
        ws["A1"] = i18n.KO.WAFER_MAP_SHEET_COL_SLOT
        ws["B1"] = self._map_col_header(
            i18n.KO.EXTRACT_MAP_SHEET_COL if self._extract
            else i18n.KO.WAFER_MAP_SHEET_COL_REF,
            self._result.ref_machine)
        if not self._extract:          # 추출은 장비가 하나 — 검증 맵 칸이 없다
            ws["C1"] = self._map_col_header(i18n.KO.WAFER_MAP_SHEET_COL_VAL,
                                           self._result.val_machine)
        # 머리칸·슬롯칸 모두 여러 줄이 될 수 있다 — wrap_text 없으면 줄바꿈이 안 보인다.
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for c in "ABC":
            ws[f"{c}1"].font = Font(bold=True)
            ws[f"{c}1"].alignment = center
        ws.row_dimensions[1].height = 30      # 두 줄 머리칸이 잘리지 않게
        ws.column_dimensions["A"].width = 22
        px_w = self._MAP_PX + 8
        for c in "BC":
            ws.column_dimensions[c].width = px_w / 7.0

        def png(data) -> XLImage:
            xli = XLImage(io.BytesIO(self._map_renderer(data, self._MAP_PX)))
            xli.width = xli.height = self._MAP_PX
            return xli

        base = self._prog_done
        live_seen = False
        for idx, slot in enumerate(rows, start=1):
            if self._stop.is_set():
                raise _Cancelled
            # 진행 라벨은 slot명 한 줄로(여러 줄이면 오버레이 문구가 깨진다).
            label = slot or i18n.KO.WAFER_MAP_SHEET_ALL
            self.signals.progress.emit(
                base + idx, self._prog_total,
                i18n.KO.EXPORT_PHASE_FMT.format(sheet=i18n.KO.WAFER_MAP_SHEET,
                                                slot=label))
            r = idx + 1
            # 슬롯 칸에는 요약 시트 B열과 **같은 표기**를 쓴다 — slot명 아래 `(#6)`.
            cell_label = self._slot_with_number(slot) if slot else label
            ws.cell(row=r, column=1, value=cell_label).alignment = center
            ws.row_dimensions[r].height = pixels_to_points(px_w)
            ref, val = slot_maps(self._result, slot)
            for col, data in (("B", ref), ("C", val)):
                if data.frame is None:
                    continue
                live_seen = live_seen or data.live_points > 0
                try:
                    _add_image_centered(ws, png(data), col, r, px_w, px_w)
                except Exception:
                    ws[f"{col}{r}"] = "—"
        # LIVE 파일로 그린 맵이 있으면 표 아래에 재검토 당부(화면 배너와 같은 문구).
        if live_seen:
            note_row = len(rows) + 3
            ws.merge_cells(start_row=note_row, start_column=1,
                           end_row=note_row, end_column=3)
            note = ws.cell(row=note_row, column=1,
                           value=i18n.KO.WAFER_MAP_WARN_LIVE_UNVERIFIED)
            note.font = Font(bold=True, color="FF8C4A0F")
            note.alignment = Alignment(wrap_text=True, vertical="center")
            ws.row_dimensions[note_row].height = 36
        self._prog_done = base + len(rows)

    # ------------------------------------------------------------------
    # AVAGO 재리뷰
    # ------------------------------------------------------------------
    def _rr_header(self, cell, text: str) -> None:
        from openpyxl.styles import Alignment, Font, PatternFill
        cell.value = text
        cell.font = Font(name=TEMPLATE_FONT, bold=True, color="FFFFFFFF")
        cell.fill = PatternFill("solid", fgColor=HEADER_NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)

    def _add_rereview_columns(self, ws, rows_input: list) -> None:
        """사진 시트 끝에 **판정**·**die (col, row)** 열 — 행 순서는 ``_fill_rows`` 와 같다."""
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        n = len(self._ad_cols())
        v_col, d_col = get_column_letter(n + 1), get_column_letter(n + 2)
        for col, text in ((v_col, i18n.KO.REREVIEW_VERDICT_HEADER),
                          (d_col, i18n.KO.REREVIEW_DIE_HEADER)):
            self._rr_header(ws[f"{col}1"], text)
            ws.merge_cells(f"{col}1:{col}2")
            ws.column_dimensions[col].width = 13
        center = Alignment(horizontal="center", vertical="center")
        for idx, (slot, _key, u) in enumerate(rows_input):
            r = DATA_START_ROW + idx
            reject = u.note == VERDICT_REJECT
            c = ws[f"{v_col}{r}"]
            c.value = u.note
            c.alignment = center
            c.font = Font(name=TEMPLATE_FONT, bold=True,
                          color="FFC00000" if reject else "FF2E7D32")
            if reject:
                c.fill = PatternFill("solid", fgColor="FFFDE2E2")
            plan = self._result.rereview.get(slot)
            die = plan.die_of.get(Path(u.path)) if plan else None
            d = ws[f"{d_col}{r}"]
            d.value = (i18n.KO.REREVIEW_DIE_CELL_FMT.format(col=die[0], row=die[1])
                       if die else "—")
            d.alignment = center

    _RR_MAP_PX = 480       # 재리뷰 맵 한 변(px) — die 수천 칸이라 결함 맵보다 크게

    def _write_reject_map_sheet(self, wb, slots: list[str]) -> None:
        """웨이퍼마다 **기존 Map**(1차 리뷰) · **수정된 Map**(재리뷰 신규 Reject 반영).

        그림은 Wafer map 보기와 같은 생김새(`paint_reject_map`) — 노치는 아래에 표시."""
        import io

        from openpyxl.drawing.image import Image as XLImage
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils.units import pixels_to_points

        from ..coords import rereview as rr

        K = i18n.KO
        ws = wb.create_sheet(title=K.WAFER_MAP_SHEET)
        ws["A1"] = K.WAFER_MAP_SHEET_COL_SLOT
        ws["B1"] = K.REREVIEW_MAP_COL_ORIG
        ws["C1"] = K.REREVIEW_MAP_COL_NEW
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for c in "ABC":
            ws[f"{c}1"].font = Font(bold=True)
            ws[f"{c}1"].alignment = center
        ws.row_dimensions[1].height = 24
        ws.column_dimensions["A"].width = 22
        size = self._RR_MAP_PX
        px_w = size + 8
        for c in "BC":
            ws.column_dimensions[c].width = px_w / 7.0
        ws.column_dimensions["D"].width = 40
        legend = ws.cell(row=2, column=1, value=K.REREVIEW_MAP_LEGEND)
        legend.font = Font(bold=True, color="FF5A574E")
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=3)
        base = self._prog_done
        for idx, slot in enumerate(slots, start=1):
            if self._stop.is_set():
                raise _Cancelled
            self.signals.progress.emit(
                base + idx, self._prog_total,
                K.EXPORT_PHASE_FMT.format(sheet=K.WAFER_MAP_SHEET, slot=slot))
            r = idx + 2
            ws.cell(row=r, column=1, value=self._slot_with_number(slot)).alignment = center
            ws.row_dimensions[r].height = pixels_to_points(px_w)
            plan = self._result.rereview[slot]
            rm = plan.reject_map
            if rm is None:
                ws.cell(row=r, column=2, value=K.REREVIEW_MAP_NO_MAP).alignment = center
                continue
            new = (rr.new_reject_cells(plan, self._result.rereview_rejects(slot))
                   if plan.aligned else frozenset())
            n_map = len(rm.rejects)
            for col, cells, label in (
                    ("B", frozenset(), K.REREVIEW_MAP_LABEL_ORIG_FMT.format(n=n_map)),
                    ("C", new, K.REREVIEW_MAP_LABEL_NEW_FMT.format(
                        n=n_map + len(new), new=len(new)))):
                try:
                    xli = XLImage(io.BytesIO(self._reject_map_renderer(
                        rm, cells, size, pitch=plan.pitch, label=label)))
                    xli.width = xli.height = size
                    _add_image_centered(ws, xli, col, r, px_w, px_w)
                except Exception:
                    ws[f"{col}{r}"] = "—"
            if not plan.aligned:
                note = ws.cell(row=r, column=4, value=K.REREVIEW_MAP_NOT_ALIGNED)
                note.alignment = Alignment(wrap_text=True, vertical="center")
        self._prog_done = base + len(slots)

    # ------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Recipe 분류 (Defect 추출)
# ---------------------------------------------------------------------------
def recipe_of(path) -> tuple[Optional[int], str]:
    """사진 1장의 Recipe — ``(코드, 이름)``.  모르면 ``(None, 'Recipe 없음')``.

    출처는 **Surface.flt 의 recipe**(사용자 결정) — D열 정보에 나오는 값과 같은
    생산자(:func:`coords.geometry.resolve`)다.  표기는 이름만(사용자 결정)이고,
    이름 파일(`RecipesInfo.ini` 등)이 없어 이름을 못 읽으면 코드로 대신한다."""
    from ..coords import geometry
    res = geometry.resolve(Path(path))
    if res.status != "ok" or res.geometry is None:
        return None, i18n.KO.EXTRACT_RECIPE_NONE
    g = res.geometry
    return g.recipe, (g.recipe_name
                      or i18n.KO.EXTRACT_RECIPE_CODE_FMT.format(code=g.recipe))


def recipe_groups(paths) -> list[tuple[str, list[Path]]]:
    """사진들을 Recipe **이름**별로 묶는다 — ``[(이름, 사진들)]``.

    순서: Recipe 코드 오름차순, 'Recipe 없음' 은 맨 끝(사용자 결정: 빠지는 사진 없이
    한 그룹으로 모은다).  그룹 안의 사진 순서는 들어온 순서 그대로다."""
    groups: dict[str, list[Path]] = {}
    order: dict[str, tuple] = {}
    for p in paths:
        code, label = recipe_of(p)
        groups.setdefault(label, []).append(Path(p))
        key = (1, 0) if code is None else (0, code)
        order[label] = min(order.get(label, key), key)
    return sorted(groups.items(), key=lambda kv: (order[kv[0]], kv[0]))


# Recipe 나란히 배치에서 '같은 결함' 으로 보고 같은 행에 두는 거리 — 미만(사용자 결정).
PAIR_TOL_UM = 150.0


def defect_position(path):
    """사진의 결함 위치 — 거리 비교용.  모르면 ``None``.

    · Camtek: 절대 stage 좌표 ``("abs", X, Y)`` µm (INI 의 X/Y 또는 점표기 파일명)
    · 그 밖(KLA·LIVE 파일명): die 좌표 ``("die", col, row, x, y)`` — **같은 die 끼리만**
      비교한다(die 원점을 모르면 다른 die 와의 거리를 알 수 없다)."""
    from .. import coords
    from ..coords import abs_coord
    xy = abs_coord.absolute_xy(Path(path))
    if xy is not None:
        return ("abs", float(xy[0]), float(xy[1]))
    c = coords.resolve(Path(path))
    if c is None:
        return None
    return ("die", c.col, c.row, float(c.x), float(c.y))


def _pos_distance(a, b) -> float:
    import math
    if a is None or b is None or a[0] != b[0]:
        return math.inf
    if a[0] == "abs":
        return math.hypot(a[1] - b[1], a[2] - b[2])
    if a[1:3] != b[1:3]:
        return math.inf
    return math.hypot(a[3] - b[3], a[4] - b[4])


def pair_rows(lists, position, tol: float = PAIR_TOL_UM) -> list[list]:
    """Recipe 별 사진 목록 → 행 목록.  행 = Recipe 마다 사진 하나 또는 ``None``.

    1) 서로 다른 Recipe 의 두 사진이 ``tol`` **미만**이면 같은 결함으로 보고 한 행에
       모은다.  가까운 쌍부터 1:1 로 확정한다(한 사진은 한 행에만).  Recipe 가 셋
       이상이면 행에 들어 있는 **모든** 사진과 ``tol`` 미만일 때만 합류한다.
    2) 그렇게 모인 행을 위에(왼쪽 Recipe 의 순서대로) 둔다.
    3) 짝이 없는 사진은 **한 장이 한 행**이다(그 Recipe 칸만 차고 나머지는 ``None``) —
       150µm 이상 떨어진 사진끼리 한 행에 섞이지 않게(사용자 결정).  Recipe 순서 →
       들어온 순서(파일명)로 놓는다.
    순수 함수 — ``position(path)`` 만 주입받아 헤드리스로 테스트한다."""
    pos = [[position(p) for p in col] for col in lists]
    edges = []
    for a in range(len(lists)):
        for b in range(a + 1, len(lists)):
            for i, pa in enumerate(pos[a]):
                for j, pb in enumerate(pos[b]):
                    d = _pos_distance(pa, pb)
                    if d < tol:
                        edges.append((d, a, i, b, j))
    edges.sort()
    rows: list[dict] = []
    where: dict = {}

    def fits(r: dict, col: int, idx: int) -> bool:
        return col not in r and all(
            _pos_distance(pos[c][k], pos[col][idx]) < tol for c, k in r.items())

    for _d, a, i, b, j in edges:
        ra, rb = where.get((a, i)), where.get((b, j))
        if ra is None and rb is None:
            where[(a, i)] = where[(b, j)] = len(rows)
            rows.append({a: i, b: j})
        elif ra is not None and rb is None and fits(rows[ra], b, j):
            rows[ra][b] = j
            where[(b, j)] = ra
        elif rb is not None and ra is None and fits(rows[rb], a, i):
            rows[rb][a] = i
            where[(a, i)] = rb
    rows.sort(key=lambda r: min(r.items()))
    out = [[lists[c][r[c]] if c in r else None for c in range(len(lists))]
           for r in rows]
    for c, col in enumerate(lists):
        for k, p in enumerate(col):
            if (c, k) not in where:
                out.append([p if cc == c else None for cc in range(len(lists))])
    return out


def _safe_sheet_title(name: str, taken: set) -> str:
    """엑셀 시트 이름 규칙(31자·금지문자·중복 불가)에 맞춘다."""
    base = re.sub(r'[:\\/?*\[\]]', "_", str(name)).strip() or "Recipe"
    base = base[:31]
    title, n = base, 2
    while title in taken:
        tail = f" ({n})"
        title = base[:31 - len(tail)] + tail
        n += 1
    return title


# ---------------------------------------------------------------------------
# 사진 ↔ 셀 크기 정합 헬퍼
# ---------------------------------------------------------------------------
# Excel 의 column width 는 ‘기본 폰트의 0 자리 글자 수’ 단위라 직접 px 변환이
# 까다롭다.  Calibri 11pt 기준 1 unit ≈ 7 px 정도가 일반 통용 근사값.
# row height 는 pt 단위이므로 96 DPI 환산 (1pt = 4/3 px).
def _col_width_to_px(width_units: float) -> int:
    return max(8, int(round((float(width_units) or 0) * 7.0)))


def _row_height_to_px(height_pt: float) -> int:
    return max(8, int(round((float(height_pt) or 0) * 4.0 / 3.0)))


def _fit_to_cell(xli, cell_w_px: int, cell_h_px: int) -> None:
    """openpyxl 의 Image 를 셀 크기에 ‘비율 유지 + 한쪽 변 가득’ 으로 맞춤.

    가로/세로 중 비율상 먼저 셀에 닿는 변이 cell 의 변 길이에 정확히 일치하고
    반대 변은 남는 여백이 생긴다 (사용자 요청: ‘가로나 세로가 셀에 딱 들어맞을
    때까지 크게’).
    """
    try:
        w = float(xli.width)
        h = float(xli.height)
    except Exception:
        return
    if w <= 0 or h <= 0:
        return
    scale = min(cell_w_px / w, cell_h_px / h)
    if scale <= 0:
        return
    xli.width = max(1, int(round(w * scale)))
    xli.height = max(1, int(round(h * scale)))


def _add_image_centered(ws, xli, col_letter: str, row: int,
                        cell_w_px: int, cell_h_px: int) -> None:
    """``_fit_to_cell`` 로 맞춘 이미지를 셀 안에 **중앙 정렬**로 삽입.

    openpyxl 기본 동작은 셀 좌상단 고정이라 비율상 남는 여백이 한쪽(우/하)에
    몰려 사진이 작아 보인다.  ``OneCellAnchor`` + 오프셋으로 남는 여백을 양쪽에
    균등 분배해 시각적으로 ‘셀에 가득’ 차도록 한다 (크롭/왜곡 없음).
    """
    from openpyxl.drawing.spreadsheet_drawing import (AnchorMarker,
                                                      OneCellAnchor)
    from openpyxl.drawing.xdr import XDRPositiveSize2D
    from openpyxl.utils import column_index_from_string
    from openpyxl.utils.units import pixels_to_EMU

    img_w = int(xli.width)
    img_h = int(xli.height)
    x_off = max(0, (cell_w_px - img_w) // 2)
    y_off = max(0, (cell_h_px - img_h) // 2)
    marker = AnchorMarker(
        col=column_index_from_string(col_letter) - 1,
        colOff=pixels_to_EMU(x_off),
        row=int(row) - 1,
        rowOff=pixels_to_EMU(y_off),
    )
    ext = XDRPositiveSize2D(pixels_to_EMU(img_w), pixels_to_EMU(img_h))
    xli.anchor = OneCellAnchor(_from=marker, ext=ext)
    ws.add_image(xli)


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
