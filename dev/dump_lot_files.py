#!/usr/bin/env python3
"""소수의 LOT/result 폴더 안 **모든 파일을 그대로** 하나의 마크다운으로 덤프.

목적: 0.77(픽셀크기) 의 진짜 출처 찾기. 0.77 은 어디에도 '0.77' 로 안 적혀 있고
**반올림 전 비슷한 값**(예: 0.7698, 0.385×2 …)으로 텍스트/바이너리 어딘가에 있을 수 있다.
그래서 고른 폴더의 모든 파일(이미지 제외) 내용을 통째로 md 에 담아 직접 뒤진다.

stdlib 만 사용. 단일 md 출력.

사용:
    python dev/dump_lot_files.py <폴더1> [<폴더2> ...] [옵션]
      (폴더 = LOT/wafer/result 디렉터리.  **적게** 고를 것 — md 가 커진다.)
    옵션: --out PATH(기본 LOT_파일덤프.md) --max-file-kb N(텍스트 파일당 KB, 기본 48)
          --max-files N(폴더당 파일 수 상한, 기본 300) --recursive/--no-recursive(기본 재귀)

읽는 법: 맨 위 **'0.77 근처 값 요약'** 표에서 어느 파일/위치에 0.74~0.80 값이 있는지 본다.
그게 0.77 의 출처 후보다.
"""

from __future__ import annotations

import argparse
import re
import struct
import time
from pathlib import Path

# ── 무옵션 자동: PI 계열 LOT 자동 탐색 ────────────────────────────────────
# 0.77 은 PI 검사 자재에서 살아있으므로 PI 제품 폴더를 고른다.  검증 예시가 있는
# 대표 PI4 폴더를 우선 포함.
PI4_REP = (r"P:\AOI-22\Scanresult\R_TB500_LIVE_PI4 AOI-21 Copy_0614"
           r"\Setup1\PXU-PI4-ENGINEER-TEST\00RMF041XYC7")
DEFAULT_ROOTS = [
    r"X:\AOI-3\Scanresult", r"V:\AOI-13\Scanresult", r"V:\AOI-14\Scanresult",
    r"V:\AOI-15\Scanresult", r"V:\AOI-16\Scanresult", r"P:\AOI-17\Scanresult",
    r"P:\AOI-18\Scanresult", r"P:\AOI-19\Scanresult", r"P:\AOI-20\Scanresult",
    r"P:\AOI-21\Scanresult", r"P:\AOI-22\Scanresult", r"P:\AOI-23\Scanresult",
    r"Y:\AOI-24\Scanresult", r"Y:\AOI-25\Scanresult",
]
_TB500 = re.compile(r"TB500", re.I)
_PI = re.compile(r"PI", re.I)


def _subdirs(d):
    try:
        return [c for c in d.iterdir() if c.is_dir()]
    except Exception:
        return []


def _find_pi_wafer(root, deadline):
    """root 아래 PI 제품 폴더에서 Surface.flt 보유 폴더 1개를 찾는다(최신 우선)."""
    root = Path(root)
    if not root.exists():
        return None
    for prod in _subdirs(root):
        if not (_TB500.search(prod.name) and _PI.search(prod.name)):
            continue
        stack = [(prod, 0)]
        while stack:
            if time.time() > deadline:
                return None
            d, depth = stack.pop(0)
            if (d / "Surface.flt").exists():
                return d
            if depth >= 5:
                continue
            subs = _subdirs(d)
            try:
                subs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            except Exception:
                pass
            for s in subs[:3]:
                stack.append((s, depth + 1))
    return None


def auto_targets(max_lots, deadline):
    """무옵션: PI wafer 폴더 몇 개 + 그 상위(Setup/제품) 폴더를 (folder, recursive) 로.

    0.77 은 wafer 폴더엔 없으니 상위 폴더(recipe/setup 위치)를 **비재귀**로 함께 덤프한다."""
    wafers = []
    pi4 = Path(PI4_REP)
    try:
        if (pi4 / "Surface.flt").exists():
            wafers.append(pi4)
    except Exception:
        pass
    for root in DEFAULT_ROOTS:
        if len(wafers) >= max_lots or time.time() > deadline:
            break
        w = _find_pi_wafer(root, deadline)
        if w is not None and w not in wafers:
            wafers.append(w)

    targets, seen = [], set()

    def add(folder, recursive):
        if folder not in seen:
            seen.add(folder)
            targets.append((folder, recursive))

    for w in wafers:
        add(w, True)                       # wafer 자체는 재귀(작음)
        p = w
        for _ in range(4):                 # 상위 LOT/Setup/제품은 비재귀
            p = p.parent
            if p == p.parent or p.name.lower() == "scanresult":
                break
            add(p, False)
    return targets, wafers


# 내용 덤프 제외(이미지/대용량 바이너리) — 이름·크기만 표기.
_SKIP_CONTENT_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff",
                     ".avi", ".mp4", ".zip", ".7z", ".gz", ".exe", ".dll"}
# 0.77 후보 밴드(반올림 고려) + 참고 밴드(area 0.59 / pixelsize 0.46).
_NEAR_077 = (0.74, 0.80)
_INTEREST = (0.40, 0.80)
_FLOAT_TOK = re.compile(r"[-+]?\d*\.\d+(?:[eE][-+]?\d+)?")


def is_text(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return False
    try:
        data[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError:
        printable = sum(1 for b in data[:4096] if 9 <= b <= 126 or b in (10, 13))
        return printable / max(1, len(data[:4096])) > 0.85


def scan_floats(data: bytes, lo: float, hi: float, cap: int = 200):
    """바이너리에서 [lo,hi] 범위 float32/float64 값 (offset, fmt, value) 수집(중복 제거)."""
    out, seen = [], set()
    for fmt, sz in (("<f", 4), ("<d", 8)):
        for off in range(0, len(data) - sz + 1):
            try:
                v = struct.unpack_from(fmt, data, off)[0]
            except struct.error:
                break
            if v != v or not (lo <= v <= hi):
                continue
            key = (round(v, 5), fmt)
            if key in seen:
                continue
            seen.add(key)
            out.append((off, fmt, v))
            if len(out) >= cap:
                return out
    return out


def text_interest(txt: str):
    """텍스트에서 [_INTEREST] 범위 숫자 토큰 (line, value) 수집."""
    out = []
    for m in _FLOAT_TOK.finditer(txt):
        try:
            v = float(m.group())
        except ValueError:
            continue
        if _INTEREST[0] <= v <= _INTEREST[1]:
            ln = txt.count("\n", 0, m.start()) + 1
            out.append((ln, v))
    return out


def dump_file(path: Path, rel: str, max_kb: int, near_hits: list):
    L = [f"\n### 파일: `{rel}`"]
    try:
        size = path.stat().st_size
    except OSError:
        return ["\n### 파일: `%s` (stat 실패)" % rel]
    ext = path.suffix.lower()
    L.append(f"- size={size} bytes, ext={ext or '(없음)'}")

    if ext in _SKIP_CONTENT_EXT:
        L.append("- (이미지/대용량 — 내용 생략)")
        return L
    try:
        data = path.read_bytes()
    except OSError as e:
        L.append(f"- 읽기 실패: {e}")
        return L

    if is_text(data):
        try:
            txt = data.decode("utf-8", errors="replace")
        except Exception:
            txt = ""
        for ln, v in text_interest(txt):
            band = "★0.77후보" if _NEAR_077[0] <= v <= _NEAR_077[1] else "관심"
            near_hits.append({"file": rel, "where": f"line {ln}", "value": v, "band": band})
        clipped = txt[: max_kb * 1024]
        L.append("```text")
        L.append(clipped + ("" if len(txt) <= max_kb * 1024 else
                            f"\n…(이하 생략, 총 {len(txt)}자)"))
        L.append("```")
    else:
        # 바이너리 — hex head + 관심 float 스캔.
        head = data[:128].hex()
        L.append("HEX(앞 128B):")
        L.append("```text\n" + " ".join(head[i:i + 2] for i in range(0, len(head), 2)) + "\n```")
        floats = scan_floats(data, *_INTEREST) if size <= 4_000_000 else []
        if size > 4_000_000:
            L.append(f"- (바이너리 {size}B — float 스캔 생략(>4MB))")
        elif floats:
            L.append("관심 float(0.40~0.80) 스캔:")
            L.append("| offset | fmt | value |\n| --- | --- | --- |")
            for off, fmt, v in floats[:80]:
                band = "★0.77후보" if _NEAR_077[0] <= v <= _NEAR_077[1] else ""
                L.append(f"| {off} | {fmt} | {v:.6f} {band} |")
                if _NEAR_077[0] <= v <= _NEAR_077[1]:
                    near_hits.append({"file": rel, "where": f"off {off} {fmt}",
                                      "value": round(v, 6), "band": "★0.77후보"})
        else:
            L.append("- 관심 범위(0.40~0.80) float 없음")
    return L


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*",
                    help="LOT/result 폴더(적게). 생략 시 PI LOT 자동 선택")
    ap.add_argument("--out", default="LOT_파일덤프.md")
    ap.add_argument("--max-file-kb", type=int, default=48)
    ap.add_argument("--max-files", type=int, default=300)
    ap.add_argument("--max-lots", type=int, default=2, help="자동 모드에서 고를 PI LOT 수")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--no-recursive", action="store_true")
    args = ap.parse_args(argv)

    deadline = time.time() + args.timeout
    if args.folders:
        targets = [(Path(f), not args.no_recursive) for f in args.folders]
        picked_note = None
    else:
        # 무옵션: PI wafer + 상위(Setup/제품) 폴더 자동 선택.
        targets, wafers = auto_targets(args.max_lots, deadline)
        picked_note = wafers
        print(f"[AUTO] PI LOT {len(wafers)}개 자동 선택, 덤프 대상 {len(targets)}개 폴더")
        if not targets:
            print("[AUTO] PI LOT 을 못 찾음 — 폴더를 직접 지정하세요(드라이브 접근 확인).")

    near_hits: list = []
    body: list = []
    total_files = 0
    for fp, recursive in targets:
        if time.time() > deadline:
            body.append("\n> [TIMEOUT] 시간 초과 — 남은 폴더 중단")
            break
        scope = "재귀" if recursive else "이 폴더만"
        body.append(f"\n## 폴더: `{fp}`  ({scope})")
        if not fp.exists():
            body.append("> 존재하지 않음")
            continue
        files = sorted(fp.rglob("*") if recursive else fp.glob("*"))
        files = [f for f in files if f.is_file()][: args.max_files]
        body.append(f"> 파일 {len(files)}개 (상한 {args.max_files})")
        for f in files:
            total_files += 1
            try:
                rel = str(f.relative_to(fp))
            except ValueError:
                rel = f.name
            body.extend(dump_file(f, rel, args.max_file_kb, near_hits))

    L = ["# LOT 전체 파일 덤프 (0.77 출처 탐색용)\n",
         "> `dump_lot_files.py` 가 고른 폴더의 모든 파일(이미지 제외)을 그대로 담았다. "
         "0.77 은 정확히 안 적혀 있고 반올림 전 비슷한 값(0.74~0.80)일 수 있으니, 아래 "
         "**0.77 근처 값 요약**에서 그 값이 어느 파일에 있는지 찾는다.\n"]
    if picked_note is not None:
        L.append("## 0. 자동 선택된 PI LOT")
        if picked_note:
            for w in picked_note:
                L.append(f"- `{w}`")
        else:
            L.append("> PI LOT 자동 탐색 실패 — 폴더를 직접 인자로 지정하세요.")
        L.append("> (각 wafer 폴더는 재귀, 상위 Setup/제품 폴더는 이 폴더만 덤프.)\n")
    L.append("## ★ 0.77 근처 값 요약 (0.74~0.80)")
    only077 = [h for h in near_hits if h["band"] == "★0.77후보"]
    if only077:
        L.append("| file | where | value |\n| --- | --- | --- |")
        seen = set()
        for h in only077:
            k = (h["file"], h["where"], round(h["value"], 4))
            if k in seen:
                continue
            seen.add(k)
            L.append(f"| {h['file']} | {h['where']} | {h['value']} |")
    else:
        L.append("> 0.74~0.80 범위 값 없음 — 더 위 폴더(제품/Setup)나 다른 LOT 도 덤프해볼 것.")
    L.append(f"\n(총 파일 {total_files}개 덤프)\n")
    L.extend(body)

    Path(args.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[OUT] {args.out}  (파일 {total_files}개, 0.77후보 {len(only077)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
