"""T254 WVU 의 Wafer map 원재료를 zip 으로 모은다(읽기 전용, 더블클릭, 이 파일 하나로 돈다).

2026-10-25 현택님 요청: Camtek(6323 R4 · 6324 R5) 과 Conder Scan(REVERSE ETCH · BS CUP ·
BS CUP_재스캔) 에서 앱 맵이 제대로 나오는지 확인한다.

* 사진 아닌 파일 전부(INI · Recipe.ini · ``<WaferID>.txt`` 맵 · s_DieLocation.dat …). 사진은
  이름만(폴더마다 ``사진목록.txt``).  zip · 30 MB 초과 파일은 뺀다(현택님 지시).
* 앱이 안 읽는 Camtek 정렬·프레임 데이터(:data:`SKIP_NAMES`)는 빼고, ``s_DieLocation.dat`` 은
  폴더(root)마다 :data:`MAP_LIMIT` 장만 담는다.
* 표준 파이썬만 쓴다 — 앱 코드·다른 dev 스크립트를 하나도 불러오지 않는다(현택님 지시).
  앱 맵은 저장소에서 원재료로 다시 계산한다.
"""

from __future__ import annotations

import os
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

_CONDER = (r"\\k5cifsn2\k5tsvdata$\1. Conder Scan\531. NVIDIA INT\145. AST254-AG6S2-6CAA"
           r"\22. E1X000.C03 DCC20 (WVU)")
FOLDERS = [
    r"\\10.142.80.91\nas3-1\AOI-16\Scanresult\2D@R4-AS-T254-A01_0857122AY-0B\6323\WVU",
    r"\\10.142.80.91\nas3-1\AOI-16\Scanresult\2D@R5-AS-T254-A01_0857122AY-0B\6324\WVU",
    _CONDER + r"\1. REVERSE ETCH",
    _CONDER + r"\2. BS CUP",
    _CONDER + r"\2. BS CUP_재스캔",
]

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
MAX_FILE_MB = 30
MAX_DEPTH = 3
MAX_DIRS = 400
MAP_LIMIT = 6
SKIP_NAMES = {n.casefold() for n in (
    "DieAlignment.dat", "s_FrameData.dat", "s_DieAlignPos.dat", "Zones.dat",
    "WaferInfo.dat", "WaferInfo.org", "DieAlignMiss.flt", "s_Plane.dat",
    "s_Coplanarity.dat", "ChuckMappingForScanCamera_dX.grd",
    "ChuckMappingForScanCamera_dY.grd")}


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
            log.append("")
        for i in zf.infolist():
            size[Path(i.filename).suffix.lower() or "(없음)"] += i.file_size
        zf.writestr("수집_보고.txt", "\n".join(log) + "\n")
        zf.writestr("용량.txt", "\n".join(f"{e}\t{b / 1e6:.1f} MB"
                                          for e, b in size.most_common()) + "\n")


def main() -> int:
    out = Path.home() / "Desktop" / "좌표대조_디바이스별"
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"맵확인_T254_WVU_{datetime.now():%Y%m%d_%H%M%S}.zip"
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
