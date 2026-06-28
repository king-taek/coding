#!/usr/bin/env python3
"""KLA .001(KLARF) 결함 파일 구조 분석 — 단일 md 출력.

KLA 자재는 Surface.flt 가 없고, 대신 검사 결과가 .001(KLARF ASCII)에 들어 있다.
미매칭 행에 표기할 결함 정보(area/size/위치/분류)가 이 파일 어디에 있는지 확인하기
위한 **탐색 덤프**다.  NAS 접근 가능한 PC 에서 실행한다.

각 .001 을 읽어 (1) 헤더 키, (2) DefectRecordSpec 컬럼 정의, (3) DefectList 레코드
(컬럼 라벨 포함), (4) SummaryList, (5) geometry 후보 컬럼(XSIZE/YSIZE/DEFECTAREA/
DSIZE 등), (6) 원본 앞부분 verbatim 을 **AOI_KLA분석.md 한 개**로 떨군다.  stdlib 만 사용.

사용:  python dev/analyze_kla_001.py                 (임베드된 4건)
       python dev/analyze_kla_001.py 경로1 경로2 ... --out 결과.md
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# ── 분석 대상(사용자 제공) ───────────────────────────────────────────────
FILES = [
    r"N:\2025-09-12\RDL4_TTTM_STR\2025-09-12-18-31_3\RDL_RDL4_TTTM_STR.001",
    r"T:\2026-02-26\FDH_RDL4\2026-02-25-20-59_2\T354_FDH_RDL4_126042010.02.001",
    r"U:\2025-12-01\T254INT.254@6323\2025-12-01-06-59_10\RDL_T254INT.254@6323_00LH3106XYB3.001",
    r"T:\2026-05-20\TB500INT.398@6322\2026-05-20-12-42_8\4DT-TB500-H-M1_TB500INT.398@6322.001",
]

# geometry/위치/분류로 쓸 만한 KLARF 컬럼(대문자 비교). 발견되면 강조.
_GEOM_HINTS = ("XSIZE", "YSIZE", "DEFECTAREA", "AREA", "DSIZE", "DIAMETER",
               "POLARITY", "DEFECTAREADIESPACE", "RADIUS")
_LOC_HINTS = ("XREL", "YREL", "XINDEX", "YINDEX", "X", "Y")
_CLS_HINTS = ("CLASSNUMBER", "ROUGHBINNUMBER", "FINEBINNUMBER", "TEST",
              "CLUSTERNUMBER", "IMAGECOUNT")

# 헤더에서 뽑아 요약에 보여줄 키(있으면).
_HEADER_KEYS = ("FileVersion", "FileTimestamp", "LotID", "WaferID", "DeviceID",
                "StepID", "SetupID", "ResultTimestamp", "SampleType",
                "DiePitch", "DieOrigin", "SampleCenterLocation", "Slot",
                "InspectionStationID", "ResultsID")

_SPEC = re.compile(r"(\w*RecordSpec)\s+(\d+)\s+(.*?);", re.IGNORECASE | re.DOTALL)
_LIST = re.compile(r"\b(\w*List)\b(.*?);", re.IGNORECASE | re.DOTALL)


def read_text(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def header_value(txt: str, key: str):
    """`Key val val ... ;` 형태의 첫 값을 반환(세미콜론 전까지)."""
    m = re.search(r"(?im)^\s*" + re.escape(key) + r"\b[ \t]+(.*?)\s*;", txt)
    return m.group(1).strip() if m else None


def parse_specs(txt: str):
    """{SpecName: [컬럼,...]}.  DefectRecordSpec/SummarySpec 등."""
    out = {}
    for name, _n, cols in _SPEC.findall(txt):
        toks = [t for t in re.split(r"[ \t\r\n]+", cols.strip()) if t]
        out[name] = toks
    return out


def parse_list(txt: str, list_name: str, ncols: int):
    """List 블록을 토큰화해 ncols 단위로 끊어 레코드 리스트 반환."""
    m = re.search(r"\b" + re.escape(list_name) + r"\b(.*?);",
                  txt, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    toks = [t for t in re.split(r"[ \t\r\n]+", m.group(1).strip()) if t]
    if ncols <= 0:
        return [toks]
    return [toks[i:i + ncols] for i in range(0, len(toks), ncols)]


def hint_cols(cols, hints):
    up = [c.upper() for c in cols]
    return [cols[i] for i, c in enumerate(up) if c in hints]


def analyze(path: Path, max_rows: int, raw_lines: int):
    L = []
    L.append(f"\n## 파일: `{path.name}`")
    L.append(f"- 전체경로: `{path}`")
    txt = read_text(path)
    if txt is None:
        L.append("> ❌ 읽기 실패 — 경로/드라이브(연결) 확인")
        return L, {}
    lines = txt.splitlines()
    L.append(f"- 크기 {len(txt.encode('utf-8', 'replace'))} bytes, 라인 {len(lines)}개")

    # 헤더 요약
    hdr = [(k, header_value(txt, k)) for k in _HEADER_KEYS]
    hdr = [(k, v) for k, v in hdr if v is not None]
    if hdr:
        L.append("\n**헤더**")
        L.append("\n| key | value |\n| --- | --- |")
        for k, v in hdr:
            L.append(f"| {k} | {v} |")

    # Spec(컬럼 정의)
    specs = parse_specs(txt)
    defect_cols = None
    for name, cols in specs.items():
        L.append(f"\n**{name}** — {len(cols)}개 컬럼")
        L.append(f"\n`{' '.join(cols)}`")
        if name.lower().startswith("defect"):
            defect_cols = cols
            g = hint_cols(cols, _GEOM_HINTS)
            loc = hint_cols(cols, _LOC_HINTS)
            cls = hint_cols(cols, _CLS_HINTS)
            L.append(f"\n- 📐 geometry 후보: **{g or '없음'}**")
            L.append(f"- 📍 위치 후보: {loc or '없음'}")
            L.append(f"- 🏷 분류 후보: {cls or '없음'}")

    # DefectList 레코드
    found_geom = {}
    if defect_cols:
        recs = parse_list(txt, "DefectList", len(defect_cols))
        L.append(f"\n**DefectList** — 레코드 {len(recs)}개 (컬럼 {len(defect_cols)}개 단위로 분할)")
        if recs:
            head = defect_cols
            L.append("\n| " + " | ".join(head) + " |")
            L.append("| " + " | ".join("---" for _ in head) + " |")
            for r in recs[:max_rows]:
                cells = (r + [""] * len(head))[:len(head)]
                L.append("| " + " | ".join(cells) + " |")
            if len(recs) > max_rows:
                L.append(f"\n> … 외 {len(recs) - max_rows}개 레코드 생략")
            # geometry 컬럼 값 분포(첫 레코드 + min/max) 간단 요약
            gcols = hint_cols(defect_cols, _GEOM_HINTS)
            for gc in gcols:
                ci = defect_cols.index(gc)
                vals = []
                for r in recs:
                    if ci < len(r):
                        try:
                            vals.append(float(r[ci]))
                        except ValueError:
                            pass
                if vals:
                    found_geom[gc] = (min(vals), max(vals), vals[0])

    if found_geom:
        L.append("\n**geometry 컬럼 값 범위**")
        L.append("\n| 컬럼 | 첫값 | min | max |\n| --- | --- | --- | --- |")
        for gc, (mn, mx, first) in found_geom.items():
            L.append(f"| {gc} | {first:g} | {mn:g} | {mx:g} |")

    # 기타 List(Summary 등)
    for name, cols in specs.items():
        if name.lower().startswith("defect"):
            continue
        list_name = name[:-4] + "List" if name.lower().endswith("spec") else None
        if not list_name:
            continue
        recs = parse_list(txt, list_name, len(cols))
        if recs:
            L.append(f"\n**{list_name}** — {len(recs)}개")
            L.append("\n| " + " | ".join(cols) + " |")
            L.append("| " + " | ".join("---" for _ in cols) + " |")
            for r in recs[:max_rows]:
                cells = (r + [""] * len(cols))[:len(cols)]
                L.append("| " + " | ".join(cells) + " |")

    # 원본 앞부분 verbatim(파서가 놓친 구조까지 사람이 직접 확인)
    L.append(f"\n<details><summary>원본 앞 {raw_lines}줄</summary>\n")
    L.append("```")
    L.extend(lines[:raw_lines])
    L.append("```")
    L.append("</details>")
    return L, {"cols": defect_cols, "geom": list(found_geom)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="분석할 .001 경로(없으면 임베드 4건)")
    ap.add_argument("--out", default="AOI_KLA분석.md")
    ap.add_argument("--max-rows", type=int, default=20, help="표에 보일 레코드 수")
    ap.add_argument("--raw-lines", type=int, default=120, help="verbatim 줄 수")
    args = ap.parse_args(argv)

    targets = args.paths or FILES
    out = ["# AOI KLA .001(KLARF) 결함 파일 구조 분석  (단일 md)\n",
           "> KLA 자재(Surface.flt 없음)의 .001 에서 미매칭 결함 표기에 쓸 정보"
           "(area/size/위치/분류)가 어디 있는지 확인하는 탐색 덤프.\n"]

    summaries = []
    for p in targets:
        body, info = analyze(Path(p), args.max_rows, args.raw_lines)
        out.extend(body)
        summaries.append((Path(p).name, info))

    # 맨 위 요약: 파일별 컬럼 수 + geometry 후보
    head = ["\n## 요약\n", "| 파일 | DefectRecordSpec 컬럼 | geometry 후보 |",
            "| --- | --- | --- |"]
    for name, info in summaries:
        cols = info.get("cols")
        geom = info.get("geom")
        head.append(f"| {name} | {len(cols) if cols else '-'} | "
                    f"{', '.join(geom) if geom else '-'} |")
    out[2:2] = head

    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[OUT] {args.out}  ({len(targets)}개 파일 분석)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
