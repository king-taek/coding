"""Overkill 튜닝 — 고른 것(꼭 잡기 · Overkill)을 CSV 한 장으로 저장하고 다시 연다(현택님 2026-11-24 안 B).

엑셀에서 열리고(UTF-8 BOM), 앱에서 다시 열면 같은 Defect 가 골라진다.  다른 사람에게 넘기거나 나중에 다시 고를 때 쓴다.
다시 고를 때는 **웨이퍼 폴더 이름 · Defect 번호(Surface.flt 순서) · 좌표**가 모두 맞는 것만 고른다 —
폴더 이름만 같은 다른 스캔의 Defect 를 잘못 고르지 않게(좌표는 :data:`XY_TOL_UM` 안).
열 이름은 파일 형식이라 화면 문구(ko.py)와 따로 둔다 — 바꾸면 예전 파일을 못 읽는다.
"""

from __future__ import annotations

import csv
from pathlib import Path

DEFECT, OVERKILL = "Defect", "Overkill"
HEAD = ("표시", "파일명", "웨이퍼", "LOT", "슬롯", "Zone", "번호", "X(µm)", "Y(µm)",
        "Area(µm²)", "Width(µm)", "Length(µm)", "Contrast")
XY_TOL_UM = 1.0


def write_csv(path: Path, rows) -> None:
    """``rows`` = HEAD 순서의 값 묶음들."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        out = csv.writer(f)
        out.writerow(HEAD)
        out.writerows(rows)


def read_csv(path: Path) -> list:
    """→ ``[(표시, 웨이퍼, 번호, x, y)]``.  표시 · 번호 · 좌표를 못 읽는 줄은 뺀다."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        head = next(rd, None)
        if head is None:
            return []
        col = {name: i for i, name in enumerate(h.strip() for h in head)}
        need = ("표시", "웨이퍼", "번호", "X(µm)", "Y(µm)")
        if any(n not in col for n in need):
            raise ValueError("열 이름이 다릅니다")
        out = []
        for r in rd:
            try:
                mark, wafer = r[col["표시"]].strip(), r[col["웨이퍼"]].strip()
                idx = int(r[col["번호"]])
                x, y = float(r[col["X(µm)"]]), float(r[col["Y(µm)"]])
            except (IndexError, ValueError):
                continue
            if mark in (DEFECT, OVERKILL) and wafer:
                out.append((mark, wafer, idx, x, y))
        return out


def match(entries, defects, wafer_of) -> tuple:
    """CSV 줄 → ``(꼭 잡기 Defect 들, Overkill Defect 들)``.  ``wafer_of(d)`` = 그 Defect 의 웨이퍼 폴더 이름."""
    by = {(wafer_of(d), d.index): d for d in defects}
    picked, over = [], []
    for mark, wafer, idx, x, y in entries:
        d = by.get((wafer, idx))
        if d is None or abs(d.x - x) > XY_TOL_UM or abs(d.y - y) > XY_TOL_UM:
            continue
        (picked if mark == DEFECT else over).append(d)
    return picked, over
