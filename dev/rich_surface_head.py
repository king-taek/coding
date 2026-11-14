"""비교 웨이퍼 20234838EWG1 의 s_RichSurface.dat 앞부분을 바탕화면 zip 4개(각 28 MB 이하)로 담는다.

Threshold 미리보기 분석용(일회성).  표준 파이썬만 쓴다 — VS Code 에서 그대로 실행하면 된다.
결과: 바탕화면 ``RichSurface_20234838EWG1_1.zip`` · ``_2.zip`` · ``_3.zip`` · ``_4.zip``.
파일 앞에서부터 27 MB 씩 잘라 하나씩 담는다(1번 zip 에 옆 .md 설명 파일과 원래 크기 기록도 넣는다).
"""
import os
import zipfile
from pathlib import Path

LOT = Path(r"I:\4F-AOI-04\Scanresult\R_TB500_LIVE_PI4 - Enhanced\Setup1\YDD-No Image")
WAFER = "20234838EWG1"
PARTS = 4                        # 앞 108 MB
PART_BYTES = 27_000_000          # 압축이 안 돼도 zip 이 28 MB 를 넘지 않게(30 MB 는 안 올라감)
DESK = Path(os.path.expanduser("~")) / "Desktop"


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
    DESK.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as f:
        for k in range(1, PARTS + 1):
            start = f.tell()
            data = f.read(PART_BYTES)
            if not data:
                break
            out = DESK / f"RichSurface_{WAFER}_{k}.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr(f"s_RichSurface_part{k}.dat", data)
                note = f"{src}\n원래 크기 {size}\n이 조각: {start} ~ {start + len(data)} 바이트\n"
                z.writestr(f"조각{k}.txt", note)
                md = src.with_name(src.name + ".md")
                if k == 1 and md.is_file():
                    z.write(md, md.name)
            print(f"  {out.name}  ({out.stat().st_size / 1e6:.1f} MB)")
    print("완료 — 바탕화면의 zip 들을 올려 주세요.")


if __name__ == "__main__":
    main()
