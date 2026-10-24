"""KLA .001 정보 파일 파싱 → DefectCoord.

변환식 — die 인덱스 원점은 상수가 아니라 같은 파일의 ``SampleTestPlan`` 에서 읽는다
(:mod:`~.wafer_geometry`)::

    col = XINDEX + geom.zero_x   (TB500: XINDEX 범위 −3..3 → zero_x = 3)
    row = YINDEX + geom.zero_y
    x   = round(XREL)
    y   = round(DiePitchY - YREL)

파일 선택 우선순위:
    1. 확장자 .001 파일
    2. .jpg / .pass 가 아닌 파일(단, 폴더 내에 1개만 있을 때)
    단, .pass 파일은 무시.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import DefectCoord
from .wafer_geometry import kla_geometry
from .ini_text import decode_ini_bytes, read_ini_text

_LOG = logging.getLogger("aoi.coords")

__all__ = ["resolve", "load_folder", "load_folder_raw", "read_wafer_id",
           "read_slot_no", "read_header_ids", "peek_die_pitch"]

# 정보파일 후보에서 제외할 확장자 — 사진/패스/설정/스크립트.
_NON_INFO_SUFFIXES = ('.jpg', '.jpeg', '.pass', '.ini', '.py')

# DefectList 행: 공백/탭 구분 숫자 필드
# 필드 순서(0-based): 0=ID, 1=X, 2=Y, 3=XREL, 4=YREL, 5=XINDEX, 6=YINDEX, ...
_DEFECT_PAT = re.compile(
    r'^\s*([\d.eE+\-]+)'   # 0: ID
    r'\s+([\d.eE+\-]+)'    # 1: X
    r'\s+([\d.eE+\-]+)'    # 2: Y
    r'\s+([\d.eE+\-]+)'    # 3: XREL
    r'\s+([\d.eE+\-]+)'    # 4: YREL
    r'\s+([-\d]+)'         # 5: XINDEX
    r'\s+([-\d]+)(?=[\s;]|$)'  # 6: YINDEX — 뒤가 끊겨야 한다('9.990' 의 '9' 를 잡지 않게)
)

# 결함 행의 시작(ID + 수) — 형식이 깨진 행을 세는 데만 쓴다.
_ROW_START = re.compile(r'^\s*\d+\s+-?[\d.]')

# DiePitch X Y; 헤더 라인
_DIEPITCH_PAT = re.compile(r'DiePitch\s+([\d.eE+\-]+)\s+([\d.eE+\-]+)', re.IGNORECASE)

# TiffFileName 라인 (파일명 추출)
_TIFF_PAT = re.compile(r'TiffFileName\s+(\S+)', re.IGNORECASE)

# WaferID "W6459076XYG1";  — 정보파일 **헤더**의 slot명(WaferID) 줄.
_WAFER_ID_PAT = re.compile(r'Wafer\s*ID\s+"([^"]*)"', re.IGNORECASE)
# Slot 10;  — 같은 헤더의 **카세트 슬롯 번호**(WaferID 바로 아래 줄).
_SLOT_PAT = re.compile(r'^\s*Slot\s+(\d+)\s*;', re.IGNORECASE | re.MULTILINE)
# WaferID 는 헤더(파일 앞부분)에 있으므로 앞부분만 읽는다 — DefectList 가 큰 파일도 저렴하게.
_HEAD_BYTES = 65536


def _find_info_file(folder: Path) -> Optional[Path]:
    """폴더에서 KLA .001 정보 파일 탐색."""
    # 1순위: .001 확장자
    for p in folder.glob("*.001"):
        return p
    # 2순위: .jpg/.pass 가 아닌 파일 (확장자 없는 파일 포함), .pass 제외
    # ★ 조건 순서를 바꾸지 마라 — `is_file()` 은 파일 하나당 stat 이라 **맨 뒤**에 둔다.
    #   맨 앞에 두면 폴더 안 사진을 전부 stat 한다(NAS 에서 사진 수만큼 왕복).  이름만
    #   보면 되는 확장자로 먼저 거르면 stat 대상은 정보파일 후보 1~2개뿐이다.
    #   세 조건 모두 부작용이 없어 순서를 바꿔도 결과는 같다(`test_kla_info_file_lookup`).
    candidates = [
        p for p in folder.iterdir()
        if p.suffix.lower() not in _NON_INFO_SUFFIXES
        and not p.name.startswith('.')
        and p.is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _info_candidates(folder: Path) -> list[Path]:
    """폴더의 정보파일 후보 — ``.001`` 을 앞에, 그다음 나머지 비-사진 파일.

    KLA 결과 폴더에는 사진 외 파일이 1~2개 있고, 확장자가 ``.001`` 이 아니라 **아예
    없을 수도** 있다.  좌표용 :func:`_find_info_file` 은 후보가 2개면 어느 것이 정보파일인지
    확신할 수 없어 None 을 주지만, WaferID 판독은 후보를 **전부** 훑어 첫 성공값을 쓴다."""
    try:
        files = [p for p in folder.iterdir()
                 if p.suffix.lower() not in _NON_INFO_SUFFIXES
                 and not p.name.startswith('.')
                 and p.is_file()]      # ★ stat 은 맨 뒤 — `_find_info_file` 주석 참조
    except OSError:
        return []
    files.sort(key=lambda p: (p.suffix.lower() != '.001', p.name.lower()))
    return files


@lru_cache(maxsize=256)
def read_header_ids(folder: Path) -> tuple[Optional[str], Optional[str]]:
    """정보파일 헤더의 ``(WaferID, Slot)`` — **같은 파일**에서 함께 읽는다.

    KLA 폴더명은 검사 시각(``2026-08-02-23-09_4``)이라 어느 웨이퍼인지 알 수 없다 —
    슬롯을 고르는 화면은 이 두 값으로 웨이퍼를 보여 준다.  KLA 가 아니면 ``(None, None)``.

    후보 파일을 순서대로 시도해 WaferID 가 있는 첫 파일을 쓴다(슬롯 번호도 그 파일의
    값이다 — 다른 파일의 번호를 섞으면 다른 웨이퍼의 번호가 붙는다)."""
    for p in _info_candidates(folder):
        try:
            with p.open("rb") as fh:
                head = fh.read(_HEAD_BYTES)
        except OSError:
            continue
        text = decode_ini_bytes(head)
        m = _WAFER_ID_PAT.search(text)
        if not m:
            continue
        wid = m.group(1).strip().upper()
        if not wid:
            continue
        sm = _SLOT_PAT.search(text)
        return wid, (str(int(sm.group(1))) if sm else None)
    return None, None


def read_wafer_id(folder: Path) -> Optional[str]:
    """폴더의 KLA 정보파일에서 ``WaferID "XXXX";`` 를 읽어 대문자로 반환(없으면 None).

    사진과 무관하게 읽히므로 **사진이 한 장도 없는 폴더도 식별**된다 — 파일명 추측이나
    OCR 로는 불가능했던 경우다.  후보 파일을 순서대로 시도해 첫 성공값을 쓴다."""
    return read_header_ids(Path(folder))[0]


# 예전에는 이 함수 자체가 캐시였다 — 호출부(테스트 픽스처)의 `cache_clear()` 를 그대로 받는다.
read_wafer_id.cache_clear = read_header_ids.cache_clear


def read_slot_no(folder: Path) -> Optional[str]:
    """KLA 정보파일 헤더의 ``Slot N;`` — 카세트 슬롯 번호(문자열).  없으면 None.

    Camtek 의 ``WaferInfo.ini`` ``ActiveSlot`` 과 같은 뜻이다(엑셀 slot# 표기)."""
    return read_header_ids(Path(folder))[1]


def peek_die_pitch(folder: Path) -> Optional[tuple[float, float]]:
    """정보파일 **헤더**(앞 :data:`_HEAD_BYTES`)에서 ``DiePitch X Y`` 만 읽는다.

    설정 화면의 die 안내용이다.  :func:`load_folder` 는 DefectList 전체(수천 줄)를
    파싱하는데, 그 순수 파이썬 파싱이 워커 스레드에서도 GIL 을 쥐어 UI 를 굶겼다
    (`wafer_geometry.peek_die_pitch` 주석).  ``DiePitch`` 는 :func:`read_wafer_id`
    가 읽는 것과 같은 헤더에 있으므로 같은 방식(앞부분만)으로 충분하다.
    KLA 정보파일이 없으면 ``None``."""
    for p in _info_candidates(folder):
        try:
            with p.open("rb") as fh:
                head = fh.read(_HEAD_BYTES)
        except OSError:
            continue
        m = _DIEPITCH_PAT.search(decode_ini_bytes(head))
        if not m:
            continue
        try:
            return (float(m.group(1)), float(m.group(2)))
        except ValueError:
            continue
    return None


@lru_cache(maxsize=256)
def load_folder_raw(folder: Path) -> dict[str, dict]:
    """폴더의 KLA 정보 파일을 파싱해 {stem(소문자) → raw 필드} 맵 반환.

    raw 필드: ``{xrel, yrel, xindex, yindex, die_pitch_y}`` (원본값 그대로).
    변환(load_folder)과 진단 도구가 같은 파싱을 공유하도록 분리한다."""
    info = _find_info_file(folder)
    if info is None:
        return {}
    try:
        return _parse_info_raw(info)
    except Exception:
        return {}


@lru_cache(maxsize=256)
def load_folder(folder: Path) -> dict[str, DefectCoord]:
    """폴더의 KLA 정보 파일을 파싱해 {stem(소문자) → DefectCoord} 맵 반환.

    변환식: col=XINDEX+zero_x, row=YINDEX+zero_y, x=round(XREL),
    y=round(DiePitchY−YREL).  원본 XREL/YREL 은 native_x/native_y 로 함께 보존한다.
    die 인덱스 원점(zero_x/zero_y)은 같은 파일의 SampleTestPlan 에서 읽는다."""
    result: dict[str, DefectCoord] = {}
    raw = load_folder_raw(folder)
    if not raw:
        return result
    geom = kla_geometry(folder)
    for stem, r in raw.items():
        col = r["xindex"] + geom.zero_x
        row = r["yindex"] + geom.zero_y
        x = round(r["xrel"])
        y = round(r["die_pitch_y"] - r["yrel"])
        result[stem] = DefectCoord(
            col=col, row=row, x=float(x), y=float(y), source="kla",
            native_x=float(r["xrel"]), native_y=float(r["yrel"]),
        )
    return result


def _parse_info_raw(path: Path) -> dict[str, dict]:
    text = read_ini_text(path) or ""
    if text and "endoffile" not in text[-4096:].lower():
        # 실측(2026-10-24, LSU TB500 RDL4): 44장 중 2장의 .001 이 결함 행 중간에서 끊겨
        # 있었다 — 뒤쪽 사진 58장은 좌표가 없다.  끊긴 이유는 미확인(장비 기록 중 · 복사).
        # 읽은 데까지는 쓰되 조용히 넘어가지 않는다.
        _LOG.warning("KLA 정보파일 %s 이 끝(EndOfFile)까지 기록되지 않았다 — 뒤쪽 결함 "
                     "사진은 좌표가 없다", path)
    lines = text.splitlines()

    # DiePitchY 추출
    die_pitch_y: Optional[float] = None
    for line in lines:
        m = _DIEPITCH_PAT.search(line)
        if m:
            # DiePitch X Y → 두 번째 값이 Y pitch
            die_pitch_y = float(m.group(2))
            break

    if die_pitch_y is None:
        return {}

    bad_rows = 0

    result: dict[str, dict] = {}
    current_stem: Optional[str] = None

    for line in lines:
        # TiffFileName 라인 → 현재 이미지 stem 갱신
        tm = _TIFF_PAT.match(line)
        if tm:
            tiff_name = tm.group(1).strip()
            current_stem = Path(tiff_name).stem.lower()
            continue

        # DefectList 데이터 행
        if current_stem is not None:
            dm = _DEFECT_PAT.match(line)
            if dm is None and _ROW_START.match(line):
                bad_rows += 1                 # 결함 행처럼 시작하는데 형식이 안 맞는다
                current_stem = None
                continue
            if dm:
                # 실측(2026-10-24, LSU 59295773EWA7): 두 수가 붙어 적힌 행
                # ``43 9784.08023366.970 28203.080 …`` 이 한 칸씩 밀려 읽혀 col/row 가
                # (3,12) 로 조용히 틀렸다.  X/Y 가 수가 아니면 그 행은 쓰지 않는다.
                # (필드 수로는 못 거른다 — IMAGELIST 가 가변 길이라 정상 행도 머리와 다르다.)
                try:
                    float(dm.group(2)), float(dm.group(3))
                except ValueError:
                    bad_rows += 1
                    current_stem = None
                    continue
                try:
                    xrel = float(dm.group(4))
                    yrel = float(dm.group(5))
                    xindex = int(dm.group(6))
                    yindex = int(dm.group(7))
                except ValueError:
                    continue
                result[current_stem] = {
                    "xrel": xrel, "yrel": yrel,
                    "xindex": xindex, "yindex": yindex,
                    "die_pitch_y": die_pitch_y,
                }
                current_stem = None  # 한 TiffFileName 당 하나의 DefectList 행

    if bad_rows:
        _LOG.warning("KLA 정보파일 %s 의 결함 행 %d개가 형식이 깨져(수가 아닌 X/Y) 좌표에서 "
                     "뺐다", path, bad_rows)
    return result


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """이미지 1장 → DefectCoord. 정보 파일이 없거나 섹션이 없으면 None."""
    coords = load_folder(image_path.parent)
    return coords.get(image_path.stem.lower())
