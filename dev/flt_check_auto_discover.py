#!/usr/bin/env python3
"""AOI Surface.flt geometry 자동탐색 검증 — 실제 NAS 폴더에서 빠르게 표집.

회사 노트북에서 실행한다. AOI Scanresult root 들을 빠른 전략으로 탐색해 `Surface.flt`
폴더를 모으고, 각 폴더에서 결함 geometry(area/width/length/contrast/zone/recipe)를
확정 스키마로 추출, `ColorImageGrabingInfo.ini` 좌표로 image↔record 매칭한 뒤,
다중 시트 Excel(없으면 CSV) 로 정리한다.

핵심 원칙(실측 보고서 b113101a 반영):
  · UI 열(ui_*)은 **자동 채우지 않는다** — 사람이 AOI UI 를 보고 직접 입력할 빈칸.
  · contrast 분포는 zone 만이 아니라 **zone×recipe 교차**로 본다(0 은 recipe/제품 의존).
  · root 한 곳에서 cap 이 소진되지 않도록 `--per-root-max` 로 root 별 상한을 둔다.
  · 좌표 매칭 실패 row 는 (aux/revisit 후보 vs 타 die)로 분류해 기록한다.

확정 스키마(TB500, 실측 84건+실NAS 116폴더로 확인): 레코드 152byte, 헤더 없음, LE.
  ActualX=24 d, ActualY=32 d, area=80 d, BlobBreadth=88 d, BlobFeretMax=104 d,
  Contrast=136 d, zone=61 B, recipe=62 B.
환산: area_um2=area×0.5929, width_um=BlobBreadth×0.77, length_um=BlobFeretMax×0.77.

사용:
  python flt_check_auto_discover.py [root ...] [옵션]
    (root 생략 시 설명서 §7.1 의 14개 기본 root 사용)
  옵션: --max-dirs N(전역 상한, 기본 100) --per-root-max N(root별 상한, 기본 20)
        --latest-lots N(디렉터리 레벨별 최신 N개만, 기본 3) --max-depth N(기본 5)
        --all(TB500 외 폴더도) --deep(레벨·breadth 제한 해제) --timeout SEC(기본 1200)
        --tol UM(매칭 허용, 기본 5) --out PATH(xlsx)
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import struct
import sys
import time
from collections import defaultdict
from pathlib import Path

# ── 확정 스키마(인라인 — 저장소 밖에서도 단독 실행 가능) ───────────────────
BYTE_ORDER = "<"
RECSIZE = 152
HEADER = 0
FIELDS = {  # name: (offset, fmt)
    "actual_x": (24, "d"), "actual_y": (32, "d"),
    "area": (80, "d"), "blob_breadth": (88, "d"), "blob_feret_max": (104, "d"),
    "contrast": (136, "d"), "zone": (61, "B"), "recipe": (62, "B"),
}
AREA_F, LEN_F = 0.5929, 0.77

DEFAULT_ROOTS = [
    r"X:\AOI-3\Scanresult", r"V:\AOI-13\Scanresult", r"V:\AOI-14\Scanresult",
    r"V:\AOI-15\Scanresult", r"V:\AOI-16\Scanresult", r"P:\AOI-17\Scanresult",
    r"P:\AOI-18\Scanresult", r"P:\AOI-19\Scanresult", r"P:\AOI-20\Scanresult",
    r"P:\AOI-21\Scanresult", r"P:\AOI-22\Scanresult", r"P:\AOI-23\Scanresult",
    r"Y:\AOI-24\Scanresult", r"Y:\AOI-25\Scanresult",
]

_INI_NAMES = ("ColorImageGrabingInfo.ini", "ColorImageGrabinginfo.ini")
_SECTION = re.compile(r"\[([^\]]+)\]")
_KV = re.compile(r"^(\w+)\s*=\s*(.+)$", re.M)
_TB500 = re.compile(r"TB500", re.I)


# ── 파싱 ──────────────────────────────────────────────────────────────────
def parse_flt(path):
    """Surface.flt → (records, size, framed_ok). 실패해도 raise 안 함."""
    try:
        data = path.read_bytes()
    except Exception:
        return [], 0, False
    size = len(data)
    recs = []
    pos = HEADER
    while pos + RECSIZE <= size:
        try:
            r = {k: struct.unpack_from(BYTE_ORDER + fmt, data, pos + off)[0]
                 for k, (off, fmt) in FIELDS.items()}
            recs.append(r)
        except struct.error:
            break
        pos += RECSIZE
    return recs, size, (size % RECSIZE == 0)


def find_ini(folder):
    for n in _INI_NAMES:
        p = folder / n
        if p.exists():
            return p
    return None


def parse_ini(path):
    """INI → {stem(lower): (x, y, col, row, recipe_number)}."""
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    out = {}
    parts = _SECTION.split(txt)
    it = iter(parts[1:])
    for name, body in zip(it, it):
        kv = {m.group(1).upper(): m.group(2).strip() for m in _KV.finditer(body)}

        def num(k):
            try:
                return float(kv[k])
            except (KeyError, ValueError):
                return None
        x = num("FAULTX") if num("FAULTX") is not None else num("X")
        y = num("FAULTY") if num("FAULTY") is not None else num("Y")
        if x is not None and y is not None:
            out[Path(name.strip()).stem.lower()] = (
                x, y, kv.get("COL"), kv.get("ROW"), kv.get("RECIPENUMBER"))
    return out


def nearest(recs, x, y):
    best, bi, bd = None, -1, None
    for i, r in enumerate(recs):
        d = math.hypot(r["actual_x"] - x, r["actual_y"] - y)
        if bd is None or d < bd:
            bd, best, bi = d, r, i
    return best, bi, (bd if bd is not None else float("inf"))


# ── 탐색 ──────────────────────────────────────────────────────────────────
def _subdirs(d):
    try:
        return [c for c in d.iterdir() if c.is_dir()]
    except Exception:
        return []


def discover(root, *, tb500_only, latest_lots, max_depth, per_root_max,
             deep, deadline, log):
    """root 에서 Surface.flt 보유 폴더를 빠른 전략으로 수집."""
    root = Path(root)
    if not root.exists():
        log(f"[WARN] root 없음: {root}")
        return []
    # 1단계: TB500 제품 폴더(아니면 전체)
    children = _subdirs(root)
    products = [c for c in children if (not tb500_only) or _TB500.search(c.name)]
    # 자식이 아예 없을 때만 root 자체를 대상으로(=root 직하에 wafer 구조).  엄격
    # TB500 모드인데 매칭 자식이 없으면 [] 를 유지(전체 스캔으로 떨어지지 않게).
    if not products and not children:
        products = [root]
    found = []
    for prod in products:
        if len(found) >= per_root_max or time.time() > deadline:
            break
        # prod 아래 BFS — Surface.flt 보유 폴더 수집
        stack = [(prod, 0)]
        while stack and len(found) < per_root_max and time.time() <= deadline:
            d, depth = stack.pop(0)
            if (d / "Surface.flt").exists():
                found.append(d)
                continue  # 그 아래로 더 안 내려감
            if depth >= max_depth:
                continue
            subs = _subdirs(d)
            if not deep:
                # 최신 우선 + 레벨별 breadth 제한
                try:
                    subs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                except Exception:
                    pass
                subs = subs[:latest_lots]
            for s in subs:
                stack.append((s, depth + 1))
    log(f"[DISCOVER] {root} -> Surface.flt 폴더 {len(found)}개")
    return found


# ── 폴더 처리 ─────────────────────────────────────────────────────────────
def process_folder(folder, tol, rows, schema_rows, log):
    folder = Path(folder)
    flt = folder / "Surface.flt"
    recs, size, framed = parse_flt(flt)
    ini_path = find_ini(folder)
    entries = parse_ini(ini_path) if ini_path else {}
    schema_rows.append({
        "folder": folder.name, "folder_path": str(folder),
        "surface_flt_size": size, "size_mod_152": size % RECSIZE,
        "framed_ok": framed, "records": len(recs),
        "ini_entries": len(entries), "ini_exists": ini_path is not None,
        "판단": "OK" if (framed and recs) else "CHECK",
    })
    log(f"[FLT] {folder} / size={size}, %152={size % RECSIZE}, "
        f"records={len(recs)}, ini={len(entries)}")
    if not recs:
        return
    more_imgs = len(entries) > len(recs)
    if not entries:
        # INI 없음 — record 만 덤프(좌표 매칭 불가)
        for i, r in enumerate(recs):
            rows.append(_row(folder, f"rec{i}", i, r, None, None, None,
                             dist=None, note="INI 없음(좌표 매칭 불가)"))
        return
    for stem, (x, y, col, row, rn) in entries.items():
        rec, idx, d = nearest(recs, x, y)
        if rec is None or d > tol:
            # 실패 분류: aux/revisit 후보(이미지>레코드) vs 타 die(nearest 멀음)
            cls = ("aux/revisit 후보(ini>records)" if more_imgs
                   else "타 die(nearest>tol)")
            rows.append(_row(folder, stem, None, None, col, row, rn,
                             dist=d, note=f"매칭실패: {cls}"))
            continue
        rmatch = "" if rn is None else ("Y" if str(rec["recipe"]) == str(rn).strip() else "N")
        rows.append(_row(folder, stem, idx, rec, col, row, rn, dist=d,
                         recipe_match=rmatch))


def _row(folder, stem, idx, rec, col, row, rn, *, dist, note="", recipe_match=""):
    base = {
        "folder": folder.name, "folder_path": str(folder), "image_stem": stem,
        "record_index": "" if idx is None else idx,
        "recipe": "" if rec is None else rec["recipe"],
        "ini_recipe": rn or "", "recipe_match": recipe_match,
        "zone": "" if rec is None else rec["zone"],
        "col": col or "", "row": row or "",
        "match_dist_um": "" if dist is None else round(dist, 3),
        "area_raw_px2": "" if rec is None else round(rec["area"], 5),
        "width_raw_px": "" if rec is None else round(rec["blob_breadth"], 5),
        "length_raw_px": "" if rec is None else round(rec["blob_feret_max"], 5),
        "area_um2": "" if rec is None else round(rec["area"] * AREA_F, 3),
        "width_um": "" if rec is None else round(rec["blob_breadth"] * LEN_F, 3),
        "length_um": "" if rec is None else round(rec["blob_feret_max"] * LEN_F, 3),
        "contrast": "" if rec is None else round(rec["contrast"], 3),
        "note": note,
        # UI 열 — 사람이 AOI UI 보고 직접 채울 빈칸(자동 채우지 않음).
        "ui_area": "", "ui_width": "", "ui_length": "", "ui_contrast": "",
        "ui_zone": "", "ui_recipe": "", "ui_match": "", "ui_note": "",
    }
    return base


# ── 집계 ──────────────────────────────────────────────────────────────────
def zone_recipe_crosstab(rows):
    agg = defaultdict(lambda: [0, 0])  # (zone,recipe) -> [총, contrast0]
    for r in rows:
        if r["record_index"] == "" or r["zone"] == "":
            continue
        key = (r["zone"], r["recipe"])
        agg[key][0] += 1
        if float(r["contrast"] or 0) == 0:
            agg[key][1] += 1
    out = []
    for (zone, recipe), (tot, z) in sorted(agg.items()):
        out.append({"zone": zone, "recipe": recipe, "총개수": tot,
                    "contrast==0": z, "contrast!=0": tot - z,
                    "0비율%": round(100 * z / tot, 1) if tot else 0})
    return out


def ui_shortlist(rows, per_bucket=3):
    """recipe2-zone1 / recipe(기타)-zone1 / zone63 버킷에서 2~3건씩 선별."""
    buckets = {"recipe2 zone1": [], "기타recipe zone1": [], "zone63": []}
    for r in rows:
        if r["record_index"] == "" or r["zone"] == "":
            continue
        z, rc = str(r["zone"]), str(r["recipe"])
        if z == "1" and rc == "2":
            b = "recipe2 zone1"
        elif z == "1":
            b = "기타recipe zone1"
        elif z == "63":
            b = "zone63"
        else:
            continue
        if len(buckets[b]) < per_bucket:
            buckets[b].append(r)
    out = []
    for b, items in buckets.items():
        for r in items:
            out.append({
                "버킷": b, "folder": r["folder"], "image_stem": r["image_stem"],
                "추출_area": r["area_um2"], "추출_width": r["width_um"],
                "추출_length": r["length_um"], "추출_contrast": r["contrast"],
                "추출_zone": r["zone"], "추출_recipe": r["recipe"],
                "UI_area": "", "UI_width": "", "UI_length": "",
                "UI_contrast": "", "UI_zone명": "", "일치?": "",
            })
    return out


# ── 출력 ──────────────────────────────────────────────────────────────────
RESULT_COLS = ["folder", "folder_path", "image_stem", "record_index", "recipe",
               "ini_recipe", "recipe_match", "zone", "col", "row", "match_dist_um",
               "area_raw_px2", "width_raw_px", "length_raw_px",
               "area_um2", "width_um", "length_um", "contrast", "note",
               "ui_area", "ui_width", "ui_length", "ui_contrast",
               "ui_zone", "ui_recipe", "ui_match", "ui_note"]


def write_excel(out, found, schema_rows, rows, logs):
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, headers, dicts):
        ws = wb.create_sheet(title=name[:31])
        ws.append(headers)
        for d in dicts:
            ws.append([d.get(h, "") for h in headers])

    sheet("자동탐색_대상폴더", ["folder_path"], [{"folder_path": str(f)} for f in found])
    sheet("폴더_스키마_정합성",
          ["folder_path", "surface_flt_size", "size_mod_152", "framed_ok",
           "records", "ini_entries", "ini_exists", "판단"], schema_rows)
    sheet("검증결과_전체", RESULT_COLS, rows)
    sheet("zone별_contrast",
          ["zone", "recipe", "총개수", "contrast==0", "contrast!=0", "0비율%"],
          zone_recipe_crosstab(rows))
    sheet("UI수기확인_shortlist",
          ["버킷", "folder", "image_stem", "추출_area", "추출_width", "추출_length",
           "추출_contrast", "추출_zone", "추출_recipe", "UI_area", "UI_width",
           "UI_length", "UI_contrast", "UI_zone명", "일치?"], ui_shortlist(rows))
    matched = [r for r in rows if r["record_index"] != ""]
    failed = [r for r in rows if r["record_index"] == "" and "매칭실패" in r["note"]]
    aux = [r for r in failed if "aux" in r["note"]]
    summary = [
        {"항목": "Surface.flt 폴더 수", "값": len(found)},
        {"항목": "전체 결과 row", "값": len(rows)},
        {"항목": "좌표 매칭 성공", "값": len(matched)},
        {"항목": "매칭 실패(합계)", "값": len(failed)},
        {"항목": "  └ aux/revisit 후보(ini>records)", "값": len(aux)},
        {"항목": "  └ 타 die(nearest>tol)", "값": len(failed) - len(aux)},
        {"항목": "주의", "값": "ui_* 는 빈칸 — 사람이 AOI UI 보고 직접 채울 것"},
    ]
    sheet("요약_결론", ["항목", "값"], summary)
    sheet("실행로그", ["log"], [{"log": l} for l in logs])
    wb.save(out)
    return out


def write_csv(out_base, rows, logs):
    csv_path = out_base + ".csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in RESULT_COLS})
    return csv_path


# ── main ──────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="*", help="AOI Scanresult root (생략 시 기본 14개)")
    ap.add_argument("--max-dirs", type=int, default=100)
    ap.add_argument("--per-root-max", type=int, default=20)
    ap.add_argument("--latest-lots", type=int, default=3)
    ap.add_argument("--max-depth", type=int, default=5)
    ap.add_argument("--all", action="store_true", help="TB500 외 폴더도 탐색")
    ap.add_argument("--deep", action="store_true", help="레벨·breadth 제한 해제")
    ap.add_argument("--timeout", type=float, default=1200.0)
    ap.add_argument("--tol", type=float, default=5.0)
    ap.add_argument("--out", default="AOI_Surface_UI_검증.xlsx")
    args = ap.parse_args(argv)

    logs = []

    def log(m):
        logs.append(m)
        print(m)

    roots = args.roots or DEFAULT_ROOTS
    deadline = time.time() + args.timeout
    log(f"[START] roots={len(roots)} max_dirs={args.max_dirs} "
        f"per_root_max={args.per_root_max} latest_lots={args.latest_lots} "
        f"all={args.all} deep={args.deep}")

    found = []
    for root in roots:
        if len(found) >= args.max_dirs or time.time() > deadline:
            break
        remain = args.max_dirs - len(found)
        got = discover(root, tb500_only=not args.all, latest_lots=args.latest_lots,
                       max_depth=args.max_depth,
                       per_root_max=min(args.per_root_max, remain),
                       deep=args.deep, deadline=deadline, log=log)
        found.extend(got)
    log(f"[TARGETS] 최종 처리 대상 {len(found)}개")

    rows, schema_rows = [], []
    for folder in found:
        if time.time() > deadline:
            log("[TIMEOUT] 시간 초과 — 남은 폴더 중단")
            break
        try:
            process_folder(folder, args.tol, rows, schema_rows, log)
        except Exception as e:  # noqa: BLE001
            log(f"[ERROR] {folder}: {e}")

    try:
        import openpyxl  # noqa: F401
        path = write_excel(args.out, found, schema_rows, rows, logs)
        print(f"\n[OUT] Excel 저장: {path}")
    except ImportError:
        path = write_csv(os.path.splitext(args.out)[0], rows, logs)
        print(f"\n[OUT] openpyxl 없음 → CSV 저장: {path}")
        print("      (전체 시트가 필요하면 pip install openpyxl 후 재실행)")

    # 콘솔 요약
    matched = sum(1 for r in rows if r["record_index"] != "")
    print(f"[SUMMARY] 폴더 {len(found)} / row {len(rows)} / 매칭성공 {matched} "
          f"/ 실패 {len(rows) - matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
