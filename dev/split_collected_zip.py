"""이미 만든 큰 수집 zip(``collect_devices.py`` 첫 버전, 500 MB)을 디바이스별 작은 zip 으로 나눈다.

네트워크 폴더를 다시 훑지 않고, 바탕화면의 ``좌표대조_1. REVERSE ETCH_*.zip`` 에서 앱이
읽지 않는 대용량 파일을 빼고 die 맵은 폴더마다 3장만 옮긴다(규칙은 ``collect_devices``
와 같다).  원본 zip 은 건드리지 않는다.  더블클릭으로 실행한다.
"""

from __future__ import annotations

import os
import sys
import zipfile
from collections import Counter
from pathlib import Path

DESKTOP = Path.home() / "Desktop"
SOURCE_GLOB = "좌표대조_1. REVERSE ETCH_*.zip"

# 첫 버전 zip 의 최상위 폴더 번호(1~18) → 디바이스.  collect_devices.DEVICES 순서와 같다.
GROUPS = [
    ("1_TB500_SGG", (1, 2)), ("2_TB500_KUH", (3, 4)), ("3_T254_RDL1", (5, 6)),
    ("4_T254_FSUBM", (7, 8)), ("5_TI_CCG", (9, 10)), ("6_TI_CCB", (11, 12)),
    ("7_QORVO_FVI", (13, 14, 15, 16)), ("8_QORVO_UBM", (17, 18)),
]
COMMON = ("수집_보고.txt", "앱좌표.tsv", "앱기하.txt")   # 모든 zip 에 함께 넣는다(작다)
SKIP_NAMES = {n.casefold() for n in (
    "DieAlignment.dat", "s_FrameData.dat", "s_DieAlignPos.dat", "Zones.dat",
    "WaferInfo.dat", "WaferInfo.org", "DieAlignMiss.flt", "s_Plane.dat",
    "s_Coplanarity.dat", "ChuckMappingForScanCamera_dX.grd",
    "ChuckMappingForScanCamera_dY.grd")}
MAP_LIMIT = 3


def split(src: Path, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made = []
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        for label, nums in GROUPS:
            dst = out / f"{label}.zip"
            maps: Counter = Counter()
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
                for i in infos:
                    top = i.filename.split("/")[0]
                    name = i.filename.rsplit("/", 1)[-1].casefold()
                    if i.filename in COMMON:
                        zout.writestr(i.filename, zin.read(i))
                        continue
                    num = top.split("_", 1)[0]
                    if not (num.isdigit() and int(num) in nums) or name in SKIP_NAMES:
                        continue
                    if name == "s_dielocation.dat":
                        if maps[top] >= MAP_LIMIT:
                            continue
                        maps[top] += 1
                    zout.writestr(i.filename, zin.read(i))
            made.append(dst)
    return made


def main() -> int:
    found = sorted(DESKTOP.glob(SOURCE_GLOB), key=lambda p: p.stat().st_mtime)
    if not found:
        print(f"바탕화면에서 {SOURCE_GLOB} 를 찾지 못했습니다.")
        return 1
    src = found[-1]
    print(f"나누는 중: {src.name}")
    out = DESKTOP / "좌표대조_디바이스별"
    for dst in split(src, out):
        print(f"   {dst.name}  {dst.stat().st_size / 1e6:.1f} MB")
    print(f"\n만들었습니다: {out}\nzip 을 하나씩 대화에 올려 주세요.")
    try:
        os.startfile(str(out))                    # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    code = main()
    input("엔터를 누르면 닫힙니다.")
    sys.exit(code)
