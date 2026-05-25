"""KLA 검사 사진의 좌상단 헤더에서 ``WaferID`` 텍스트를 OCR 로 읽는 헬퍼.

slot명이 ref/val 간에 일치하지 않을 때, 각 폴더 대표 이미지의 ``WaferID : XXXX``
값을 읽어 **같은 WaferID 끼리 ref↔val 을 짝짓는 매칭 키로만** 사용한다.  매칭
이후 단계(검토/엑셀 등)는 원본 폴더명을 그대로 쓰므로, WaferID 는 어디에도
영속 저장하지 않는다(병합 시 일시적으로만 사용).

OCR 엔진은 EasyOCR(torch 기반).  미설치/모델 부재 등으로 사용할 수 없으면
``ocr_available()`` 이 False 를 반환하고, 호출부는 기존 수동 매핑으로 폴백한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# 좌상단 헤더 크롭 비율 — 상단 헤더 바 + 좌측 Lot/WaferID/Gain 열을 충분히 덮는다.
# (원본 전체 해상도 기준, 헤더 글자는 원본에서만 선명하므로 축소본을 쓰지 않는다.)
_CROP_TOP_FRAC = 0.12
_CROP_LEFT_FRAC = 0.5
_OCR_LANGS = ("en",)

# "WaferID : 00MML090XYG5" 형태에서 값(영숫자) 추출.
_WAFER_ID_RE = re.compile(r"WaferID\s*[:：]?\s*([A-Za-z0-9]+)", re.IGNORECASE)

_reader = None          # EasyOCR Reader 싱글톤(모델 로딩이 느려 1회만 생성)
_reader_failed = False


def ocr_available() -> bool:
    """EasyOCR 사용 가능 여부 — import 가능하면 True (soft dependency 가드)."""
    try:
        import easyocr  # noqa: F401
        return True
    except Exception:
        return False


def _get_reader():
    """EasyOCR Reader 를 지연 생성(모델 로딩 1회).  실패 시 None."""
    global _reader, _reader_failed
    if _reader is not None:
        return _reader
    if _reader_failed:
        return None
    try:
        import easyocr
        _reader = easyocr.Reader(list(_OCR_LANGS), gpu=False)
    except Exception:
        _reader_failed = True
        _reader = None
    return _reader


def _parse_wafer_id(text: str) -> Optional[str]:
    """OCR 로 읽은 텍스트에서 WaferID 값을 추출(대문자/공백 정규화). 없으면 None."""
    if not text:
        return None
    m = _WAFER_ID_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().upper() or None


def read_wafer_id(path) -> Optional[str]:
    """이미지의 좌상단 헤더를 OCR 해 WaferID 값을 돌려준다. 실패 시 None."""
    reader = _get_reader()
    if reader is None:
        return None
    try:
        import numpy as np

        from . import image_io
        img = image_io._open(Path(path))          # 원본 전체 해상도
        w, h = img.size
        crop = img.crop((
            0, 0,
            max(1, int(w * _CROP_LEFT_FRAC)),
            max(1, int(h * _CROP_TOP_FRAC)),
        ))
        arr = np.asarray(crop.convert("RGB"))
        parts = reader.readtext(arr, detail=0)
        text = " ".join(parts) if parts else ""
        return _parse_wafer_id(text)
    except Exception:
        return None


def merge_unmatched_by_wafer_id(sr, wid_by_ref: dict, wid_by_val: dict):
    """WaferID 가 같은 ``ref_only`` / ``val_only`` 폴더를 짝지어 병합한다.

    병합된 slot 의 이름은 **원본 ref 폴더명**을 유지한다(WaferID 는 매칭 키로만
    사용 — 이후 검토/엑셀 단계는 원본 폴더명을 그대로 사용).  ``sr`` (ScanResult)
    를 직접 수정하고, 짝지어진 ``(ref폴더명, val폴더명)`` 목록을 반환한다.

    ``wid_by_ref`` / ``wid_by_val`` : {폴더명 → WaferID}.
    """
    from ..models.slot import ImageItem

    ref_by_wid: dict[str, list[str]] = {}
    val_by_wid: dict[str, list[str]] = {}
    for name, wid in (wid_by_ref or {}).items():
        if wid:
            ref_by_wid.setdefault(wid, []).append(name)
    for name, wid in (wid_by_val or {}).items():
        if wid:
            val_by_wid.setdefault(wid, []).append(name)

    paired: list[tuple[str, str]] = []
    for wid in sorted(set(ref_by_wid) & set(val_by_wid)):
        ref_names = ref_by_wid[wid]
        val_names = val_by_wid[wid]
        primary = ref_names[0]
        ref_slot = sr.slots.get(primary)
        if ref_slot is None:
            continue
        # 같은 WaferID 의 다른 ref 폴더가 있으면 primary 로 합침(드문 경우).
        for extra in ref_names[1:]:
            es = sr.slots.get(extra)
            if es is None:
                continue
            ref_slot.ref_images.extend(
                ImageItem(slot=primary, path=it.path, side="ref")
                for it in es.ref_images
            )
            sr.slots.pop(extra, None)
            if extra in sr.ref_only:
                sr.ref_only.remove(extra)
        # val 폴더 이미지를 primary slot 에 합침(slot명 = 원본 ref 폴더명 유지).
        for vname in val_names:
            vs = sr.slots.get(vname)
            if vs is None:
                continue
            ref_slot.val_images.extend(
                ImageItem(slot=primary, path=it.path, side="val")
                for it in vs.val_images
            )
            sr.slots.pop(vname, None)
            if vname in sr.val_only:
                sr.val_only.remove(vname)
            paired.append((primary, vname))
        if primary in sr.ref_only:
            sr.ref_only.remove(primary)

    return paired
