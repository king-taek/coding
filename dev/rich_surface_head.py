"""비교 웨이퍼 20234838EWG1 의 s_RichSurface.dat 앞부분(100 MB)을 바탕화면 zip 으로 담는다.

Threshold 미리보기 분석용(일회성).  표준 파이썬만 쓴다 — VS Code 에서 그대로 실행하면 된다.
결과: 바탕화면 ``RichSurface_20234838EWG1.zip`` (앞 100 MB + 옆 .md 설명 파일 + 원래 크기 기록).
"""
import os
import zipfile
from pathlib import Path

LOT = Path(r"I:\4F-AOI-04\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-No Image")
WAFER = "20234838EWG1"
HEAD = 100 * 1024 * 1024
OUT = Path(os.path.expanduser("~")) / "Desktop" / f"RichSurface_{WAFER}.zip"


def find_file():
    for d in [LOT / WAFER] + sorted(LOT.iterdir()):
        f = d / "s_RichSurface.dat"
        if WAFER in d.name.upper() and f.is_file():
            return f
    raise SystemExit(f"못 찾음: {LOT}\\*{WAFER}*\\s_RichSurface.dat")


def main():
    src = find_file()
    size = src.stat().st_size
    print(f"찾음: {src}  ({size / 1e6:.1f} MB)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        with src.open("rb") as f, z.open("s_RichSurface_head.dat", "w") as w:
            left = HEAD
            while left > 0:
                b = f.read(min(left, 8 * 1024 * 1024))
                if not b:
                    break
                w.write(b)
                left -= len(b)
                print(f"  {(HEAD - left) / 1e6:.0f} MB", end="\r")
        md = src.with_name(src.name + ".md")
        if md.is_file():
            z.write(md, md.name)
        z.writestr("원래크기.txt", f"{src}\n{size}\n앞 {min(HEAD, size)} 바이트만 담음\n")
    print(f"\n완료: {OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
