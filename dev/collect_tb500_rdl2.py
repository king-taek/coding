"""TB500 RDL2(MLS) 같은 웨이퍼를 찍은 4곳의 원재료를 zip 으로 모은다(읽기 전용, 더블클릭, 이 파일 하나로 돈다).

2026-10-26 현택님 요청: Camtek 4F-AOI-4·5 에서만 '⚠ 장비 번호표가 없어 계산한 col / row'
가 뜬 이유를 찾고, 같은 wafer/layer 를 찍은 Camtek 2대 · KLA · Conder 를 서로 맞춰 본다.

* 사진 아닌 파일 전부(INI · ProductInfo.ini · ``.001`` · ``<WaferID>.txt`` 맵 · s_DieLocation.dat …).
  사진은 이름만(폴더마다 ``사진목록.txt``).  zip · 30 MB 초과 파일은 뺀다(현택님 지시).
* Camtek 폴더의 상위 2단계(Setup1 · 레시피 폴더)의 파일도 담는다 — 번호표(ProductInfo.ini)가
  웨이퍼 폴더가 아니라 위에 있을 수 있어서다.
* 웨이퍼 폴더마다 번호표 상태를 ``번호표_점검.txt`` 에 적고 화면에도 보여 준다.
* 표준 파이썬만 쓴다 — 앱 코드·다른 dev 스크립트를 하나도 불러오지 않는다(현택님 지시).
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

FOLDERS = [
    r"I:\4F-AOI-05\Scanresult\Test_TB500_RDL2 - Multi\Setup1\MLS-RDL2 TEST AOI-5",
    r"I:\4F-AOI-04\Scanresult\Test_TB500_RDL2 - Multi\Setup1\MLS-RDL2 TEST AOI-4",
    r"W:\2026-10-21\TB500.INT888@6324",
    r"\\k5cifsn2\k5tsvdata$\1. Conder Scan\531. NVIDIA INT\129. 4DT-TB500-H-M1"
    r"\842. TB500.INT888 (MLS)\4. RDL2",
]

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
MAX_FILE_MB = 30
MAX_DEPTH = 4
MAX_DIRS = 400
MAP_LIMIT = 30
SKIP_NAMES = {n.casefold() for n in (
    "DieAlignment.dat", "s_FrameData.dat", "s_DieAlignPos.dat", "Zones.dat",
    "WaferInfo.dat", "WaferInfo.org", "DieAlignMiss.flt", "s_Plane.dat",
    "s_Coplanarity.dat", "ChuckMappingForScanCamera_dX.grd",
    "ChuckMappingForScanCamera_dY.grd")}


def _ini(path: Path) -> str:
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except OSError:
            return ""
    return ""


def _key(txt: str, key: str) -> str:
    """``key=값`` 의 값(첫 줄).  없으면 '없음'."""
    m = re.search(r"(?im)^\s*" + re.escape(key) + r"\s*=\s*([^;\r\n]*)", txt)
    return m.group(1).strip() if m else "없음"


def _number_table(d: Path) -> str:
    """웨이퍼 폴더 하나의 번호표(ProductInfo.ini [ExportData]) 상태 한 줄."""
    pi = d / "ProductInfo.ini"
    parts = []
    if not pi.is_file():
        up = [u.name for u in (d.parent, d.parent.parent) if (u / "ProductInfo.ini").is_file()]
        parts.append("ProductInfo.ini 없음" + (f"(상위 {up} 에는 있음)" if up else ""))
    else:
        txt = _ini(pi)
        sec = re.search(r"(?ims)^\s*\[ExportData\]\s*$(.*?)(?=^\s*\[|\Z)", txt)
        if sec is None:
            parts.append("[ExportData] 없음")
        else:
            parts.append("[ExportData] " + " ".join(
                f"{k}={_key(sec.group(1), k)}" for k in ("cMin", "cMax", "rMin", "rMax")))
        parts.append(f"XDieIndex={_key(txt, 'XDieIndex')} YDieIndex={_key(txt, 'YDieIndex')}")
    pw = _ini(d / "Params_WaferInfo.ini")
    parts.append(f"DieStep={_key(pw, 'DieStep_X')},{_key(pw, 'DieStep_Y')}")
    parts.append(f"Center={_key(pw, 'Center_X')},{_key(pw, 'Center_Y')}")
    parts.append("Wafer2Table.ini " + ("있음" if (d / "Wafer2Table.ini").is_file() else "없음"))
    rec = sorted(p.name for p in d.glob("Recipe*-ProductInfo.ini"))
    if rec:
        parts.append(f"레시피별 ProductInfo {len(rec)}개")
    return " | ".join(parts)


def _walk(root: Path):
    """``root`` 와 그 아래 ``MAX_DEPTH`` 단계까지의 폴더(이름순)."""
    todo, seen = [(root, 0)], 0
    while todo and seen < MAX_DIRS:
        d, depth = todo.pop(0)
        seen += 1
        yield d
        if depth >= MAX_DEPTH:
            continue
        try:
            subs = sorted((p for p in d.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
        except OSError:
            continue
        todo.extend((s, depth + 1) for s in subs)


def _add_dir(zf, d: Path, arc: str, log: list[str], maps: Counter) -> list[Path]:
    """``d`` 바로 아래 파일 — 사진 아닌 것은 zip 에, 사진은 이름만.  사진 경로를 돌려준다."""
    photos: list[Path] = []
    try:
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.name.lower())
    except OSError as exc:
        log.append(f"  ! 폴더를 못 읽음: {d} ({exc})")
        return photos
    tag = arc.split("/")[0]
    for p in files:
        n = p.name.casefold()
        if n == "thumbs.db" or n in SKIP_NAMES:
            continue
        if p.suffix.lower() == ".zip":
            log.append(f"  - zip 이라 뺌: {p}")
            continue
        if p.suffix.lower() in IMAGE_EXT:
            photos.append(p)
            continue
        if n == "s_dielocation.dat":
            if maps[tag] >= MAP_LIMIT:
                log.append(f"  - die 맵 {MAP_LIMIT}장 초과라 뺌: {p}")
                continue
            maps[tag] += 1
        size_mb = p.stat().st_size / 1e6
        if size_mb > MAX_FILE_MB:
            log.append(f"  - 너무 커서 뺌({size_mb:.0f} MB): {p}")
            continue
        zf.write(p, f"{arc}/{p.name}")
    if photos:
        zf.writestr(f"{arc}/사진목록.txt", "# 사진 파일명(사진 자체는 담지 않음)\n"
                    + "\n".join(p.name for p in photos) + "\n")
    return photos


def collect(folders: list[Path], dst: Path) -> None:
    log = [f"수집 {datetime.now():%Y-%m-%d %H:%M}", ""]
    table = ["# 웨이퍼 폴더마다 장비 번호표(ProductInfo.ini [ExportData]) 상태", ""]
    maps: Counter = Counter()
    size: Counter = Counter()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, root in enumerate(folders, 1):
            tag = f"{k}_{root.name}"
            log.append(f"[{k}] {root}")
            print(f"[{k}/{len(folders)}] {root}")
            if not root.is_dir():
                log.append("  ! 폴더가 없음")
                print("   ! 폴더가 없습니다")
                continue
            for up_i, up in enumerate((root.parent, root.parent.parent), 1):
                _add_dir(zf, up, f"{tag}/_상위{up_i}_{up.name}", log, maps)
            for d in _walk(root):
                rel = d.relative_to(root).as_posix()
                arc = tag if rel == "." else f"{tag}/{rel}"
                photos = _add_dir(zf, d, arc, log, maps)
                if photos:
                    log.append(f"  {rel}: 사진 {len(photos)}장")
                if (d / "ColorImageGrabingInfo.ini").is_file():
                    line = f"{tag}/{rel}: {_number_table(d)}"
                    table.append(line)
                    print("   " + line)
            log.append("")
        for i in zf.infolist():
            size[Path(i.filename).suffix.lower() or "(없음)"] += i.file_size
        zf.writestr("번호표_점검.txt", "\n".join(table) + "\n")
        zf.writestr("수집_보고.txt", "\n".join(log) + "\n")
        zf.writestr("용량.txt", "\n".join(f"{e}\t{b / 1e6:.1f} MB"
                                          for e, b in size.most_common()) + "\n")


def main() -> int:
    out = Path.home() / "Desktop" / "좌표대조_디바이스별"
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"TB500_RDL2_4곳_{datetime.now():%Y%m%d_%H%M%S}.zip"
    collect([Path(f) for f in FOLDERS], dst)
    print(f"\n만들었습니다: {dst}  {dst.stat().st_size / 1e6:.1f} MB\nzip 을 대화에 올려 주세요.")
    try:
        os.startfile(str(out))                    # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as exc:                      # 더블클릭 창이 바로 닫히지 않게
        print(f"\n오류: {exc}")
        code = 1
    input("엔터를 누르면 닫힙니다.")
    sys.exit(code)
