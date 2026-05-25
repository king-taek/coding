"""slot(폴더)명 불일치 시, **파일명 ↔ 폴더명 포함관계(substring)** 로 폴더를 짝짓는
헬퍼.

상황(사용자 설명):
  1. 두 장비 중 **한쪽 폴더는 slot명으로 정확히 명명**되어 있다.
  2. 반대쪽(KLA) 사진의 **파일명 안에 그 slot명이 들어 있다.**
  → 그래서 ref/val 미매칭 폴더를 비교해, **한쪽 폴더명이 반대쪽 폴더의 사진 파일명에
     부분 문자열로 나타나면** 같은 slot 으로 보고 병합한다(대소문자 무시).

병합된 slot명은 **정확한 쪽(=폴더명이 반대쪽 파일명에서 발견된 쪽)** 의 폴더명을
쓴다(원본 폴더명 유지).  특정 패턴(XY 등)에 의존하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

# 너무 짧은 폴더명은 우연한 부분일치(오탐)를 막기 위해 매칭에서 제외.
_MIN_NAME_LEN = 4


def _stems_upper(images) -> list:
    """이미지 목록의 파일명(확장자 제외)을 대문자로."""
    out = []
    for it in images:
        path = getattr(it, "path", it)
        out.append(Path(str(path)).stem.upper())
    return out


def _name_in_files(name, file_stems) -> bool:
    """폴더명 ``name`` 이 파일명들 중 하나에 부분 문자열로 들어있는지(대소문자 무시)."""
    key = str(name).strip().upper()
    if len(key) < _MIN_NAME_LEN:
        return False
    return any(key in stem for stem in file_stems)


def match_by_filename_containment(sr):
    """미매칭 폴더를 '한쪽 폴더명이 반대쪽 파일명에 포함되는지' 로 짝지어 병합.

    ``sr`` (ScanResult) 를 직접 수정하고, 짝지은 ``(ref폴더명, val폴더명)`` 목록을
    반환한다.  병합 slot명 = 정확한(폴더명이 발견된) 쪽의 폴더명.
    """
    from ..models.slot import ImageItem, Slot

    ref_files = {n: _stems_upper(sr.slots[n].ref_images) for n in sr.ref_only}
    val_files = {n: _stems_upper(sr.slots[n].val_images) for n in sr.val_only}

    paired: list[tuple[str, str]] = []
    used_val: set = set()
    for ref_name in list(sr.ref_only):
        chosen = None        # (val_name, slot_name)
        for val_name in list(sr.val_only):
            if val_name in used_val:
                continue
            # val 폴더명이 ref 사진 파일명에 있음 → val 이 정확한 slot, ref 가 KLA.
            if _name_in_files(val_name, ref_files.get(ref_name, [])):
                chosen = (val_name, val_name)
                break
            # ref 폴더명이 val 사진 파일명에 있음 → ref 가 정확한 slot, val 이 KLA.
            if _name_in_files(ref_name, val_files.get(val_name, [])):
                chosen = (val_name, ref_name)
                break
        if chosen is None:
            continue
        val_name, slot_name = chosen
        rslot = sr.slots.get(ref_name)
        vslot = sr.slots.get(val_name)
        if rslot is None or vslot is None:
            continue
        # 양쪽 사진을 정확한 slot명으로 재키잉해 하나의 slot 으로 합친다.
        ref_imgs = [ImageItem(slot=slot_name, path=it.path, side="ref")
                    for it in rslot.ref_images]
        val_imgs = [ImageItem(slot=slot_name, path=it.path, side="val")
                    for it in vslot.val_images]
        sr.slots.pop(ref_name, None)
        sr.slots.pop(val_name, None)
        sr.slots[slot_name] = Slot(name=slot_name,
                                   ref_images=ref_imgs, val_images=val_imgs)
        for n in (ref_name, val_name, slot_name):
            if n in sr.ref_only:
                sr.ref_only.remove(n)
            if n in sr.val_only:
                sr.val_only.remove(n)
        used_val.add(val_name)
        paired.append((ref_name, val_name))

    return paired
