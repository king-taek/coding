"""Classify(사람이 검토로 바꾼 분류)를 꼭 잡기 · Overkill 로 가져온다 — 현택님 2026-11-24 안 A.

사람이 바꾼 Defect 만 본다(지금 분류 ≠ 첫 분류).  바꾼 분류의 종류가 Good(Accept · Good)이면 Overkill,
그 밖은 꼭 잡기가 기본이다.  손대지 않은 Defect 는 쓰지 않는다.
Conder 폴더에는 Surface.flt 가 없어 Classify 를 읽을 수 없다(장비 Scanresult 폴더만).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFECT, OVERKILL, SKIP = "defect", "overkill", "skip"


@dataclass(frozen=True)
class ClassRow:
    cls: int            # 사람이 바꾼 분류 번호(절댓값)
    name: str
    good: bool          # 그 분류의 종류가 Good(ManReClassify.ini)
    defects: tuple

    @property
    def default(self) -> str:
        return OVERKILL if self.good else DEFECT


def changed(d) -> bool:
    """검토로 분류가 바뀌었나(Accept 포함) — 지금 분류는 사람이 바꾸면 음수로 적힌다."""
    return abs(d.cls) != d.bin


def class_rows(defects, classes: dict) -> list:
    """바뀐 Defect 를 바뀐 분류별로 — 많은 순."""
    groups: dict = {}
    for d in defects:
        if changed(d):
            groups.setdefault(abs(d.cls), []).append(d)
    rows = []
    for c, ds in groups.items():
        info = classes.get(c)
        rows.append(ClassRow(c, info.name if info else f"class {c}", bool(info and info.good), tuple(ds)))
    rows.sort(key=lambda r: (-len(r.defects), r.cls))
    return rows


def split(rows, choice: dict, key_of) -> tuple:
    """``choice`` = {분류: DEFECT | OVERKILL | SKIP}(없으면 기본) → ``(꼭 잡기, Overkill)`` — 각 ``{key_of(d): d}``."""
    picked: dict = {}
    over: dict = {}
    for r in rows:
        out = {DEFECT: picked, OVERKILL: over}.get(choice.get(r.cls, r.default))
        if out is not None:
            for d in r.defects:
                out[key_of(d)] = d
    return picked, over
