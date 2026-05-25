"""KLA 검사 사진의 좌상단 헤더에서 ``WaferID`` 텍스트를 OCR 로 읽는 헬퍼.

slot명이 ref/val 간에 일치하지 않을 때, 각 폴더 대표 이미지의 ``WaferID : XXXX``
값을 읽어 **같은 WaferID 끼리 ref↔val 을 짝짓는 매칭 키로만** 사용한다.  매칭
이후 단계(검토/엑셀 등)는 원본 폴더명을 그대로 쓰므로, WaferID 는 어디에도
영속 저장하지 않는다(병합 시 일시적으로만 사용).

OCR 엔진은 RapidOCR(``rapidocr-onnxruntime``).  인식 모델(ONNX)이 pip 패키지에
**내장**되어 있어 런타임 다운로드가 필요 없다(오프라인/폐쇄망 PC 안전, 시스템
바이너리 설치 불필요).  미설치 등으로 사용할 수 없으면 ``ocr_available()`` 이
False 를 반환하고, 호출부는 기존 수동 매핑으로 폴백한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# 좌상단 헤더 크롭 비율 — 상단 헤더 바 + 좌측 Lot/WaferID/Gain 열을 충분히 덮는다.
# (원본 전체 해상도 기준, 헤더 글자는 원본에서만 선명하므로 축소본을 쓰지 않는다.)
_CROP_TOP_FRAC = 0.12
_CROP_LEFT_FRAC = 0.5

# 인식이 빗나갈 때 자동으로 시도하는 크롭 비율 사다리 (top_frac, left_frac).
# 기본 크롭부터 점점 넓혀 가며, 마지막엔 전체 이미지까지 시도한다.
_CROP_LADDER = (
    (0.12, 0.5),     # 기본 — 상단 헤더 + 좌측 열
    (0.20, 0.5),     # 헤더가 더 두꺼운 경우
    (0.12, 1.0),     # 좌측 열이 잘렸거나 열 배치가 다른 경우 — 상단 전체 폭
    (0.30, 0.65),    # 더 넉넉히
    (0.08, 0.35),    # 더 좁게(주변 텍스트 노이즈 최소화)
    (1.0, 1.0),      # 최후 — 전체 이미지(느리지만 확실)
)

# 한 폴더에서 인식을 시도할 최대 이미지 수(첫 장이 실패하면 다음 장으로).
_MAX_IMAGES_PER_FOLDER = 5

# "WaferID : 00MML090XYG5" 형태에서 값(영숫자) 추출.
_WAFER_ID_RE = re.compile(r"WaferID\s*[:：]?\s*([A-Za-z0-9]+)", re.IGNORECASE)

_reader = None          # RapidOCR 엔진 싱글톤(초기화가 느려 1회만 생성)
_reader_failed = False


def ocr_available() -> bool:
    """RapidOCR 사용 가능 여부 — import 가능하면 True (soft dependency 가드)."""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_reader():
    """RapidOCR 엔진을 지연 생성(1회).  실패 시 None.

    인식 모델(ONNX)은 패키지에 내장되어 있어 런타임 다운로드가 없다.
    """
    global _reader, _reader_failed
    if _reader is not None:
        return _reader
    if _reader_failed:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
        _reader = RapidOCR()
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


def _crop_box(size, top_frac: float, left_frac: float):
    w, h = size
    return (0, 0,
            max(1, int(round(w * left_frac))),
            max(1, int(round(h * top_frac))))


def _ocr_text(reader, pil_crop) -> str:
    """RapidOCR 로 크롭 이미지의 텍스트를 합쳐 돌려준다."""
    import numpy as np
    out = reader(np.asarray(pil_crop.convert("RGB")))
    # RapidOCR 반환: (result, elapse).  result 는 [[box, text, score], ...] 또는 None.
    result = out[0] if isinstance(out, tuple) else out
    parts = [item[1] for item in result] if result else []
    return " ".join(parts)


def read_wafer_id(path) -> Optional[str]:
    """이미지의 좌상단 헤더를 OCR 해 WaferID 값을 돌려준다. 실패 시 None.

    **항상 원본 전체 해상도**로 진행하며(헤더 글자는 원본에서만 선명), 인식이
    빗나가면 크롭 비율을 ``_CROP_LADDER`` 순서로 자동 조절하며 재시도한다.
    """
    reader = _get_reader()
    if reader is None:
        return None
    try:
        from . import image_io
        img = image_io._open(Path(path))          # 원본 전체 해상도
    except Exception:
        return None
    for top_frac, left_frac in _CROP_LADDER:
        try:
            crop = img.crop(_crop_box(img.size, top_frac, left_frac))
            wid = _parse_wafer_id(_ocr_text(reader, crop))
            if wid:
                return wid
        except Exception:
            continue
    return None


def read_folder_wafer_id(paths, limit: int = _MAX_IMAGES_PER_FOLDER) -> Optional[str]:
    """한 폴더의 여러 이미지를 차례로 OCR — 첫 성공 값을 돌려준다.

    같은 폴더의 사진들은 WaferID 가 동일하므로, 첫 장이 (크롭 사다리로도) 실패
    하면 다음 장으로 넘어가며 최대 ``limit`` 장까지 시도한다.
    """
    for p in list(paths)[:max(1, int(limit))]:
        wid = read_wafer_id(p)
        if wid:
            return wid
    return None


def header_crop_image(path, top_frac: float = 0.12, left_frac: float = 0.5):
    """수동 매핑 다이얼로그 미리보기용 — 좌상단 헤더 크롭 PIL 이미지(RGB). 실패 시 None.

    OCR 이 끝까지 실패한 폴더를 사용자에게 보여줄 때, 우리가 읽으려 한 ‘그 부분’
    (헤더)을 그대로 보여주기 위한 헬퍼.  Qt 비의존(PIL 만 사용).
    """
    try:
        from . import image_io
        img = image_io._open(Path(path))          # 원본 전체 해상도
        return img.crop(_crop_box(img.size, top_frac, left_frac)).convert("RGB")
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
