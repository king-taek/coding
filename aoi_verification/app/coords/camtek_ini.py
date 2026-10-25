"""Camtek INI 파일(ColorImageGrabingInfo.ini) 파싱 → DefectCoord.

변환식 — 상수가 아니라 폴더에서 읽은 :class:`~.wafer_geometry.CamtekGeometry` 를 쓴다
(die 크기가 다른 디바이스 지원).  **die 인덱스는 좌표에서 유도한다** — INI 의 ``Col``/
``Row`` 필드는 레시피마다 원점이 다를 수 있어 변환에 쓰지 않는다::

    x_index = floor(X / geom.pitch_x)       y_index = floor(Y / geom.pitch_y)
    col = x_index - geom.col_origin         # col_origin  = 첫 완전 die 열
    row = geom.row_total - y_index          # row_total   = 마지막 완전 die 행
    x   = floor(X - x_index × pitch_x)      # 장비 표기 = 버림 (반올림 아님 — 실측:
    y   = floor(Y - y_index × pitch_y)      #   나머지 43180.809 를 장비는 43180 으로 표기)
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .models import (CAMTEK_COL_OFFSET, NUMBER_WARN_NO_GRID, NUMBER_WARN_NO_TABLE,
                     DefectCoord)
from .wafer_geometry import (EXPORT_SOURCE_TAG, CamtekGeometry, FALLBACK_CAMTEK,
                             camtek_geometry)
from .ini_text import read_ini_text

__all__ = ["resolve", "load_folder", "load_abs_folder", "load_raw_folder",
           "load_recipe_folder", "abs_coord", "has_ini"]

_LOG = logging.getLogger("aoi.coords")

# INI 파일 이름 후보 — 대소문자 두 가지
_INI_CANDIDATES = ("ColorImageGrabingInfo.ini", "ColorImageGrabinginfo.ini")

# ★ '=' 앞뒤는 ``\s`` 가 아니라 ``[ \t]`` — ``\s*`` 는 줄바꿈까지 먹어서 값이 빈 키(``Foo=``)가
#   다음 줄(``X=100.0``)을 자기 값으로 삼켜 그 섹션이 좌표를 잃었다.
_KEY_PAT = re.compile(r'^(\w+)[ \t]*=[ \t]*(.+)$', re.MULTILINE)
_SECTION_PAT = re.compile(r'\[([^\]]+)\]')


def _find_ini(folder: Path) -> Optional[Path]:
    for name in _INI_CANDIDATES:
        p = folder / name
        if p.exists():
            return p
    return None


def has_ini(folder: Path) -> bool:
    """이 폴더에 Camtek INI 가 **있는가** — 파일 존재만 본다(파싱하지 않는다).

    설정 화면의 die 안내가 '변환할 INI 는 있는데 pitch 를 적은 파일이 없다' 를
    판정하는 데 쓴다.  :func:`~.wafer_geometry.has_camtek_entries` 는 INI 를 통째로
    파싱하는데(결함 수천 건, 순수 파이썬), 그 비용이 UI 스레드를 굶겼다
    (`wafer_geometry.peek_die_pitch` 주석).  존재 확인은 stat 두 번이다."""
    try:
        return _find_ini(Path(folder)) is not None
    except OSError:
        return False


@lru_cache(maxsize=256)
def load_folder(folder: Path) -> dict[str, DefectCoord]:
    """폴더의 INI 파일을 파싱해 {stem(소문자) → DefectCoord} 맵 반환.

    같은 폴더의 두 번째 이미지부터는 캐시에서 즉시 반환된다.
    """
    try:
        entries = _sections(folder)
        if not entries:
            return {}
        # die 격자 기하는 폴더당 한 번만 구한다(자체 캐시).
        geom = camtek_geometry(folder)
        if geom is None:
            # ★ pitch 를 몰라도 **매칭은 된다** — INI 의 절대 X/Y 를 그대로 쓰면 된다.
            #   die 로 쪼개는 건 (a) 화면·엑셀의 die-내부 좌표 표기와 (b) KLA 교차 매칭에만
            #   필요하다.  Camtek↔Camtek 은 ref/val 이 같은 규칙이면 그만이다.
            out = _abs_entries(entries)
        else:
            out = {stem: _coord_from_raw(raw, geom)
                   for stem, raw, _xy, _recipe in entries if raw is not None}
        shift = display_shift(folder)
        if shift != (0, 0):
            out = {k: replace(c, col_shift=shift[0], row_shift=shift[1])
                   for k, c in out.items()}
        return out
    except Exception:
        _LOG.warning("Camtek INI 파싱 실패 — 이 폴더의 좌표를 비웁니다: %s", folder,
                     exc_info=True)
        return {}


_RECIPE_INI = "Recipe.ini"
_SHIFT_KEYS = ("DisplayColShift", "DisplayRowShift")


@lru_cache(maxsize=256)
def display_shift(folder: Path) -> tuple[int, int]:
    """``Recipe.ini [Display]`` 의 ``(DisplayColShift, DisplayRowShift)`` — 없으면 ``(0, 0)``.

    장비가 화면에 col/row 를 **이만큼 더해서** 보여 준다.  실측(2026-10-25, T254 FS UBM
    ``TSC-FS``): ``DisplayColShift=-1`` 인 레시피에서 앱 col 9 인 결함을 장비 화면·LIVE
    파일명은 8 로 적었고, 같은 칸을 장비 맵에서 확인했다(관측, 42장).  다른 디바이스
    68장은 전부 0.  ⚠ row 시프트가 0 이 아닌 실물은 아직 없다 — 같은 부호로 더한다는 건
    col 에서 옮겨 온 **가정**이다.

    매칭·Wafer map 위치에는 쓰지 않는다 — KLA·LOT 맵 txt·시프트 없는 레시피는 실제 die
    번호를 쓰므로, 시프트를 섞으면 같은 die 가 1 어긋난다.  값은 줄 끝 ``; 주석`` 을 버리고
    정수로 읽으며, 정수가 아니면 경고하고 0 으로 둔다(화면 표기만 바뀌므로 매칭은 안전)."""
    p = Path(folder) / _RECIPE_INI
    text = read_ini_text(p)
    if text is None:
        return (0, 0)
    found = {k: 0 for k in _SHIFT_KEYS}
    section = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]").strip().casefold()
            continue
        if section != "display" or "=" not in line:
            continue
        key, val = (t.strip() for t in line.split("=", 1))
        if key not in found:
            continue
        val = val.split(";", 1)[0].strip()
        try:
            found[key] = int(val)
        except ValueError:
            _LOG.warning("%s 의 %s=%r 를 정수로 못 읽어 0 으로 둡니다.", p, key, val)
    return (found["DisplayColShift"], found["DisplayRowShift"])


def _extract_coord(content: str,
                   geom: CamtekGeometry = FALLBACK_CAMTEK) -> Optional[DefectCoord]:
    """INI 섹션 내용 → DefectCoord. 필수 키가 없으면 None.

    ``geom`` 은 폴더에서 읽은 die 격자 기하 — 생략하면 TB500 폴백."""
    raw = _extract_raw(content)
    if raw is None:
        return None
    return _coord_from_raw(raw, geom)


def _coord_from_raw(raw: tuple[float, float, int, int],
                    geom: CamtekGeometry) -> DefectCoord:
    """원시 ``(X, Y, Col, Row)`` → die-내부 DefectCoord."""
    X, Y, _col_i, _row_i = raw
    # ★ die 인덱스는 **좌표에서 유도**한다 — INI 의 Col/Row 필드를 쓰지 않는다.
    #   실측: 같은 결함을 두 레시피가 찍으면 Col 은 같은데 Row 만 1 어긋난다
    #   (recipe1 Row=3, recipe2 Row=4).  필드를 그대로 쓰면 die 내부 y 가 −15349 같은
    #   음수가 된다 — die 안의 위치로 불가능한 값이다.  그 필드는 pitch 검산의
    #   참조값으로만 쓴다(`wafer_geometry._grid_check`).
    x_index = math.floor(X / geom.pitch_x)
    y_index = math.floor(Y / geom.pitch_y)
    col = x_index - geom.col_origin
    row = geom.row_total - y_index
    # 버림(floor) — 장비 화면·정답 데이터와 표기를 일치시킨다.  거리 계산 변화는
    # ref/val 양쪽 동일 규칙으로 2 µm 미만이라 매칭 판정(20/200 µm)에 영향 없다.
    x = float(math.floor(X - x_index * geom.pitch_x))
    y = float(math.floor(Y - y_index * geom.pitch_y))
    # 번호 기준을 장비 번호표에서 읽지 못했으면 유도값이라 틀렸을 수 있다 — 화면에 경고만.
    warn = "" if EXPORT_SOURCE_TAG in geom.source else NUMBER_WARN_NO_TABLE
    return DefectCoord(col=col, row=row, x=x, y=y, source="camtek_ini", number_warn=warn)


def abs_coord(X: float, Y: float, col_i: int, row_i: int) -> DefectCoord:
    """원시 ``(X, Y, Col, Row)`` → **절대 wafer 좌표** DefectCoord (순수 함수).

    :func:`_parse_ini_abs` 와 :func:`..coords.resolve_batch` 의 프레임 통일이 공유한다."""
    # col 은 상수 오프셋이라 그 자재의 첫 열이 2 가 아니면 틀린다(RDL4 1 · QORVO 5, 관측) —
    # 화면·엑셀은 숫자 대신 경고를 낸다(사용자 결정 2026-10-25).  매칭은 버킷 키로만 쓴다.
    return DefectCoord(col=col_i - CAMTEK_COL_OFFSET, row=-row_i,
                       x=float(math.floor(X)), y=float(math.floor(Y)),
                       source="camtek_abs", number_warn=NUMBER_WARN_NO_GRID)


def _abs_entries(entries) -> dict[str, DefectCoord]:
    """die pitch 를 모를 때의 폴백 — **절대 wafer 좌표**로 DefectCoord 를 만든다.

    · ``col`` 은 pitch 없이도 정확하다(``Col − CAMTEK_COL_OFFSET``).
    · ``row`` 은 기준(``ceil(Diameter/pitch_y)``)을 모르므로 **장비 화면 값을 낼 수 없다**.
      추측하지 않고 ``−Row`` 를 버킷 키로만 쓴다(음수 → 장비 값이 아님이 드러난다).
      ref/val 이 같은 규칙이라 ``(col,row) ±1`` 이웃 게이트는 정상 동작한다.
    · ``x/y`` 는 절대 좌표.  같은·이웃 die 안에서 **실제 물리 거리**로 재므로 die-내부
      비교보다 오히려 엄격하다(인접 die 의 같은 상대위치 결함이 거리 0 으로 보이던 문제가 없다).

    프레임이 다른 좌표(die-내부 x≈1만 vs 절대 x≈10만)와는 거리가 tol×3 을 한참 넘어
    **자동으로 기각**되므로 별도 가드가 필요 없다. 다만 그러면 그 슬롯이 통째로 '매치
    실패' 로 보이므로, 한 실행 안에서 프레임이 섞이는 건 :func:`..coords.resolve_batch`
    가 미리 막는다.
    """
    return {stem: abs_coord(*raw)
            for stem, raw, _xy, _recipe in entries if raw is not None}


def _extract_raw(content: str) -> Optional[tuple[float, float, int, int]]:
    """INI 섹션 내용 → 원시 ``(X, Y, Col, Row)``. 필수 키가 없으면 None."""
    return _raw_from_kv(_kv(content))


def _kv(content: str) -> dict[str, str]:
    """INI 섹션 내용 → ``{키(대문자) → 값}``.  같은 키는 뒤의 것이 이긴다."""
    kv: dict[str, str] = {}
    for key, value in _KEY_PAT.findall(content):
        kv[key.upper()] = value.strip()
    return kv


def _fget(kv: dict[str, str], key: str) -> Optional[float]:
    v = kv.get(key)
    try:
        f = float(v) if v is not None else None
    except ValueError:
        return None
    # nan/inf 는 숫자가 아니다 — 그대로 두면 floor/int 가 예외를 던지고 load_folder 의
    # 바깥 except 가 **폴더 전체**를 {} 로 만든다(좌표 전반 점검, 2026-10-24).  그 항목만 뺀다.
    if f is not None and not math.isfinite(f):
        _LOG.warning("Camtek INI 값 %s=%s 는 유한한 수가 아니다 — 그 항목만 건너뛴다", key, v)
        return None
    return f


def _xy_from_kv(kv: dict[str, str]) -> Optional[tuple[float, float]]:
    """X/Y(없으면 FaultX/FaultY).  하나라도 없으면 None."""
    X = _fget(kv, "X")
    if X is None:
        X = _fget(kv, "FAULTX")
    Y = _fget(kv, "Y")
    if Y is None:
        Y = _fget(kv, "FAULTY")
    if X is None or Y is None:
        return None
    return (X, Y)


def _raw_from_kv(kv: dict[str, str]) -> Optional[tuple[float, float, int, int]]:
    xy = _xy_from_kv(kv)
    Col = _fget(kv, "COL")
    Row = _fget(kv, "ROW")
    if xy is None or Col is None or Row is None:
        return None
    return (xy[0], xy[1], int(Col), int(Row))


# ── 폴더당 INI 1회 읽기 ──────────────────────────────────────────────────
# 결함이 수천 건이면 INI 가 수 MB 다.  예전에는 좌표·검산·레시피·절대좌표가 **각자**
# 파일을 다시 읽고 섹션 전체를 다시 파싱해, 사진 한 장을 조회해도 NAS 에서 같은 파일을
# 4번 읽었다(단일 사진 정보가 느렸던 원인).  여기서 한 번 읽어 섹션마다
# (stem, 원시 X/Y/Col/Row, 절대 X/Y, RecipeNumber) 를 뽑아 두고 네 맵이 나눠 쓴다.
# 캐시 키에 mtime·크기를 넣어 파일이 바뀌면 다시 읽는다.
_RECIPE_PAT = re.compile(r"(?im)^\s*RecipeNumber[ \t]*=[ \t]*(-?\d+)\s*$")


def _sections(folder: Path) -> tuple:
    ini = _find_ini(folder)
    if ini is None:
        return ()
    try:
        st = ini.stat()
    except OSError:
        return ()
    return _parse_sections(ini, st.st_mtime_ns, st.st_size)


@lru_cache(maxsize=32)
def _parse_sections(ini: Path, _mtime_ns: int, _size: int) -> tuple:
    text = read_ini_text(ini) or ""
    parts = _SECTION_PAT.split(text)
    out = []
    it = iter(parts[1:])
    for name, content in zip(it, it):
        kv = _kv(content)
        m = _RECIPE_PAT.search(content)
        out.append((Path(name.strip()).stem.lower(), _raw_from_kv(kv),
                    _xy_from_kv(kv), int(m.group(1)) if m else 0))
    return tuple(out)


@lru_cache(maxsize=256)
def load_raw_folder(folder: Path) -> dict[str, tuple[float, float, int, int]]:
    """폴더의 INI → ``{stem(소문자) → (X, Y, Col, Row)}`` 원시값.

    :func:`~.wafer_geometry.camtek_geometry` 가 읽은 die pitch 를 검산할 때 쓴다
    (``Col == floor(X/pitch_x)``).  변환 상수를 안 쓰므로 geometry 와 순환하지 않는다.
    """
    try:
        return {stem: raw for stem, raw, _xy, _recipe in _sections(folder)
                if raw is not None}
    except Exception:
        _LOG.warning("Camtek INI 원시값 파싱 실패: %s", folder, exc_info=True)
        return {}


@lru_cache(maxsize=256)
def load_recipe_folder(folder: Path) -> dict[str, int]:
    """폴더의 INI → ``{stem(소문자) → RecipeNumber}``.  키가 없으면 ``0``.

    :func:`~.wafer_geometry._grid_check` 가 검산을 **레시피별로 묶을 때** 쓴다 —
    실측상 레시피마다 ``Row`` 필드의 원점이 1 다를 수 있어, 한 폴더의 항목을
    통째로 묶으면 정상적인 pitch 도 거부된다.

    :func:`load_raw_folder` 의 4-튜플 계약을 안 건드리려고 별도 캐시로 둔다
    (``load_abs_folder`` 와 같은 관습)."""
    try:
        return {stem: recipe for stem, _raw, _xy, recipe in _sections(folder)}
    except Exception:
        _LOG.warning("Camtek INI RecipeNumber 파싱 실패: %s", folder, exc_info=True)
        return {}


def resolve(image_path: Path) -> Optional[DefectCoord]:
    """이미지 1장 → DefectCoord. INI 가 없거나 섹션이 없으면 None."""
    coords = load_folder(image_path.parent)
    return coords.get(image_path.stem.lower())


# ── 절대 좌표(원시 X/Y) — Surface.flt 매칭용 ──────────────────────────────
# 변환된 DefectCoord 와 달리, Surface.flt 의 ActualX/ActualY 와 직접 비교할
# **원시 절대 wafer 좌표** 를 그대로 보존한다.  기존 resolve/_parse_ini 경로는
# 손대지 않고, 같은 섹션 분리를 재사용해 X/Y(또는 FaultX/FaultY)만 뽑아 캐시한다.

@lru_cache(maxsize=256)
def load_abs_folder(folder: Path) -> dict[str, tuple[float, float]]:
    """폴더의 INI → {stem(소문자) → (절대 X, 절대 Y)}.  없으면 빈 dict."""
    try:
        return {stem: xy for stem, _raw, xy, _recipe in _sections(folder)
                if xy is not None}
    except Exception:
        _LOG.warning("Camtek INI 절대 좌표 파싱 실패: %s", folder, exc_info=True)
        return {}
