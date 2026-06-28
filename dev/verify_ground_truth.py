#!/usr/bin/env python3
"""실측 ground-truth(사용자 제공 UI 값) 대조 검증 — 단일 md 출력.

NAS 접근 가능한 PC 에서 실행한다.  아래 EXAMPLES 의 각 JPEG 폴더에서 Surface.flt +
recipe 파일을 읽어, **동적 픽셀크기(Scan2DPixelSize 등)** 와 zone/recipe 이름까지 적용해
추출값을 계산하고, 사용자가 준 UI 값(소수점 반올림 고려)과 대조한다.

확인 항목: Area/Width/Length/Contrast (+ Col/Row/Zone/Recipe 이름).
결과는 **AOI_정답대조.md** 한 개로 떨군다.  stdlib 만 사용.

사용:  python dev/verify_ground_truth.py            (임베드된 4건)
       python dev/verify_ground_truth.py --out 결과.md
"""

from __future__ import annotations

import argparse
import math
import re
import struct
from pathlib import Path

# ── 사용자 제공 정답(UI 표시값, 소수점 반올림 적용 가능) ───────────────────
EXAMPLES = [
    dict(path=r"P:\AOI-19\Scanresult\R_TB500_LIVE_PI3\Setup1\KMY-PIDS5\00RXN059XYD5\152756.222197.c.2068531159.1.jpeg",
         area=148.07, width=7.71, length=18.73, contrast=40.3, recipe="PI_Bubble", col=2, row=3, zone="RDL"),
    dict(path=r"P:\AOI-19\Scanresult\R_TB500_LIVE_PI3\Setup1\KMY-PIDS5\00RXN059XYD5\157334.220919.c.1458070665.1.jpeg",
         area=241.65, width=10.76, length=21.32, contrast=43.97, recipe="PI_Bubble", col=2, row=3, zone="RDL"),
    dict(path=r"P:\AOI-17\Scanresult\R_TB500_LIVE_PI4\Setup1\PXU-PIDS3\00RNL037XYH0\209189.327470.c.701357993.1.jpeg",
         area=21.98, width=2.87, length=6.93, contrast=None, recipe="PI_Bubble", col=3, row=0, zone="PI_Opening"),
    dict(path=r"P:\AOI-17\Scanresult\R_TB500_LIVE_PI4\Setup1\PXU-PIDS3\00RNL038XYD7\119305.171914.c.-2056293179.2.jpeg",
         area=19.01, width=2.51, length=6.94, contrast=120.22, recipe="PI", col=1, row=4, zone="PI_Opening"),
]

# ── 확정 Surface.flt 스키마(예시에 박혀 있던 XML 그대로, RecordSize=152) ────
REC = 152
# Vartype: 2→int16 h, 3→int32 i, 4→float32 f, 5→float64 d, 17→uint8 B
F = {
    "Type": (4, "<i"), "x": (8, "<d"), "y": (16, "<d"),
    "ActualX": (24, "<d"), "ActualY": (32, "<d"),
    "frameIdx": (48, "<i"), "xInFrame": (52, "<f"), "yInFrame": (56, "<f"),
    "waferRegion": (60, "<B"), "zone": (61, "<B"), "recipe": (62, "<B"),
    "BoxWidth": (64, "<d"), "BoxHeight": (72, "<d"), "area": (80, "<d"),
    "BlobBreadth": (88, "<d"), "BlobLength": (96, "<d"), "BlobFeretMax": (104, "<d"),
    "BlobFeretMaxAngle": (112, "<d"), "BlobFeretMin": (120, "<d"),
    "Contrast": (136, "<d"),
}
# 픽셀크기 출처 우선순위 (파일, X키, Y키).  Y키 None 이면 단일값(X=Y).
# 면적은 px_x × px_y(이방성)로 환산해야 정확하다(실측: PI3-KMY 는 X≠Y 미세차).
_PX_SOURCES = (("Params_WaferInfo.ini", "RefPixelSizeX", "RefPixelSizeY"),
               ("TrainData/Die.ini", "PixelSize_X", "PixelSize_Y"),
               ("ProductInfo.ini", "Scan2DPixelSize", None),
               ("RecipesInfo.ini", "Scan2DPixelSize", None))
_INI_NAMES = ("ColorImageGrabingInfo.ini", "ColorImageGrabinginfo.ini")
_SEC = re.compile(r"\[[^\]]*\]")


def parse_flt(path):
    try:
        data = path.read_bytes()
    except OSError:
        return []
    recs, pos = [], 0
    while pos + REC <= len(data):
        try:
            recs.append({k: struct.unpack_from(fmt, data, pos + off)[0]
                         for k, (off, fmt) in F.items()})
        except struct.error:
            break
        pos += REC
    return recs


def read_key(path, key):
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"(?im)^\s*" + re.escape(key) + r"\s*=\s*([-\d.eE]+)", txt)
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


def scan_px(folder):
    """2D 스캔 픽셀크기 (px_x, px_y, 출처).  못 찾으면 (None, None, None)."""
    for rel, kx, ky in _PX_SOURCES:
        x = read_key(folder / rel, kx)
        if x is not None and 0.05 <= x <= 5.0:
            y = read_key(folder / rel, ky) if ky else None
            if y is None or not (0.05 <= y <= 5.0):
                y = x
            return x, y, f"{rel}:{kx}"
    return None, None, None


def pairs(folder, name_key, id_key, files=("ProductInfo.ini", "RecipesInfo.ini")):
    """recipe 파일들에서 [section] 안의 (id_key, name_key) 쌍 → {id:name}.

    우선 대표 ini 를 보고, 비면 폴더 안 모든 *.ini 로 확대(실측 '(매핑없음)' 대비)."""
    def scan(names):
        out = {}
        for fn in names:
            try:
                txt = (folder / fn).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for body in _SEC.split(txt):
                nm = re.search(r"(?im)^\s*" + name_key + r"\s*=\s*(.+?)\s*$", body)
                i = re.search(r"(?im)^\s*" + id_key + r"\s*=\s*(\d+)", body)
                if nm and i:
                    out.setdefault(int(i.group(1)), nm.group(1).strip())
        return out

    out = scan(files)
    if not out:
        try:
            out = scan(sorted(p.name for p in folder.glob("*.ini")))
        except OSError:
            pass
    return out


def ini_fault(folder, stem):
    for n in _INI_NAMES:
        p = folder / n
        if not p.exists():
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parts = _SEC.split(txt)  # 섹션명이 [stem.jpeg] 형태가 아니라 일반 split 으론 부족
        # 섹션 단위 재파싱
        for m in re.finditer(r"\[([^\]]+)\]([^\[]*)", txt):
            if Path(m.group(1).strip()).stem.lower() != stem.lower():
                continue
            body = m.group(2)

            def num(k):
                mm = re.search(r"(?im)^\s*" + k + r"\s*=\s*([-\d.eE]+)", body)
                return float(mm.group(1)) if mm else None
            x = num("FaultX") if num("FaultX") is not None else num("X")
            y = num("FaultY") if num("FaultY") is not None else num("Y")
            col = num("Col")
            row = num("Row")
            return x, y, col, row
    return None, None, None, None


def filename_xy(name):
    m = re.match(r"^(\d+(?:\.\d+)?)\.(\d+(?:\.\d+)?)\.", name)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def nearest(recs, x, y):
    best, bd = None, None
    for r in recs:
        d = math.hypot(r["ActualX"] - x, r["ActualY"] - y)
        if bd is None or d < bd:
            bd, best = d, r
    return best, bd


def cmp_cell(extracted, expected):
    # 합격 기준: 소수점 1째자리까지 일치(round(.,1) 동일)하면 정답으로 본다.
    if expected is None:
        ok = extracted is None or abs(extracted) < 0.05
        return f"{'(없음)' if extracted is None else round(extracted,2)}", "(없음)", "✅" if ok else "❌"
    if extracted is None:
        return "-", str(expected), "❌"
    ok = round(extracted, 1) == round(expected, 1)
    return f"{round(extracted,2)}", str(expected), "✅" if ok else "❌"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="AOI_정답대조.md")
    ap.add_argument("--tol", type=float, default=5.0)
    args = ap.parse_args(argv)

    L = ["# AOI Surface.flt 추출값 vs UI 정답 대조  (단일 md)\n",
         "> 동적 픽셀크기(Scan2DPixelSize 등) + zone/recipe 이름을 적용해 계산한 추출값을, "
         "사용자 UI 값과 대조한다. 일치는 **소수점 1째자리**까지 같으면 정답.\n"]

    n_ok = n_tot = 0
    for i, ex in enumerate(EXAMPLES, 1):
        p = Path(ex["path"])
        folder, stem = p.parent, p.stem
        L.append(f"\n## 예시 {i}: `{p.name}`")
        L.append(f"- 폴더: `{folder}`")
        recs = parse_flt(folder / "Surface.flt")
        if not recs:
            L.append("> ❌ Surface.flt 없음/파싱 실패 — 경로/드라이브 확인")
            continue
        px, px_y, px_src = scan_px(folder)
        L.append(f"- Surface.flt 레코드 {len(recs)}개, 픽셀크기 px = "
                 f"**{px if px is not None else '못찾음(0.77 폴백)'}** "
                 f"(출처: {px_src or '-'})"
                 + (f" / px_y={px_y}" if px_y is not None and px_y != px else ""))
        if px is None:
            px = px_y = 0.77
        # 좌표 매칭
        x, y, col, row = ini_fault(folder, stem)
        src = "INI FaultX/Y"
        if x is None:
            x, y = filename_xy(p.name)
            src = "파일명 X.Y"
        rec, dist = (nearest(recs, x, y) if x is not None else (None, None))
        if rec is None or (dist is not None and dist > args.tol):
            L.append(f"> ❌ 좌표 매칭 실패 (src={src}, dist={dist}). 이 결함의 record 못 찾음.")
            continue
        L.append(f"- 매칭 record: dist={dist:.3f}µm (src={src}), zone코드={rec['zone']}, "
                 f"recipe코드={rec['recipe']}")

        zname = pairs(folder, "ZoneName", "ZoneID").get(rec["zone"])
        rname = pairs(folder, "RecipeName", "RecipeNumber").get(rec["recipe"])

        rows = [
            ("Area (㎛²)", rec["area"] * px * px_y, ex["area"]),
            ("Width (㎛)", rec["BlobBreadth"] * px, ex["width"]),
            ("Length (㎛)", rec["BlobFeretMax"] * px, ex["length"]),
            ("Contrast", rec["Contrast"], ex["contrast"]),
        ]
        L.append("\n| 항목 | 추출 | UI(정답) | 일치 |")
        L.append("| --- | --- | --- | --- |")
        for label, extr, expv in rows:
            a, b, ok = cmp_cell(extr, expv)
            n_tot += 1
            n_ok += (ok == "✅")
            L.append(f"| {label} | {a} | {b} | {ok} |")
        # zone/recipe 이름 + col/row(참고)
        L.append(f"| Zone | {zname or '(매핑없음)'} (코드 {rec['zone']}) | {ex['zone']} | "
                 f"{'✅' if zname == ex['zone'] else '❔'} |")
        L.append(f"| Recipe | {rname or '(매핑없음)'} (코드 {rec['recipe']}) | {ex['recipe']} | "
                 f"{'✅' if rname == ex['recipe'] else '❔'} |")
        ui_col = None if col is None else int(col) - 2
        ui_row = None if row is None else 7 - int(row)
        L.append(f"| Col/Row | ColorImage {col}/{row} → 변환 {ui_col}/{ui_row} | "
                 f"{ex['col']}/{ex['row']} | "
                 f"{'✅' if (ui_col == ex['col'] and ui_row == ex['row']) else '❔'} |")
        # 참고: 전체 디코딩 필드
        L.append("\n<details><summary>record 전체 필드</summary>\n")
        L.append("| field | raw | ×px(선형) |\n| --- | --- | --- |")
        for k in ("BoxWidth", "BoxHeight", "BlobBreadth", "BlobLength",
                  "BlobFeretMax", "BlobFeretMin", "BlobFeretMaxAngle", "area",
                  "Contrast", "x", "y", "ActualX", "ActualY"):
            raw = rec[k]
            conv = round(raw * px, 3) if k not in ("Contrast", "BlobFeretMaxAngle",
                                                   "ActualX", "ActualY", "area") else ""
            L.append(f"| {k} | {round(raw,4)} | {conv} |")
        L.append("</details>")

    L.insert(2, f"\n**요약: {n_ok}/{n_tot} 항목 일치**\n")
    Path(args.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[OUT] {args.out}  ({n_ok}/{n_tot} 일치)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
