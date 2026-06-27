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
from pathlib import Path

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
    ap.add_argument("folders", nargs="+", help="LOT/result 폴더(적게)")
    ap.add_argument("--out", default="LOT_파일덤프.md")
    ap.add_argument("--max-file-kb", type=int, default=48)
    ap.add_argument("--max-files", type=int, default=300)
    ap.add_argument("--no-recursive", action="store_true")
    args = ap.parse_args(argv)

    near_hits: list = []
    body: list = []
    total_files = 0
    for folder in args.folders:
        fp = Path(folder)
        body.append(f"\n## 폴더: `{fp}`")
        if not fp.exists():
            body.append("> 존재하지 않음")
            continue
        files = sorted(fp.rglob("*") if not args.no_recursive else fp.glob("*"))
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
