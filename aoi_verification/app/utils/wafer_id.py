"""KLA 사진 **파일명**에서 WaferID(=slot명)를 뽑아, slot(폴더)명 불일치 폴더를
매칭하는 헬퍼.

KLA 사진은 파일명 앞부분에 WaferID 가 들어 있다.
  예: ``W6459080XYH2_3_0_23_1`` → slot명 ``W6459080XYH``
      (``...XY<글자>`` 까지가 slot명, 뒤따르는 인덱스 숫자/토큰은 버린다)

slot(폴더)명이 ref/val 간 일치하지 않을 때, 파일명에서 뽑은 WaferID 를 매칭 키로
써서 같은 wafer 끼리 ref↔val 을 자동으로 짝짓는다(OCR 불필요 — 훨씬 빠름).
WaferID 는 매칭 키로만 쓰고, 이후 검토/엑셀 단계는 **원본 폴더명**을 그대로 쓴다.

KLA 가 아닌 장비(예: 파일명 ``81090.137592.c.212779204.1``)는 파일명에 WaferID 가
없어 None 이 되고, 그 경우 폴더명이 매칭 키가 된다(반대쪽 KLA 의 WaferID 와 폴더명이
같으면 매칭).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

# 파일명에서 WaferID(slot명) 추출.
#   - 'XY' + 글자 까지가 slot명이고, 그 뒤의 인덱스 숫자는 buffer 로 버린다.
#   - 앞부분(영숫자)이 충분히 길어야 오탐을 줄인다({6,}).
#   - WaferID 는 대문자라 stem 을 대문자화한 뒤 대문자 패턴으로 찾는다.
#   예) 'W6459080XYH2_3_0_23_1' → 'W6459080XYH'
_FILENAME_WID_RE = re.compile(r"([A-Z0-9]{6,}XY[A-Z])")

# 한 폴더에서 파일명을 확인할 최대 이미지 수(같은 폴더는 동일 WaferID — 다수결).
_MAX_IMAGES_PER_FOLDER = 5


def wafer_id_from_filename(name) -> Optional[str]:
    """파일명(경로 가능)에서 WaferID(slot명)를 추출. 없으면 None.

    예: ``W6459080XYH2_3_0_23_1.jpg`` → ``W6459080XYH``.
    """
    stem = Path(str(name)).name.upper()
    m = _FILENAME_WID_RE.search(stem)
    return m.group(1) if m else None


def wafer_id_from_images(images, limit: int = _MAX_IMAGES_PER_FOLDER) -> Optional[str]:
    """폴더의 이미지 파일명들에서 WaferID 를 추출 — 다수결로 결정.

    같은 폴더의 사진들은 동일 WaferID 를 가지므로, 일부 파일명이 어긋나도 가장 많이
    나온 값을 채택한다.  ``images`` 는 ``ImageItem`` 또는 경로의 목록.
    """
    votes: Counter = Counter()
    for it in list(images)[:max(1, int(limit))]:
        path = getattr(it, "path", it)
        wid = wafer_id_from_filename(path)
        if wid:
            votes[wid] += 1
    return votes.most_common(1)[0][0] if votes else None


def _norm_key(s) -> str:
    return str(s).strip().upper()


def merge_unmatched_by_wafer_id(sr, wid_by_ref: dict, wid_by_val: dict):
    """``ref_only`` / ``val_only`` 폴더를 WaferID 또는 폴더명 일치로 짝지어 병합.

    각 폴더의 **매칭 키 = {폴더명, (있으면) 파일명에서 뽑은 WaferID}**.  ref·val 의
    키 집합이 겹치면 같은 slot 으로 본다.  이로써:
      · 양쪽 다 WaferID → WaferID == WaferID 로 매칭.
      · 한쪽만 WaferID(예: KLA) → 그 WaferID 가 **반대쪽 폴더명**과 같으면 매칭
        (KLA 의 파일명 WaferID 가 반대쪽 폴더명으로 있으면 동일 slot).

    병합된 slot 의 이름은 **원본 ref 폴더명**을 유지한다(이후 검토/엑셀 단계는
    원본 폴더명 사용).  ``sr`` (ScanResult) 를 직접 수정하고, 짝지어진
    ``(ref폴더명, val폴더명)`` 목록을 반환한다.  ``wid_by_*`` : {폴더명 → WaferID}.
    """
    from ..models.slot import ImageItem

    wid_by_ref = wid_by_ref or {}
    wid_by_val = wid_by_val or {}

    def keyset(name, wid):
        ks = {_norm_key(name)}
        if wid:
            ks.add(_norm_key(wid))
        return ks

    val_keys = {v: keyset(v, wid_by_val.get(v)) for v in sr.val_only}

    paired: list[tuple[str, str]] = []
    used_val: set = set()
    for ref_name in list(sr.ref_only):
        rk = keyset(ref_name, wid_by_ref.get(ref_name))
        match_val = None
        for val_name in list(sr.val_only):
            if val_name in used_val:
                continue
            if rk & val_keys.get(val_name, set()):
                match_val = val_name
                break
        if match_val is None:
            continue
        ref_slot = sr.slots.get(ref_name)
        vs = sr.slots.get(match_val)
        if ref_slot is None or vs is None:
            continue
        # val 폴더 이미지를 ref 폴더명 slot 에 합침(slot명 = 원본 ref 폴더명 유지).
        ref_slot.val_images.extend(
            ImageItem(slot=ref_name, path=it.path, side="val")
            for it in vs.val_images
        )
        sr.slots.pop(match_val, None)
        used_val.add(match_val)
        if match_val in sr.val_only:
            sr.val_only.remove(match_val)
        if ref_name in sr.ref_only:
            sr.ref_only.remove(ref_name)
        paired.append((ref_name, match_val))

    return paired
