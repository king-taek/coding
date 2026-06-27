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
import math
import re
import struct
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

# 대표 PI 폴더 — 무옵션 실행 시 자동 포함(contrast 비0 케이스 항상 표집).
PI4_REP = (r"P:\AOI-22\Scanresult\R_TB500_LIVE_PI4 AOI-21 Copy_0614"
           r"\Setup1\PXU-PI4-ENGINEER-TEST\00RMF041XYC7")

# 0.77 출처 추적 — 찾을 목표값.  area 계수 0.5929 = 0.77².
HUNT_TARGETS = {"len_factor_0.77": 0.77, "area_factor_0.5929": 0.5929}
_HUNT_REL = 0.005   # ±0.5%
# 0.77 후보를 뒤질 텍스트 파일 확장자.
_TEXT_EXT = (".ini", ".rcp", ".recipe", ".txt", ".cfg", ".xml", ".info", ".dat")
_FLOAT_TOK = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?")


def _near(v, target, rel=_HUNT_REL):
    return abs(v - target) <= max(abs(target) * rel, 1e-9)


def hunt_pixel_factor(folder, extra_targets=()):
    """폴더 트리(자신+상위 2단계)에서 0.77/0.5929 (및 INI PixelSize 등 extra) 값을
    텍스트·바이너리 파일에서 찾아 후보 목록 반환. 절대 raise 안 함."""
    targets = dict(HUNT_TARGETS)
    for i, t in enumerate(extra_targets):
        targets[f"ini_pixelsize_{i}"] = t
    hits = []
    # 폴더 자신 + 상위 2단계(LOT/Setup) 까지 — recipe/setup 파일이 위에 있을 수 있음.
    scope = [folder]
    p = folder
    for _ in range(2):
        p = p.parent
        scope.append(p)
    seen = set()
    for d in scope:
        try:
            entries = list(d.iterdir())
        except Exception:
            continue
        for f in entries:
            if not f.is_file() or f in seen:
                continue
            seen.add(f)
            ext = f.suffix.lower()
            try:
                if ext in _TEXT_EXT and f.stat().st_size < 5_000_000:
                    txt = f.read_text(encoding="utf-8", errors="replace")
                    for m in _FLOAT_TOK.finditer(txt):
                        v = float(m.group())
                        for name, tgt in targets.items():
                            if _near(v, tgt):
                                ln = txt.count("\n", 0, m.start()) + 1
                                hits.append({"file": f.name, "where": f"line {ln}",
                                             "value": v, "match": name})
                if f.name.lower() in ("surface.flt",) or (
                        ext == ".dat" and f.stat().st_size < 2_000_000):
                    data = f.read_bytes()
                    for fmt, sz in (("<f", 4), ("<d", 8)):
                        for off in range(0, len(data) - sz + 1, sz):
                            try:
                                v = struct.unpack_from(fmt, data, off)[0]
                            except struct.error:
                                continue
                            if v != v or abs(v) > 1e6:  # NaN/huge skip
                                continue
                            for name, tgt in targets.items():
                                if _near(v, tgt):
                                    hits.append({"file": f.name,
                                                 "where": f"off {off} {fmt}",
                                                 "value": round(v, 6), "match": name})
            except Exception:
                continue
    return hits


def parse_ini_meta(path):
    """INI 첫 섹션들의 PixelSizeX/Y, Mag, OpticName 모음(분포용)."""
    out = []
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return out
    parts = _SECTION.split(txt)
    it = iter(parts[1:])
    for _name, body in zip(it, it):
        kv = {m.group(1).upper(): m.group(2).strip() for m in _KV.finditer(body)}
        out.append((kv.get("OPTICNAME", ""), kv.get("MAG", ""),
                    kv.get("PIXELSIZEX", ""), kv.get("PIXELSIZEY", "")))
    return out


def dump_record_fields(rec_bytes):
    """한 152byte record 의 모든 float64 offset(0..144) 디코딩 + 알려진 라벨."""
    known = {off: name for name, (off, fmt) in FIELDS.items() if fmt == "d"}
    out = []
    for off in range(0, RECSIZE - 7, 8):
        try:
            v = struct.unpack_from("<d", rec_bytes, off)[0]
        except struct.error:
            continue
        if v != v or abs(v) > 1e12:
            continue
        out.append({"offset": off, "value": round(v, 6),
                    "known": known.get(off, "")})
    return out


def parse_zone_mapping(folder):
    """폴더/상위에서 Zones.ini·Recipe2-Zones.ini 찾으면 {코드: 이름} 추출."""
    names = ("Zones.ini", "Recipe2-Zones.ini")
    out = {}
    p = folder
    for _ in range(3):
        for n in names:
            f = p / n
            if f.exists():
                try:
                    txt = f.read_text(encoding="utf-8", errors="replace")
                    for sec in _SECTION.findall(txt):
                        pass  # 섹션명만으론 부족 — 내용에서 id/name 매칭 시도
                    for m in re.finditer(
                            r"(?im)^\s*(?:zone)?id\s*=\s*(\d+).*?\n(?:.*\n)?\s*name\s*=\s*(.+)$",
                            txt):
                        out[m.group(1)] = m.group(2).strip()
                except Exception:
                    pass
        p = p.parent
    return out


def framing_hypothesis(size):
    """size%152≠0 일 때 원인 추정: header H 또는 대체 record 크기."""
    if size % RECSIZE == 0:
        return "OK(152 정합)"
    rem = size % RECSIZE
    # header H 가정: (size-H) % 152 == 0 → H == rem.
    return f"끝에 {rem}B 잉여(부분 record/꼬리블록) 또는 헤더 {rem}B 가정"


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
def process_folder(folder, tol, rows, schema_rows, log, ctx=None):
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
        "framing": framing_hypothesis(size),
        "판단": "OK" if (framed and recs) else "CHECK",
    })
    log(f"[FLT] {folder} / size={size}, %152={size % RECSIZE}, "
        f"records={len(recs)}, ini={len(entries)}")

    # ── 확장 분석 수집(ctx) ──────────────────────────────────────────────
    if ctx is not None:
        if ini_path:
            ctx["optic"].extend(parse_ini_meta(ini_path))
        if recs and not ctx.get("field_dump"):
            try:
                data = flt.read_bytes()
                ctx["field_dump"] = dump_record_fields(data[:RECSIZE])
                ctx["field_dump_folder"] = folder.name
            except Exception:
                pass
        # 0.77 출처 추적·zone 매핑 — 파일 스캔이라 앞쪽 일부 폴더만.
        if ctx.get("hunt_budget", 0) > 0:
            ctx["hunt_budget"] -= 1
            ps = []
            if ini_path:
                for (_o, _m, px, py) in parse_ini_meta(ini_path):
                    for s in (px, py):
                        try:
                            ps.append(float(s))
                        except (TypeError, ValueError):
                            pass
            for h in hunt_pixel_factor(folder, tuple(sorted(set(ps))[:4])):
                h["folder"] = folder.name
                ctx["pixel_hits"].append(h)
            ctx["zone_map"].update(parse_zone_mapping(folder))

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


def ui_shortlist(rows, per_group=5):
    """UI 교차확인용 선별 — 두 그룹으로 나눠 **비0/0 둘 다** 포함한다.

      ① contrast≠0 (값검증): UI 의 contrast 가 추출값과 같은지 확인.
      ② contrast=0 (공란검증): UI 도 contrast 를 0/공란으로 보여주는지 확인.
    각 그룹은 (zone,recipe) 가 겹치지 않게 다양화해 N개씩 뽑는다."""
    def pick(pred):
        seen, out = set(), []
        for r in rows:
            if r["record_index"] == "" or r["zone"] == "":
                continue
            try:
                c = float(r["contrast"] or 0)
            except (TypeError, ValueError):
                continue
            if not pred(c):
                continue
            key = (r["zone"], r["recipe"])
            if key in seen:
                continue          # (zone,recipe) 다양화 우선
            seen.add(key)
            out.append(r)
            if len(out) >= per_group:
                break
        return out

    out = []
    for label, items in (("contrast≠0 (값검증)", pick(lambda c: c != 0)),
                         ("contrast=0 (공란검증)", pick(lambda c: c == 0))):
        for r in items:
            out.append({
                "그룹": label, "folder": r["folder"], "image_stem": r["image_stem"],
                "추출_area": r["area_um2"], "추출_width": r["width_um"],
                "추출_length": r["length_um"], "추출_contrast": r["contrast"],
                "추출_zone": r["zone"], "추출_recipe": r["recipe"],
                "UI_area": "", "UI_width": "", "UI_length": "",
                "UI_contrast": "", "UI_zone명": "", "일치?": "",
            })
    return out


def _product_of(folder_path):
    """경로에서 제품 폴더(Scanresult 다음 세그먼트) 추출."""
    parts = [p for p in re.split(r"[\\/]", str(folder_path)) if p]
    for i, p in enumerate(parts):
        if p.lower() == "scanresult" and i + 1 < len(parts):
            return parts[i + 1]
    return parts[-1] if parts else str(folder_path)


# ── 출력 — 단일 마크다운(.md) ─────────────────────────────────────────────
RESULT_COLS = ["folder", "image_stem", "record_index", "recipe", "ini_recipe",
               "recipe_match", "zone", "match_dist_um", "area_um2", "width_um",
               "length_um", "contrast", "note"]
_FULL_LIMIT = 300   # 검증결과_전체 표에 적을 최대 행(초과분은 건수만 안내)


def _md_table(headers, dict_rows, limit=None):
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    shown = dict_rows if limit is None else dict_rows[:limit]
    for d in shown:
        out.append("| " + " | ".join(str(d.get(h, "")) for h in headers) + " |")
    if limit is not None and len(dict_rows) > limit:
        out.append(f"| … | 총 {len(dict_rows)}행 중 상위 {limit}행만 표시 |"
                   + " |" * (len(headers) - 2))
    return "\n".join(out)


def write_markdown(out, found, schema_rows, rows, logs, args, ctx=None):
    ctx = ctx or {}
    matched = [r for r in rows if r["record_index"] != ""]
    failed = [r for r in rows if r["record_index"] == "" and "매칭실패" in r["note"]]
    aux = [r for r in failed if "aux" in r["note"]]
    framed_ok = sum(1 for s in schema_rows if s["framed_ok"])
    dists = [r["match_dist_um"] for r in matched if r["match_dist_um"] != ""]
    rm_y = sum(1 for r in matched if r["recipe_match"] == "Y")
    rm_n = sum(1 for r in matched if r["recipe_match"] == "N")

    L = []
    L.append("# AOI Surface.flt geometry vs UI — 자동탐색 검증 결과\n")
    L.append("> 이 파일은 `flt_check_auto_discover.py` 가 생성한 단일 보고서입니다. "
             "아래 **UI수기확인 shortlist** 의 `UI_*` 칸을 AOI UI 보고 채운 뒤, 이 .md 를 "
             "그대로 복사해 전달하세요.\n")

    L.append("## 1. 요약")
    L.append(_md_table(["항목", "값"], [
        {"항목": "탐색 root 수", "값": len(args.roots) or len(DEFAULT_ROOTS)},
        {"항목": "Surface.flt 폴더 수", "값": len(found)},
        {"항목": "스키마 정합(framed_ok)", "값": f"{framed_ok}/{len(schema_rows)}"},
        {"항목": "전체 결과 row", "값": len(rows)},
        {"항목": "좌표 매칭 성공", "값": len(matched)},
        {"항목": "매칭 실패(합계)", "값": len(failed)},
        {"항목": "  └ aux/revisit 후보(ini>records)", "값": len(aux)},
        {"항목": "  └ 타 die(nearest>tol)", "값": len(failed) - len(aux)},
        {"항목": "매칭거리 min/max µm",
         "값": (f"{min(dists):.1f} / {max(dists):.1f}" if dists else "-")},
        {"항목": "recipe_match Y / N", "값": f"{rm_y} / {rm_n}"},
    ]))
    L.append("\n> 주의: `UI_*` 열은 비어 있습니다(자동 채움 금지). 사람이 AOI UI 를 보고 직접 입력하세요.\n")

    L.append("## 2. zone × recipe 별 contrast 분포")
    L.append("contrast=0 이 zone 때문인지 recipe/제품 때문인지 본다.\n")
    L.append(_md_table(["zone", "recipe", "총개수", "contrast==0", "contrast!=0", "0비율%"],
                       zone_recipe_crosstab(rows)))

    L.append("\n## 3. UI 수기확인 shortlist  ← 여기 UI 칸을 채우세요")
    L.append("각 결함을 AOI UI 에서 열어 UI 값을 적는다. 특히 **추출 contrast=0 인 결함을 "
             "UI 도 0/공란으로 보여주는지** 확인.\n")
    L.append(_md_table(
        ["그룹", "image_stem", "추출_area", "추출_width", "추출_length", "추출_contrast",
         "추출_zone", "추출_recipe", "UI_area", "UI_width", "UI_length", "UI_contrast",
         "UI_zone명", "일치?"], ui_shortlist(rows)))

    # ── 4. 0.77 출처 후보 ────────────────────────────────────────────────
    L.append("\n## 4. 픽셀크기(0.77) 출처 후보  ★")
    L.append("0.77 은 하드코딩 상수가 아니라 원본 어딘가의 값일 것. INI PixelSize(optic별 "
             "0.17/0.46)와 다르므로 별도(검사) 픽셀일 가능성. 폴더 트리의 텍스트·바이너리에서 "
             "`0.77`/`0.5929`/INI PixelSize 와 일치하는 값을 찾은 결과:\n")
    hits = ctx.get("pixel_hits", [])
    if hits:
        agg = defaultdict(lambda: [0, None])
        for h in hits:
            k = (h["file"], h["where"], h["match"], h["value"])
            agg[k][0] += 1
        rows_h = [{"file": f, "where": w, "match": m, "value": v, "건수": c}
                  for (f, w, m, v), (c, _) in
                  sorted(agg.items(), key=lambda kv: -kv[1][0])]
        L.append(_md_table(["file", "where", "match", "value", "건수"], rows_h, limit=60))
        L.append("\n> 위에서 `match=len_factor_0.77` 이 특정 파일/필드에 일관되게(건수 多) 있으면 "
                 "그게 0.77 의 출처다. 앱이 거기서 동적으로 읽도록 후속 작업.")
    else:
        L.append("> (스캔 범위에서 0.77/0.5929 와 일치하는 값을 못 찾음 — `--deep` 또는 상위 "
                 "recipe/setup 폴더까지 확대 필요. 0.77 은 색상 INI 가 아닌 검사 recipe 에 있을 것.)")

    # ── 5. 확장 필드 덤프 ────────────────────────────────────────────────
    L.append(f"\n## 5. Surface.flt 레코드 전체 필드 덤프 (샘플: "
             f"{ctx.get('field_dump_folder', '-')})")
    L.append("알려진 6필드 외 다른 offset 값도 함께 본다(BoxWidth/Height·BlobLength·FeretMin "
             "등 후보 식별 → UI 와 대조).\n")
    L.append(_md_table(["offset", "value", "known"], ctx.get("field_dump", [])))

    # ── 6. PixelSize / Mag / Optic 분포 ──────────────────────────────────
    L.append("\n## 6. INI PixelSize / Mag / Optic 분포")
    optic = ctx.get("optic", [])
    if optic:
        oc = defaultdict(int)
        for (o, m, px, py) in optic:
            oc[(o, m, px, py)] += 1
        rows_o = [{"OpticName": o, "Mag": m, "PixelSizeX": px, "PixelSizeY": py, "건수": c}
                  for (o, m, px, py), c in sorted(oc.items(), key=lambda kv: -kv[1])]
        L.append(_md_table(["OpticName", "Mag", "PixelSizeX", "PixelSizeY", "건수"],
                           rows_o, limit=40))
    else:
        L.append("> (INI 메타 없음)")

    # ── 7. 제품별 contrast≠0 비율 ────────────────────────────────────────
    L.append("\n## 7. 제품별 contrast≠0 비율  (어떤 제품/recipe 가 contrast 측정?)")
    prod = defaultdict(lambda: [0, 0])  # product -> [총, contrast!=0]
    for r in matched:
        product = _product_of(r["folder_path"])
        prod[product][0] += 1
        if float(r["contrast"] or 0) != 0:
            prod[product][1] += 1
    rows_p = [{"제품": p, "매칭수": t, "contrast≠0": nz,
               "≠0비율%": round(100 * nz / t, 1) if t else 0}
              for p, (t, nz) in sorted(prod.items(), key=lambda kv: -kv[1][1])]
    L.append(_md_table(["제품", "매칭수", "contrast≠0", "≠0비율%"], rows_p, limit=60))

    # ── 8. zone 이름 매핑(있으면) ────────────────────────────────────────
    L.append("\n## 8. zone 코드 → 이름 매핑")
    zm = ctx.get("zone_map", {})
    if zm:
        L.append(_md_table(["zone", "name"],
                           [{"zone": k, "name": v} for k, v in sorted(zm.items())]))
    else:
        L.append("> (Zones.ini/Recipe2-Zones.ini 를 못 찾음 — 코드만 사용. 알려진 추정: "
                 "1=PI Opening, 63=Scan Area)")

    # ── 9. 폴더 스키마 정합성 ────────────────────────────────────────────
    L.append("\n## 9. 폴더 스키마 정합성  (framed_ok=False 는 152 비정합)")
    L.append(_md_table(
        ["folder_path", "surface_flt_size", "size_mod_152", "framed_ok",
         "records", "ini_entries", "framing", "판단"], schema_rows, limit=120))

    L.append("\n## 10. 검증결과 (전체 일부)")
    L.append(_md_table(RESULT_COLS, rows, limit=_FULL_LIMIT))

    L.append("\n## 11. 실행 로그")
    L.append("```\n" + "\n".join(logs) + "\n```")

    Path(out).write_text("\n".join(L) + "\n", encoding="utf-8")
    return out


# ── main ──────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="*", help="AOI Scanresult root (생략 시 기본 14개)")
    ap.add_argument("--max-dirs", type=int, default=200)
    ap.add_argument("--per-root-max", type=int, default=None,
                    help="root 당 상한(미지정 시 max_dirs/root수 로 자동 분배)")
    ap.add_argument("--latest-lots", type=int, default=3)
    ap.add_argument("--max-depth", type=int, default=5)
    ap.add_argument("--all", action="store_true", help="TB500 외 폴더도 탐색")
    ap.add_argument("--deep", action="store_true", help="레벨·breadth 제한 해제")
    ap.add_argument("--timeout", type=float, default=1200.0)
    ap.add_argument("--tol", type=float, default=5.0)
    ap.add_argument("--out", default="AOI_검증결과.md", help="단일 마크다운 출력 경로")
    args = ap.parse_args(argv)

    logs = []

    def log(m):
        logs.append(m)
        print(m)

    auto = not args.roots                    # 무옵션(자동) 모드
    roots = args.roots or DEFAULT_ROOTS
    per_root_max = (args.per_root_max if args.per_root_max is not None
                    else max(5, args.max_dirs // max(1, len(roots))))
    deadline = time.time() + args.timeout
    log(f"[START] auto={auto} roots={len(roots)} max_dirs={args.max_dirs} "
        f"per_root_max={per_root_max} latest_lots={args.latest_lots} "
        f"all={args.all} deep={args.deep}")

    found = []
    # 자동 모드: 대표 PI4 폴더 우선 포함(contrast 비0 케이스 항상 표집).
    if auto:
        pi4 = Path(PI4_REP)
        try:
            if (pi4 / "Surface.flt").exists():
                found.append(pi4)
                log(f"[AUTO] 대표 PI4 폴더 포함: {pi4}")
        except Exception:
            pass
    for root in roots:
        if len(found) >= args.max_dirs or time.time() > deadline:
            break
        remain = args.max_dirs - len(found)
        got = discover(root, tb500_only=not args.all, latest_lots=args.latest_lots,
                       max_depth=args.max_depth,
                       per_root_max=min(per_root_max, remain),
                       deep=args.deep, deadline=deadline, log=log)
        found.extend(got)
    log(f"[TARGETS] 최종 처리 대상 {len(found)}개")

    ctx = {"pixel_hits": [], "optic": [], "zone_map": {},
           "field_dump": None, "field_dump_folder": "-", "hunt_budget": 20}
    rows, schema_rows = [], []
    for folder in found:
        if time.time() > deadline:
            log("[TIMEOUT] 시간 초과 — 남은 폴더 중단")
            break
        try:
            process_folder(folder, args.tol, rows, schema_rows, log, ctx)
        except Exception as e:  # noqa: BLE001
            log(f"[ERROR] {folder}: {e}")

    out = args.out if args.out.lower().endswith(".md") else args.out + ".md"
    path = write_markdown(out, found, schema_rows, rows, logs, args, ctx)
    print(f"\n[OUT] 단일 마크다운 저장: {path}")

    # 콘솔 요약
    matched = sum(1 for r in rows if r["record_index"] != "")
    print(f"[SUMMARY] 폴더 {len(found)} / row {len(rows)} / 매칭성공 {matched} "
          f"/ 실패 {len(rows) - matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
