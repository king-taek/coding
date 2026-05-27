"""KLA 폴더의 slot명(WaferID) 해석 — **파일명 우선, OCR 폴백**.

KLA 장비 사진의 slot명(WaferID)은 보통 **파일명 앞부분**에 들어 있다.
  예) ``W6459153XYF5_3_0_23_1.jpg`` → ``W6459153XYF5``
파일명이 WaferID 형식이 아닌 경우(예) ``FrontSideADRImg_544131.jpg``) 에는
이미지 좌상단 헤더의 ``WaferID : XXXX`` 텍스트를 **OCR**(RapidOCR) 로 읽는다.
사진이 한 장도 없는 폴더는 매칭하지 않고 ‘사진파일 없음’ 으로 둔다.

해석된 WaferID 는 **매칭 키로만** 쓰고 어디에도 영속 저장하지 않는다(병합 시
일시 사용).  매칭 이후(검토/엑셀)는 원본 폴더명을 그대로 쓴다.

OCR 엔진은 RapidOCR(``rapidocr-onnxruntime``) — 인식 모델(ONNX)이 패키지에 내장돼
런타임 다운로드가 없다(오프라인/폐쇄망 안전).  미설치면 ``ocr_available()`` 이
False 이고 OCR 폴백은 자동으로 비활성된다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 1) 파일명에서 WaferID 파싱 (OCR 불필요 — 빠름)
# ---------------------------------------------------------------------------
def _is_wafer_id(token: str) -> bool:
    """토큰이 WaferID 형식인지 — 영숫자 8~16자, 숫자 ≥3 & 영문 ≥2.

    ``W6459153XYF5``·``00NJ3159XYC1`` 는 통과, ``FrontSideADRImg``(숫자 0개) 는
    탈락 → 형식이 아니면 OCR 폴백으로 넘긴다."""
    if not token or not token.isalnum():
        return False
    if not (8 <= len(token) <= 16):
        return False
    n_dig = sum(c.isdigit() for c in token)
    n_alpha = sum(c.isalpha() for c in token)
    return n_dig >= 3 and n_alpha >= 2


def parse_wafer_id_from_filename(name) -> Optional[str]:
    """파일명 첫 ``_`` 앞 토큰이 WaferID 형식이면 대문자로 반환, 아니면 None.

    예) ``W6459153XYF5_3_0_23_1.jpg`` → ``W6459153XYF5``."""
    stem = Path(str(name)).stem
    token = stem.split("_", 1)[0].strip()
    return token.upper() if _is_wafer_id(token) else None


def folder_wafer_id_from_filenames(paths) -> Optional[str]:
    """폴더 이미지들의 파일명에서 WaferID 를 다수결로 정한다(OCR 불필요).

    같은 폴더 사진은 보통 동일 WaferID 이므로 첫 유효 토큰을 곧장 채택해도 되지만,
    드문 혼입에 대비해 다수결로 고른다.  하나도 형식에 안 맞으면 None(→OCR 폴백)."""
    votes: dict[str, int] = {}
    for p in paths:
        wid = parse_wafer_id_from_filename(getattr(p, "name", None) or Path(str(p)).name)
        if wid:
            votes[wid] = votes.get(wid, 0) + 1
    if not votes:
        return None
    return max(votes.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# 2) OCR 폴백 (RapidOCR) — 이미지 좌상단 헤더의 'WaferID : XXXX' 판독
# ---------------------------------------------------------------------------
_DET_LIMIT_SIDE_LEN = 320          # 검출 입력 한 변 — 속도/정확 균형
_CROP_LADDER = ((0.12, 0.5), (0.20, 1.0))   # (top_frac, left_frac) 헤더 크롭 사다리
_MAX_IMAGES_PER_FOLDER = 5
_VOTE_EARLY_STOP = 2
_MIN_CONF = 0.30
_WAFER_ID_RE = re.compile(r"WaferID\s*[:：]?\s*([A-Za-z0-9]+)", re.IGNORECASE)

_reader = None
_reader_failed = False


def ocr_available() -> bool:
    """RapidOCR 사용 가능 여부 — import 되면 True(soft dependency)."""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_reader():
    global _reader, _reader_failed
    if _reader is not None:
        return _reader
    if _reader_failed:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
        _reader = RapidOCR(det_limit_side_len=_DET_LIMIT_SIDE_LEN)
    except Exception:
        _reader_failed = True
        _reader = None
    return _reader


def _parse_wafer_id(text: str) -> Optional[str]:
    if not text:
        return None
    m = _WAFER_ID_RE.search(text)
    return (m.group(1).strip().upper() or None) if m else None


def _crop_box(size, top_frac: float, left_frac: float):
    w, h = size
    return (0, 0, max(1, int(round(w * left_frac))),
            max(1, int(round(h * top_frac))))


def _texts_from_result(result) -> list:
    out = []
    for item in (result or []):
        try:
            if len(item) >= 3:                 # det+rec: [box, text, score]
                out.append((str(item[1]), float(item[2])))
            elif len(item) == 2:               # rec-only: [text, score]
                out.append((str(item[0]), float(item[1])))
        except Exception:
            continue
    return out


def _wafer_candidates(result):
    seq = _texts_from_result(result)
    cands = []
    for text, score in seq:
        wid = _parse_wafer_id(text)
        if wid:
            cands.append((wid, score))
    if not cands and seq:
        joined = " ".join(t for t, _ in seq)
        wid = _parse_wafer_id(joined)
        if wid:
            cands.append((wid, sum(s for _, s in seq) / len(seq)))
    return cands


def _read_one(path):
    reader = _get_reader()
    if reader is None:
        return None
    try:
        from . import image_io
        img = image_io._open(Path(path))
    except Exception:
        return None
    import numpy as np
    for top_frac, left_frac in _CROP_LADDER:
        try:
            crop = img.crop(_crop_box(img.size, top_frac, left_frac))
            out = reader(np.asarray(crop.convert("RGB")))
            result = out[0] if isinstance(out, tuple) else out
            cands = [c for c in _wafer_candidates(result) if c[1] >= _MIN_CONF]
            if cands:
                return max(cands, key=lambda c: c[1])
        except Exception:
            continue
    return None


def read_wafer_id(path) -> Optional[str]:
    """이미지 헤더를 OCR 해 WaferID 반환(실패 시 None).  항상 원본 해상도."""
    r = _read_one(path)
    return r[0] if r else None


def read_folder_wafer_id(paths, limit: int = _MAX_IMAGES_PER_FOLDER) -> Optional[str]:
    """폴더 여러 이미지를 OCR 해 다수결로 WaferID 결정(정확도↑)."""
    paths = list(paths)
    if not paths:
        return None
    votes: dict[str, list] = {}
    for p in paths[:max(1, int(limit))]:
        r = _read_one(p)
        if r:
            wid, conf = r
            v = votes.setdefault(wid, [0, 0.0])
            v[0] += 1
            v[1] += float(conf)
            if v[0] >= _VOTE_EARLY_STOP:
                break
    if not votes:
        return None
    return max(votes.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]


def header_crop_image(path, top_frac: float = 0.12, left_frac: float = 0.5):
    """매핑 미리보기용 — 좌상단 헤더 크롭 PIL 이미지(RGB).  OCR 이 읽으려 한 ‘그
    부분’ 을 사용자에게 보여줄 때 사용(Qt 비의존)."""
    try:
        from . import image_io
        img = image_io._open(Path(path))
        return img.crop(_crop_box(img.size, top_frac, left_frac)).convert("RGB")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 3) WaferID/폴더명 키로 ref_only ↔ val_only 병합
# ---------------------------------------------------------------------------
def _norm_key(s) -> str:
    return str(s).strip().upper()


def merge_unmatched_by_wafer_id(sr, wid_by_ref: dict, wid_by_val: dict):
    """``ref_only``/``val_only`` 를 ‘폴더명 또는 WaferID’ 키 교집합으로 병합.

    각 폴더 키 = {폴더명, (있으면) WaferID}.  ref·val 키가 겹치면 같은 slot.
    병합 slot명 = 원본 ref 폴더명 유지.  ``sr`` 를 직접 수정하고 짝지은
    ``(ref, val)`` 목록 반환.  ``wid_by_*`` : {폴더명 → WaferID}."""
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
