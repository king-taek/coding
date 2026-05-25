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

# 빠른 경로(rec-only) — 상단 헤더의 ‘겹치지 않는’ 줄 밴드들(top_frac, bottom_frac).
# 각 밴드를 검출 없이 인식만(use_det=False) 돌리면 한 줄을 ~0.05s 에 읽는다.
# WaferID 가 들어간 밴드를 먼저 만나면 즉시 반환(보통 2밴드 이내).
_LINE_BANDS = (
    (0.000, 0.030),   # 1행 (Lot)
    (0.028, 0.052),   # 2행 (WaferID) — 대개 여기서 성공
    (0.050, 0.082),   # 3행 (Gain)
)
_BAND_LEFT_FRAC = 0.45

# 느린 폴백(det+rec) 크롭 사다리 — 빠른 경로가 모두 실패할 때만, 소수 이미지에만.
# 전체-이미지(1.0,1.0) 패스는 가장 느려 제거하고 3패스로 상한을 둔다.
_CROP_LADDER = (
    (0.12, 0.5),     # 기본 — 상단 헤더 + 좌측 열
    (0.20, 1.0),     # 좌측 열이 잘렸거나 열 배치가 다른 경우 — 상단 전체 폭
    (0.30, 0.65),    # 더 넉넉히
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


def _texts_from_result(result) -> list:
    """RapidOCR 결과에서 텍스트만 추출.

    det+rec 모드 item 은 ``[box, text, score]`` (len 3), rec-only 모드 item 은
    ``[text, score]`` (len 2) 로 형태가 다르므로 둘 다 처리한다.
    """
    out = []
    for item in (result or []):
        try:
            if len(item) >= 3:
                out.append(str(item[1]))
            elif len(item) == 2:
                out.append(str(item[0]))
        except Exception:
            continue
    return out


def _band_box(size, top_frac: float, bottom_frac: float, left_frac: float):
    w, h = size
    return (0, max(0, int(round(h * top_frac))),
            max(1, int(round(w * left_frac))),
            max(1, int(round(h * bottom_frac))))


def _read_fast(reader, img) -> Optional[str]:
    """rec-only 빠른 경로 — 헤더 줄 밴드들을 검출 없이 인식만 돌린다(빠름).

    겹치지 않는 한 줄짜리 밴드를 순서대로 인식하고, WaferID 가 잡히면 즉시 반환.
    """
    import numpy as np
    for top_f, bot_f in _LINE_BANDS:
        try:
            crop = img.crop(_band_box(img.size, top_f, bot_f, _BAND_LEFT_FRAC))
            out = reader(np.asarray(crop.convert("RGB")),
                         use_det=False, use_cls=False, use_rec=True)
            result = out[0] if isinstance(out, tuple) else out
            wid = _parse_wafer_id(" ".join(_texts_from_result(result)))
            if wid:
                return wid
        except Exception:
            continue
    return None


def _read_robust(reader, img) -> Optional[str]:
    """느린 폴백 — det+rec 크롭 사다리(빠른 경로가 실패할 때만)."""
    import numpy as np
    for top_frac, left_frac in _CROP_LADDER:
        try:
            crop = img.crop(_crop_box(img.size, top_frac, left_frac))
            out = reader(np.asarray(crop.convert("RGB")))
            result = out[0] if isinstance(out, tuple) else out
            wid = _parse_wafer_id(" ".join(_texts_from_result(result)))
            if wid:
                return wid
        except Exception:
            continue
    return None


def _read_one(path, robust: bool) -> Optional[str]:
    """원본을 1회 열어 빠른 경로 → (robust 면) 느린 폴백 순으로 시도."""
    reader = _get_reader()
    if reader is None:
        return None
    try:
        from . import image_io
        img = image_io._open(Path(path))          # 항상 원본 전체 해상도
    except Exception:
        return None
    wid = _read_fast(reader, img)
    if wid or not robust:
        return wid
    return _read_robust(reader, img)


def read_wafer_id(path) -> Optional[str]:
    """이미지의 좌상단 헤더를 OCR 해 WaferID 값을 돌려준다. 실패 시 None.

    **항상 원본 전체 해상도**로 진행한다.  먼저 검출을 건너뛰는 rec-only 빠른
    경로를 쓰고, 실패하면 det+rec 크롭 사다리로 폴백한다.
    """
    return _read_one(path, robust=True)


def read_folder_wafer_id(paths, limit: int = _MAX_IMAGES_PER_FOLDER) -> Optional[str]:
    """한 폴더의 여러 이미지를 OCR — 첫 성공 값을 돌려준다.

    같은 폴더의 사진들은 WaferID 가 동일하므로, **빠른 경로(rec-only)를 먼저**
    여러 장에 적용하고(폴더당 최대 ``limit`` 장), 전부 실패할 때만 느린 det+rec
    폴백을 첫 1장에만 적용해 최악 비용을 제한한다.
    """
    paths = list(paths)
    if not paths:
        return None
    n = max(1, int(limit))
    # 1) 빠른 경로(rec-only)를 이미지들에 우선 적용.
    for p in paths[:n]:
        wid = _read_one(p, robust=False)
        if wid:
            return wid
    # 2) 전부 실패 시에만 느린 det+rec 폴백을 첫 장에 적용.
    return _read_robust_only(paths[0])


def _read_robust_only(path) -> Optional[str]:
    """느린 폴백 경로만 단독 실행(빠른 경로가 이미 실패한 뒤 첫 장에 사용)."""
    reader = _get_reader()
    if reader is None:
        return None
    try:
        from . import image_io
        img = image_io._open(Path(path))
    except Exception:
        return None
    return _read_robust(reader, img)


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


def _norm_key(s) -> str:
    return str(s).strip().upper()


def merge_unmatched_by_wafer_id(sr, wid_by_ref: dict, wid_by_val: dict):
    """``ref_only`` / ``val_only`` 폴더를 WaferID 또는 폴더명 일치로 짝지어 병합.

    각 폴더의 **매칭 키 = {폴더명, (있으면) OCR 로 읽은 WaferID}**.  ref·val 의
    키 집합이 겹치면 같은 slot 으로 본다.  이로써:
      · 양쪽 다 OCR → WaferID == WaferID 로 매칭.
      · 한쪽만 OCR → 그쪽 WaferID 가 **반대쪽 폴더명**과 같으면 매칭(사용자 규칙:
        검증/기준 사진의 WaferID 가 반대쪽 폴더명으로 있으면 동일 slot).

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
