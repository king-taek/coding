"""KLA↔Camtek 보정 원인 조사 — 한 LOT 의 웨이퍼 전부를 두 장비 쪽에서 모아 zip 하나로 만든다(읽기 전용).

무엇을 확인하려는가: 웨이퍼 한 장(31266529EWF6)에서는 Camtek−KLA 가 die 안쪽 좌표로 약
(+90, −92) µm 였다.  그중 (+38, −100) 은 두 장비가 die 격자를 웨이퍼 중심에 거는 방식
(KLA ``SampleCenterLocation`` ↔ Camtek ``Wafer2Table`` 중심 ÷ DieStep)의 차이로 파일에서
계산되고, 나머지 (+50, ~0) 은 두 장비가 잰 웨이퍼 중심의 차이로 보인다.  이 두 덩어리가
**웨이퍼마다 일정한지** 여러 장으로 확인하려고 LOT 전체를 모은다.  사진 짝은 사람이 고르지
않아도 된다 — 웨이퍼 중심 기준 절대좌표로 짝을 찾는다.

* KLA LOT 폴더 아래 실행 폴더마다 ``.001`` 의 WaferID 를 읽고, Camtek 레시피 폴더에서 같은
  이름의 웨이퍼 폴더를 찾아 둘 다 담는다.
* 사진은 파일명만, 사진 아닌 파일은 ``MAX_FILE_MB`` 이하만(``s_FrameData.dat`` 같은 대용량 제외).

사용법: 더블클릭(아래 경로 고정).  zip 은 바탕화면에 만든다.  원본은 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 2026-10-24 KLA↔Camtek 보정 건 — VPK RDL4 4F-K1(KLA) ↔ AOI-20(Camtek), LOT TB500.INT895@6412.
KLA_LOT = r"H:\2026-10-03\TB500.INT895@6412"
CAMTEK_RECIPE = r"P:\AOI-20\Scanresult\TB500_RDL4 - Multi\Setup1\VPK-RDL4 4F RE"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
MAX_FILE_MB = 2           # 이보다 큰 비사진 파일은 담지 않고 보고에만 적는다(웨이퍼가 많다)
MAX_DEPTH = 2             # 고른 폴더 아래로 내려가는 깊이
MAX_DIRS = 400            # 폴더 하나에서 훑는 하위 폴더 상한(LOT 통째로 골랐을 때 대비)


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


def _add_dir(zf: zipfile.ZipFile, d: Path, arc: str, log: list[str]) -> list[Path]:
    """``d`` 바로 아래 파일 — 사진 아닌 것은 zip 에, 사진은 이름만.  사진 경로를 돌려준다."""
    photos: list[Path] = []
    try:
        files = sorted((p for p in d.iterdir() if p.is_file()), key=lambda p: p.name.lower())
    except OSError as exc:
        log.append(f"  ! 폴더를 못 읽음: {d} ({exc})")
        return photos
    for p in files:
        if p.name.casefold() == "thumbs.db":       # 썸네일 부산물 = 사진으로 친다
            continue
        if p.suffix.lower() in IMAGE_EXT:
            photos.append(p)
            continue
        size_mb = p.stat().st_size / 1e6
        if size_mb > MAX_FILE_MB:
            log.append(f"  - 너무 커서 뺌({size_mb:.0f} MB): {p}")
            continue
        zf.write(p, f"{arc}/{p.name}")
    if photos:
        zf.writestr(f"{arc}/사진목록.txt",
                    "# 사진 파일명(사진 자체는 담지 않음)\n"
                    + "\n".join(p.name for p in photos) + "\n")
    return photos


def _app_rows(photos: list[Path]) -> list[str]:
    """이 PC 의 앱 코드로 계산한 사진별 좌표.  앱을 못 불러오면 빈 목록."""
    try:
        from aoi_verification.app.coords import resolve
    except Exception:
        return []
    rows = []
    for p in photos:
        try:
            c = resolve(p)
        except Exception as exc:                  # 한 장 실패가 수집을 막지 않게
            rows.append(f"{p.parent.name}\t{p.name}\t오류 {exc}")
            continue
        if c is None:
            rows.append(f"{p.parent.name}\t{p.name}\t좌표없음")
        else:
            rows.append(f"{p.parent.name}\t{p.name}\t{c.source}\t{c.col}\t{c.row}"
                        f"\t{c.x:.3f}\t{c.y:.3f}\t{c.native_x}\t{c.native_y}")
    return rows


def _app_geometry(d: Path) -> str:
    """폴더의 die 격자 — 앱이 쓰는 값과, 비교용 중심 유도값·die 맵 값을 함께."""
    try:
        from aoi_verification.app.coords import wafer_geometry as wg
        cg = wg.camtek_geometry(d) or wg.live_geometry(d)
        line = f"{d}\n  camtek: {cg}"
        if cg is not None:
            dia = wg._read_diameter(d)
            cx, cy = wg._wafer_center(d)
            line += (f"\n  중심 유도: center=({cx}, {cy}) dia={dia} → col_origin="
                     f"{wg._col_origin(cx, dia, cg.pitch_x)} row_total="
                     f"{wg._row_total(cy, dia, cg.pitch_y)}"
                     f"\n  die 맵: {wg._die_map_origins(d, cg.pitch_x, cg.pitch_y)}")
        from aoi_verification.app.coords import kla_info
        if kla_info._find_info_file(d) is not None:
            line += f"\n  kla: {wg.kla_geometry(d)}"
        return line
    except Exception as exc:
        return f"{d}\n  ! 기하 계산 실패: {exc}"


def collect(folders: list[Path], out_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = out_dir / f"좌표대조_{folders[0].name}_{stamp}.zip"
    log = [f"수집 {datetime.now():%Y-%m-%d %H:%M}", ""]
    tsv = ["폴더\t사진\tsource\tcol\trow\tx\ty\tnative_x\tnative_y"]
    geo: list[str] = []
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, root in enumerate(folders, 1):
            tag = f"{k}_{root.name}"
            log.append(f"[{k}] {root}")
            if not root.is_dir():
                log.append("  ! 폴더가 없음")
                continue
            for up_i, up in enumerate((root.parent, root.parent.parent), 1):
                _add_dir(zf, up, f"{tag}/_상위{up_i}_{up.name}", log)
            for d in _walk(root):
                rel = d.relative_to(root).as_posix()
                arc = tag if rel == "." else f"{tag}/{rel}"
                photos = _add_dir(zf, d, arc, log)
                if photos:
                    log.append(f"  {rel}: 사진 {len(photos)}장")
                    tsv += _app_rows(photos)
                    geo.append(_app_geometry(d))
            log.append("")
        zf.writestr("수집_보고.txt", "\n".join(log) + "\n")
        zf.writestr("앱좌표.tsv", "\n".join(tsv) + "\n")
        zf.writestr("앱기하.txt", "\n\n".join(geo) + "\n")
    return dst


def _wafer_folders(log: list[str]) -> list[Path]:
    """KLA 실행 폴더와, 같은 WaferID 의 Camtek 웨이퍼 폴더를 번갈아 담은 목록."""
    import re
    out: list[Path] = []
    kla_lot, cam = Path(KLA_LOT), Path(CAMTEK_RECIPE)
    try:
        runs = sorted(p for p in kla_lot.iterdir() if p.is_dir())
    except OSError as exc:
        log.append(f"! KLA LOT 폴더를 못 읽음: {kla_lot} ({exc})")
        return out
    for run in runs:
        wid = None
        for f in run.iterdir():
            if f.is_file() and f.suffix.lower() not in IMAGE_EXT and f.stat().st_size < 5e6:
                m = re.search(r'WaferID\s+"([^"]+)"', f.read_text(errors="ignore"))
                if m:
                    wid = m.group(1)
                    break
        log.append(f"KLA {run.name}: WaferID {wid}")
        if wid is None:
            continue
        out.append(run)
        if (cam / wid).is_dir():
            out.append(cam / wid)
        else:
            log.append(f"  ! Camtek 쪽에 {wid} 폴더가 없음")
    return out


def main() -> int:
    out = Path.home() / "Desktop"
    log: list[str] = []
    folders = _wafer_folders(log)
    print("\n".join(log))
    if not folders:
        print("모을 폴더가 없습니다.")
        return 1
    dst = collect(folders, out)
    print(f"만들었습니다: {dst}\n이 zip 을 대화에 올려 주세요.")
    try:
        os.startfile(str(out))                    # type: ignore[attr-defined]  (Windows)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    code = main()
    input("엔터를 누르면 닫힙니다.")
    sys.exit(code)
