"""이전 결과 엑셀 → 기준 폴더.

앱이 저장한 엑셀(매칭 결과 · Defect 추출)에서 **기준 쪽** 사진과 좌표를 꺼내
``<캐시>/excel_ref/<해시>/<슬롯>/<파일명>`` 폴더로 푼다.  이 폴더를 기준 폴더로 쓰면
스캔 · 선별 · 매칭이 폴더와 똑같이 돈다.  좌표는 루트의 사이드카(``coords.excel_ref``)가 준다.

엑셀에 남는 것 (``workers/exporter.py``):
  · B열 = 슬롯명(+ 아래 줄 ``(#N)`` · KLA 하위폴더명)
  · 기준 사진 = C열 그림(축소본) — 사진 칸의 셀 글자 또는 옆 '정보' 칸 = 파일명 + 계측 + 좌표 줄
  · 원본 경로 · 원본 해상도 · 레시피 화면 번호 시프트는 남지 않는다.

⚠ 좌표는 엑셀에 **화면 번호(시프트 적용)** 로 적혀 있어 그 값을 그대로 ``col``/``row`` 로 쓴다
(시프트 0 으로 간주).  시프트가 0 이 아닌 레시피는 ±1 이웃 게이트가 받아 주는 범위다.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import i18n
from ..coords.excel_ref import SIDECAR_NAME, photo_key

__all__ = ["ExcelRef", "load_excel_ref", "parse_coord_lines", "ExcelRefError"]

_IMG_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
_SKIP_SHEETS = (i18n.KO.WAFER_MAP_SHEET, i18n.KO.SLOT_MISMATCH_SHEET,
                i18n.KO.MULTI_VAL_SUMMARY_SHEET)
_FIRST_DATA_ROW = 3
_PHOTO_COL = 2                 # C (0-based) — 기준 사진 열
_NUM = r"(-?\d+(?:\.\d+)?)"
# 줄 모양은 ko.py 의 *_FMT 와 같다(test_excel_ref 가 서로 맞는지 확인한다).
_RE_COLROW = re.compile(rf"^col\s+(-?\d+)\s*/\s*row\s+(-?\d+)\s*$")
_RE_XY = re.compile(rf"^x\s+{_NUM}\s*/\s*y\s+{_NUM}\s*㎛\s*$")
_RE_ABS = re.compile(rf"^wafer\s+x\s+{_NUM}\s*/\s*y\s+{_NUM}\s*㎛\s*$")
_RE_NATIVE = re.compile(rf"^KLA원본\s+x\s+{_NUM}\s*/\s*y\s+{_NUM}\s*㎛\s*$")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ExcelRefError(Exception):
    """엑셀을 기준으로 쓸 수 없을 때(사유가 사용자에게 그대로 보인다)."""


@dataclass
class ExcelRef:
    root: Path                 # 풀어 둔 기준 폴더
    machine: str               # 머리의 기준 호기(없으면 "")
    photos: int = 0
    slots: int = 0
    with_coord: int = 0
    skipped: int = 0           # 같은 슬롯·파일명이 겹쳐 건너뛴 그림
    slot_names: list = field(default_factory=list)


def parse_coord_lines(lines) -> Optional[dict]:
    """셀 글자의 줄들 → ``{col,row,x,y,source[,native_x,native_y]}`` 또는 None.

    · ``col / row`` + ``x / y``  → Camtek 의 die 내부 좌표(``camtek_live``: 절대좌표로
      되돌릴 수 없는 소스라 절대좌표 폴더와 섞이면 매칭하지 않고 경고한다)
    · + ``KLA원본`` 줄 → ``kla``
    · ``wafer x / y`` 만 → 절대 wafer 좌표(``camtek_abs``, col/row 는 0)
    """
    col = row = x = y = ax = ay = nx = ny = None
    for raw in lines:
        s = raw.strip()
        if (m := _RE_COLROW.match(s)):
            col, row = int(m.group(1)), int(m.group(2))
        elif (m := _RE_NATIVE.match(s)):
            nx, ny = float(m.group(1)), float(m.group(2))
        elif (m := _RE_ABS.match(s)):
            ax, ay = float(m.group(1)), float(m.group(2))
        elif (m := _RE_XY.match(s)):
            x, y = float(m.group(1)), float(m.group(2))
    if col is not None and x is not None:
        rec = {"col": col, "row": row, "x": x, "y": y,
               "source": "kla" if nx is not None else "camtek_live"}
        if nx is not None:
            rec["native_x"], rec["native_y"] = nx, ny
        return rec
    if ax is not None:
        return {"col": 0, "row": 0, "x": ax, "y": ay, "source": "camtek_abs"}
    return None


def _text(v) -> str:
    return "" if v is None else str(v)


def _slot_of(cell_text: str) -> str:
    """B열 → 슬롯명.  첫 줄이 슬롯명이고 아래 ``(#N)`` · KLA 하위폴더명은 버린다."""
    first = cell_text.replace("\r", "").split("\n", 1)[0].strip()
    return _BAD_CHARS.sub("_", first).strip(" .") or "slot"


def _photo_cols(ws) -> list[tuple[int, Optional[int]]]:
    """[(사진 열, 정보 열 또는 None)] (0-based).  2행 머리가 '정보' 인 열의 왼쪽이 사진 열.
    없으면 C 하나(매칭 요약 · 미매칭 · 한 시트 추출)."""
    info_cols = [c.column - 1 for c in ws[2]
                 if _text(c.value).strip() == i18n.KO.EXTRACT_INFO_HEADER]
    if not info_cols:
        return [(_PHOTO_COL, None)]
    return [(c - 1, c) for c in info_cols if c >= 1]


def _cache_dir(xlsx: Path) -> Path:
    from .paths import cache_root
    st = xlsx.stat()
    key = hashlib.sha1(
        f"{xlsx.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16]
    return cache_root() / "excel_ref" / key


def load_excel_ref(xlsx, progress: Optional[Callable] = None) -> ExcelRef:
    """엑셀을 기준 폴더로 푼다(같은 파일이 안 바뀌었으면 풀어 둔 것을 재사용).

    ``progress(done, total)`` — 그림을 풀 때마다 부른다.  백그라운드 스레드에서 부를 것."""
    from openpyxl import load_workbook

    xlsx = Path(xlsx)
    if not xlsx.is_file():
        raise ExcelRefError(i18n.KO.EXCEL_REF_ERR_NOFILE)
    root = _cache_dir(xlsx)
    meta = root / ".meta.json"
    if meta.is_file():                         # 이미 푼 적이 있다
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
            return ExcelRef(root=root, **d)
        except (OSError, ValueError, TypeError):
            pass

    try:
        wb = load_workbook(xlsx)
    except Exception as e:                     # 깨진 파일 · 비밀번호 등
        raise ExcelRefError(i18n.KO.EXCEL_REF_ERR_OPEN) from e

    tmp = root.with_name(root.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    sidecar: dict = {}
    seen: set = set()
    slots: set = set()
    machine = ""
    skipped = 0
    jobs = []                                  # (ws, image, row, photo_col, info_col)
    anchors: dict = {}                         # 시트 → 그림이 놓인 (행, 열) 집합
    for ws in wb.worksheets:
        if ws.title.startswith(_SKIP_SHEETS):
            continue
        cols = dict(_photo_cols(ws))
        anchors[ws.title] = set()
        for img in getattr(ws, "_images", []):
            try:
                anchors[ws.title].add((img.anchor._from.row + 1, img.anchor._from.col))
            except AttributeError:
                pass
        for img in getattr(ws, "_images", []):
            try:
                a = img.anchor._from
                r, c = a.row + 1, a.col          # r: 1-based 행, c: 0-based 열
            except AttributeError:
                continue
            if r >= _FIRST_DATA_ROW and c in cols and \
                    isinstance(ws.cell(row=r, column=1).value, int):
                jobs.append((ws, img, r, c, cols[c]))
        if not machine:
            machine = _text(ws.cell(row=2, column=_PHOTO_COL + 1).value).strip()
    total = len(jobs)
    n = 0
    for ws, img, r, c, info_c in jobs:
        n += 1
        if progress:
            progress(n, total)
        slot = _slot_of(_text(ws.cell(row=r, column=2).value))
        body = _text(ws.cell(row=r, column=c + 1).value)
        lines = [s for s in body.replace("\r", "").split("\n") if s.strip()]
        coord = parse_coord_lines(lines[1:])
        if info_c is None and not lines and (r, c + 1) not in anchors[ws.title]:
            info_c = c + 1                               # 미매칭 행: 사진 옆 칸이 정보다
        if coord is None and info_c is not None:         # 추출 · 미매칭: 옆 '정보' 칸
            lines = [s for s in _text(ws.cell(row=r, column=info_c + 1).value)
                     .replace("\r", "").split("\n") if s.strip()]
            coord = parse_coord_lines(lines[1:])
        name = _BAD_CHARS.sub("_", lines[0].strip()) if lines else ""
        if not name:
            name = f"사진_{n:04d}"
        if Path(name).suffix.lower() not in _IMG_SUFFIXES:
            name += ".jpg"
        key = (slot, name.lower())
        if key in seen:                                  # 요약 · 미매칭 시트에 겹친 행
            skipped += 1
            continue
        seen.add(key)
        try:
            data = img._data()
        except Exception:
            skipped += 1
            continue
        d = tmp / slot
        d.mkdir(exist_ok=True)
        (d / name).write_bytes(data)
        slots.add(slot)
        if coord is not None:
            sidecar[photo_key(slot, Path(name).stem)] = coord

    if not seen:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExcelRefError(i18n.KO.EXCEL_REF_ERR_NOPHOTO)
    (tmp / SIDECAR_NAME).write_text(
        json.dumps({"version": 1, "photos": sidecar}, ensure_ascii=False),
        encoding="utf-8")
    info = {"machine": machine, "photos": len(seen), "slots": len(slots),
            "with_coord": len(sidecar), "skipped": skipped,
            "slot_names": sorted(slots)}
    (tmp / ".meta.json").write_text(json.dumps(info, ensure_ascii=False),
                                    encoding="utf-8")
    shutil.rmtree(root, ignore_errors=True)
    tmp.rename(root)
    return ExcelRef(root=root, **info)
