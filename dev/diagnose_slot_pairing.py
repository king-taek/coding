"""KLA 슬롯이 Camtek 웨이퍼와 짝지어지지 않는 이유 진단(읽기 전용).

2026-10-25 현택님: LSU LOT(TB500.INT846@6412) 검증 결과에서 KLA 슬롯 1·4·9·12·15 가
'기준 전용' 으로 남았는데 Camtek 에도 그 웨이퍼가 있다 — 왜 못 찾았나?

앱은 KLA 폴더의 정보파일(.001 등) 헤더 ``WaferID "XXXX";`` 를 읽어 Camtek 폴더명(WaferID)
과 짝짓는다(``kla_info.read_wafer_id`` → ``slot.merge_unmatched_by_wafer_id``).  이 도구는
KLA 폴더마다 무엇이 들었고 앱이 WaferID 를 읽었는지, Camtek 폴더마다 카세트 슬롯 번호
(``WaferInfo.ini`` ``ActiveSlot``)가 무엇인지 적고, **앱과 같은 코드로** 짝짓기를 돌려
어디서 끊겼는지 보여 준다.

사용법: 더블클릭(아래 경로 고정).  결과는 화면과 바탕화면 ``슬롯짝_진단.txt`` 에 남는다.
원본은 아무것도 바꾸지 않는다.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (이름, KLA LOT 폴더, Camtek 레시피 폴더)
LOT_PAIRS = [
    ("LSU", r"H:\2026-09-24\TB500.INT846@6412",
     r"P:\AOI-23\Scanresult\TB500_RDL4 - Multi - Swelling\Setup1\LSU-RDL4 RE"),
]

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".gif"}
FULL_READ_MB = 50          # WaferID 를 파일 전체에서 찾아볼 크기 상한(헤더에 없을 때)
_WID = re.compile(rb'WaferID\s+"([^"]*)"')
_SLOT = re.compile(rb"(?m)^\s*Slot\s+(\d+)\s*;")


def _slot_from_name(name: str) -> str:
    """KLA 폴더명 ``2026-09-24-15-51_1`` 끝의 숫자(카세트 슬롯 번호)."""
    m = re.search(r"_(\d+)$", name)
    return m.group(1) if m else "?"


def _kla_folder(d: Path, out: list[str]) -> tuple[str | None, str]:
    """KLA 실행 폴더 하나 — 앱이 읽은 WaferID 와 사진 파일명 앞부분(추정 WaferID)."""
    from aoi_verification.app.coords import kla_info
    try:
        files = sorted(p for p in d.iterdir() if p.is_file())
    except OSError as exc:
        out.append(f"    ! 폴더를 못 읽음: {exc}")
        return None, ""
    photos = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    others = [p for p in files if p.suffix.lower() not in IMAGE_EXT]
    kla_info.read_header_ids.cache_clear()
    wid, slot = kla_info.read_header_ids(d)
    prefix = photos[0].name.split("_")[0] if photos else ""
    out.append(f"    사진 {len(photos)}장 (첫 사진: {photos[0].name if photos else '-'})")
    out.append(f"    앱이 읽은 WaferID: {wid}   Slot: {slot}")
    if not others:
        out.append("    ! 사진 말고 다른 파일이 없음 — 정보파일(.001)이 없다")
    for p in others:
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        line = f"    - {p.name}  ({size:,} B)"
        try:
            with p.open("rb") as fh:
                head = fh.read(kla_info._HEAD_BYTES)
            m = _WID.search(head)
            sm = _SLOT.search(head)
            if m:
                line += f"  헤더 WaferID \"{m.group(1).decode(errors='replace')}\""
            else:
                line += "  헤더에 WaferID 없음"
                if 0 <= size <= FULL_READ_MB * 1e6:
                    full = p.read_bytes()
                    fm = _WID.search(full)
                    line += (f" (파일 {fm.start():,} 바이트 위치에 있음: "
                             f"\"{fm.group(1).decode(errors='replace')}\")" if fm
                             else " (파일 전체에도 없음)")
            if sm:
                line += f"  Slot {sm.group(1).decode()}"
            first = head[:120].decode("utf-8", errors="replace").splitlines()[:2]
            line += f"\n        첫 줄: {first}"
        except OSError as exc:
            line += f"  ! 못 읽음: {exc}"
        out.append(line)
    return wid, prefix


def _camtek_folders(cam: Path, out: list[str]) -> dict[str, tuple[str | None, int]]:
    """Camtek 웨이퍼 폴더마다 (ActiveSlot, 앱이 보는 사진 수)."""
    from aoi_verification.app.models import lot_info, slot as slotmod
    res: dict[str, tuple[str | None, int]] = {}
    try:
        dirs = sorted(p for p in cam.iterdir() if p.is_dir())
    except OSError as exc:
        out.append(f"  ! Camtek 폴더를 못 읽음: {exc}")
        return res
    for d in dirs:
        res[d.name] = (lot_info.read_active_slot(d), len(slotmod._list_images(d)))
    return res


def diagnose(name: str, kla_lot: Path, cam: Path) -> list[str]:
    from aoi_verification.app.models import slot as slotmod
    from aoi_verification.app.coords import kla_info
    out = [f"===== {name} =====", f"KLA   : {kla_lot}", f"Camtek: {cam}", ""]
    if not kla_lot.is_dir() or not cam.is_dir():
        out.append(f"! 폴더가 없음 — KLA {kla_lot.is_dir()} / Camtek {cam.is_dir()}")
        return out

    cams = _camtek_folders(cam, out)
    by_slot: dict[str, list[str]] = {}
    for cname, (s, _n) in cams.items():
        by_slot.setdefault(s or "?", []).append(cname)

    out.append("[1] KLA 폴더별")
    runs = sorted((p for p in kla_lot.iterdir() if p.is_dir()), key=lambda p: p.name)
    lonely = []
    for d in runs:
        sno = _slot_from_name(d.name)
        print(f"  KLA {d.name} …", flush=True)
        out.append(f"  {d.name}  (폴더명 슬롯 {sno})")
        wid, prefix = _kla_folder(d, out)
        if wid and wid in {c.upper() for c in cams}:
            out.append(f"    → Camtek {wid} 와 짝 OK")
            continue
        lonely.append(d.name)
        out.append(f"    → ✗ 짝 없음. 사진 이름 앞부분: {prefix or '-'}"
                   f" / Camtek 에 그 이름 폴더: {'있음' if prefix and prefix in cams else '없음'}"
                   f" / Camtek ActiveSlot={sno} 인 폴더: {by_slot.get(sno, ['없음'])}")
    out.append("")

    out.append("[2] Camtek 폴더별 (ActiveSlot · 사진 수)")
    for cname, (s, n) in cams.items():
        out.append(f"  {cname}  slot {s}  사진 {n}장")
    out.append("")

    out.append("[3] 앱과 같은 코드로 짝짓기(scan → WaferID 병합)")
    print("  앱 짝짓기 재현 중 …", flush=True)
    kla_info.read_header_ids.cache_clear()
    sr = slotmod.scan(kla_lot, cam)
    info_ref = {n: w for n in sr.ref_only
                if (w := kla_info.read_wafer_id(sr.slots[n].ref_dir))}
    # 앱처럼 정보파일이 빈 폴더는 파일 이름 + 슬롯 번호로 한 번 더 본다(`_kla_resolve_impl`).
    ids, broken = slotmod.wafer_ids_from_broken_info(
        {n: sr.slots[n].ref_dir for n in sr.ref_only},
        {n: sr.slots[n].val_dir for n in sr.val_only}, info_ref)
    info_ref.update(ids)
    for n, w, ok in broken:
        out.append(f"  정보파일 비어 있음: {n} (이름의 WaferID {w}) → 이름+슬롯번호로 짝 {'지음' if ok else '못 지음'}")
    slotmod.merge_unmatched_by_wafer_id(sr, info_ref, {})
    out.append(f"  짝지어진 슬롯 {sum(1 for s in sr.slots.values() if s.ref_dir and s.val_dir)}개")
    out.append(f"  기준(KLA) 전용: {sorted(sr.ref_only)}")
    out.append(f"  검증(Camtek) 전용: {sorted(sr.val_only)}")
    out.append(f"  (위 [1] 의 짝 없음: {lonely})")
    out.append("")
    return out


def main() -> int:
    lines = [f"슬롯 짝 진단 {datetime.now():%Y-%m-%d %H:%M}", ""]
    for name, kla, cam in LOT_PAIRS:
        print(f"{name} 진단 중…", flush=True)
        try:
            lines += diagnose(name, Path(kla), Path(cam))
        except Exception as exc:          # 진단 도구 — 중간에 죽지 말고 적어서 돌려준다
            import traceback
            lines += [f"! {name} 진단 중 오류: {exc}", traceback.format_exc()]
    text = "\n".join(lines)
    print(text)
    dest = Path.home() / "Desktop" / "슬롯짝_진단.txt"
    try:
        dest.write_text(text, encoding="utf-8")
        print(f"\n저장: {dest}")
    except OSError as exc:
        print(f"\n저장 실패({exc}) — 위 화면 내용을 복사해 주세요.")
    return 0


if __name__ == "__main__":
    try:
        main()
    finally:
        if os.name == "nt":
            input("\n엔터를 누르면 닫힙니다…")
