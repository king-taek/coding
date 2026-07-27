"""KLA 폴더의 slot명(WaferID) 해석 — **정보파일 우선, OCR 폴백**.

KLA 결과 폴더에는 사진 외에 정보파일이 1~2개 있고, 그 헤더에 slot명(WaferID)이
``WaferID "W6459076XYG1";`` 형태로 **명시돼 있다**.  이 값을 1순위로 쓴다
(파서: :func:`..coords.kla_info.read_wafer_id`).  정보파일이 없거나 WaferID 줄이
없으면 이미지 좌상단 헤더의 ``WaferID : XXXX`` 텍스트를 **OCR**(RapidOCR) 로 읽는다.

정보파일은 사진과 무관하게 읽히므로 **사진이 한 장도 없는 폴더도 식별**된다 —
사진 파일명 추측이나 OCR 로는 불가능했던 경우다.

해석된 WaferID 는 **매칭 키로만** 쓰고 어디에도 영속 저장하지 않는다(병합 시
일시 사용).  매칭 이후(검토/엑셀)는 병합된 slot명을 쓴다.

OCR 엔진은 RapidOCR(``rapidocr-onnxruntime``) — 인식 모델(ONNX)이 패키지에 내장돼
런타임 다운로드가 없다(오프라인/폐쇄망 안전).  미설치면 ``ocr_available()`` 이
False 이고 OCR 폴백은 자동으로 비활성된다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 1) OCR 폴백 (RapidOCR) — 이미지 좌상단 헤더의 'WaferID : XXXX' 판독
# ---------------------------------------------------------------------------
_DET_LIMIT_SIDE_LEN = 640          # 검출 입력 한 변 — 헤더 글자 검출 정확도↑(과거 320)
# KLA 사진 **왼쪽 최상단** 헤더(Lot:/WaferID:/Gain:) 영역만 좁게 크롭(가로·세로를
# 절반으로 — 너무 넓으면 OCR 정확도↓).  못 읽으면 한 단계 넓혀 재시도.
# (top_frac, left_frac)
_CROP_LADDER = ((0.09, 0.25), (0.15, 0.5))
_MAX_IMAGES_PER_FOLDER = 3         # 폴더당 최대 시도 장수
_VOTE_EARLY_STOP = 1               # 첫 성공 판독에서 즉시 종료
_MIN_CONF = 0.30
# 'WaferID : XXXX' / 'WAFER ID: XXXX' 둘 다 매칭(WAFER 와 ID 사이 공백 허용),
# 콜론/공백 변형 허용.  Lot:/Gain: 줄과 섞여 있어도 WaferID 값만 추출.
_WAFER_ID_RE = re.compile(r"WAFER\s*ID\s*[:：]?\s*([A-Za-z0-9]+)", re.IGNORECASE)

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


def header_crop_image(path, top_frac: float = 0.09, left_frac: float = 0.25):
    """매핑 미리보기용 — 좌상단 헤더(WaferID 등) 크롭 PIL 이미지(RGB).  OCR 이 읽으려
    한 ‘그 부분’ 을 사용자에게 보여줘 직접 WaferID 를 읽게 한다(좁게 크롭)."""
    try:
        from . import image_io
        img = image_io._open(Path(path))
        return img.crop(_crop_box(img.size, top_frac, left_frac)).convert("RGB")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2) WaferID/폴더명 키로 ref_only ↔ val_only 병합
# ---------------------------------------------------------------------------
def _norm_key(s) -> str:
    return str(s).strip().upper()


def merge_unmatched_by_wafer_id(sr, wid_by_ref: dict, wid_by_val: dict):
    """``ref_only``/``val_only`` 를 ‘폴더명 또는 WaferID’ 키 교집합으로 병합.

    각 폴더 키 = {폴더명, (있으면) WaferID}.  ref·val 키가 겹치면 같은 slot.
    병합 **slot명 = 교집합 키(WaferID 우선)** — KLA 가 기준/검증 어느 쪽이든 깔끔한
    WaferID 가 slot명이 된다(KLA 의 임의 폴더명이 slot명이 되는 것 방지).
    ``sr`` 를 직접 수정하고 짝지은 ``(ref, val)`` 목록 반환.  ``wid_by_*`` : {폴더명 → WaferID}."""
    from ..models.slot import ImageItem, Slot

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
        slot_name = None
        for val_name in list(sr.val_only):
            if val_name in used_val:
                continue
            inter = rk & val_keys.get(val_name, set())
            if not inter:
                continue
            match_val = val_name
            # slot명 = 교집합 키 중 WaferID 우선(없으면 임의의 교집합 키).
            wids = {_norm_key(w) for w in
                    (wid_by_ref.get(ref_name), wid_by_val.get(val_name)) if w}
            pref = inter & wids
            slot_name = sorted(pref)[0] if pref else sorted(inter)[0]
            break
        if match_val is None:
            continue
        ref_slot = sr.slots.get(ref_name)
        vs = sr.slots.get(match_val)
        if ref_slot is None or vs is None:
            continue
        # slot명이 **무관한 기존 슬롯**과 겹치면 병합하지 않는다 — 겹친 채로 진행하면
        # 그 슬롯의 사진을 덮어써 웨이퍼가 조용히 사라진다.  두 폴더는 ref_only/val_only
        # 에 남아 수동 매핑으로 넘어간다(정확도 우선).
        existing = sr.slots.get(slot_name)
        if existing is not None and slot_name not in (ref_name, match_val):
            continue
        merged = existing or Slot(name=slot_name)
        merged.ref_images = [ImageItem(slot=slot_name, path=it.path, side="ref")
                             for it in ref_slot.ref_images]
        merged.val_images = [ImageItem(slot=slot_name, path=it.path, side="val")
                             for it in vs.val_images]
        # 원본 폴더 경로를 옮겨 둔다 — 사진 0장 폴더의 정보파일을 나중에도 찾을 수 있게.
        merged.ref_dir = ref_slot.ref_dir
        merged.val_dir = vs.val_dir
        if ref_name != slot_name:
            sr.slots.pop(ref_name, None)
        if match_val != slot_name:
            sr.slots.pop(match_val, None)
        sr.slots[slot_name] = merged
        used_val.add(match_val)
        for n in (ref_name, match_val, slot_name):
            if n in sr.ref_only:
                sr.ref_only.remove(n)
            if n in sr.val_only:
                sr.val_only.remove(n)
        paired.append((ref_name, match_val))
    return paired
