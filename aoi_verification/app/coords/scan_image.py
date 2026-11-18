"""Color 결함 사진 → 같은 웨이퍼 폴더의 Scan(``.t.``) 사진에서 결함 중심 300㎛ Crop.

단일 사진 정보 화면과 결과 엑셀이 **같은 함수**로 계산한다(두 벌로 갈라지면 화면과
엑셀의 Crop 이 달라진다).

좌표 규칙 (실측 4웨이퍼 11쌍이 사용자 확인으로 일치 — 평균 클릭 오차 2.3px)::

    u = W/2 + (결함X − Scan중심X) / pixel_x        # 원본 JPEG, 왼쪽 위가 (0, 0)
    v = H/2 + (결함Y − Scan중심Y) / pixel_y

- 결함 X/Y: ``ColorImageGrabingInfo.ini`` 의 원시 X/Y(FaultX/FaultY), 없으면 점표기
  파일명의 앞 두 토큰.  die 좌표·KLA 좌표는 쓰지 않는다.
- Scan 중심: 같은 폴더 ``ScanResultImageList.txt`` (Version=1) 의 [파일명, X, Y].
- pixel: Scan 마다 그 **레시피의** 값(:func:`pixel_size.scan_pixel_size_xy_recipe`, 파일명
  ``….t.N.jpeg`` 의 N — 다중 레시피는 배율이 달라 픽셀 크기가 4배 다를 수 있다).  **못 읽으면 실패**다 — 다른 기능이
  쓰는 0.77 기본값을 여기에 흘리지 않는다(배율이 틀리면 엉뚱한 곳을 자른다).
- W/H 는 **실제 JPEG 크기**를 읽는다.  3168×1024 를 상수로 두지 않고, 크기가
  다르다고 2배 같은 보정을 추측하지도 않는다.  축 반전·교환·회전 없음.
- EXIF 회전을 적용하지 않은 raw JPEG 좌표다(검증이 그 규약이었다).  그래서 디코드도
  ``image_io``(EXIF 자동 회전)를 거치지 않는다.

'Scan 있음' 판정(:func:`resolve` 의 ``status == "ok"``)은 목록 파일이 있고, 그 목록의
Scan 중 결함 좌표를 원본 범위 안에 담은 것이 하나라도 있을 때다(사용자 결정).
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

from . import abs_coord, camtek_ini, pixel_size
from .ini_text import decode_ini_bytes

__all__ = ["SCAN_LIST_NAME", "CROP_SIDE_UM", "Candidate", "ScanMatch",
           "parse_manifest", "scan_uv", "crop_box", "rank_candidates", "resolve", "resolve_xy", "resolve_xy_many",
           "cut", "load_crop", "crops_from", "load_full", "clear_caches"]

_LOG = logging.getLogger("aoi.scan")

SCAN_LIST_NAME = "ScanResultImageList.txt"
CROP_SIDE_UM = 300.0
# 원본 밖으로 나간 Crop 영역을 채우는 고정 중성 회색.
PAD_GRAY = 128
_SUPPORTED_VERSION = "1"

# 상태 — 화면·엑셀이 문구를 고른다.
OK = "ok"
NO_MANIFEST = "no_manifest"
UNSUPPORTED_VERSION = "unsupported_version"
NO_COORDINATES = "no_coordinates"
NO_PIXEL_SIZE = "no_pixel_size"
NO_CANDIDATE = "no_candidate"
UNREADABLE = "unreadable_scan"


# ---------------------------------------------------------------------------
# 순수 계산
# ---------------------------------------------------------------------------
def _safe_name(name: str) -> bool:
    """목록의 파일명이 같은 폴더 안의 파일 이름인가(경로 이동·절대경로 거부)."""
    if not name or name in (".", ".."):
        return False
    if any(c in name for c in ("/", "\\", ":", "\0")):
        return False
    return not name.startswith("..")


def parse_manifest(text: str) -> tuple[str, list[tuple[str, float, float]]]:
    """``ScanResultImageList.txt`` 본문 → (상태, [(파일명, 중심X, 중심Y)]).

    첫 의미 있는 줄이 ``Version=1`` 이어야 한다.  깨진 행·NaN·위험한 이름은 그 행만
    뺀다.  같은 이름이 같은 값으로 두 번 나오면 하나로, **다른 값이면 그 이름을 통째로
    뺀다**(어느 쪽이 맞는지 모르니 조용히 덮어쓰지 않는다)."""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines:
        return UNSUPPORTED_VERSION, []
    key, _, val = lines[0].partition("=")
    if key.strip().lower() != "version" or val.strip() != _SUPPORTED_VERSION:
        return UNSUPPORTED_VERSION, []
    seen: dict[str, tuple[str, float, float]] = {}
    conflict: set[str] = set()
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) != 3:
            continue
        name = parts[0].strip('"')
        try:
            x, y = float(parts[1]), float(parts[2])
        except ValueError:
            continue
        if not (math.isfinite(x) and math.isfinite(y)) or not _safe_name(name):
            continue
        k = name.casefold()
        if k in seen and seen[k][1:] != (x, y):
            conflict.add(k)
        seen.setdefault(k, (name, x, y))
    for k in conflict:
        _LOG.warning("Scan 목록: %s 의 좌표가 서로 달라 제외", seen[k][0])
    return OK, [v for k, v in seen.items() if k not in conflict]


def scan_uv(defect_xy, center_xy, size_wh, pixel_xy) -> tuple[float, float]:
    """결함 절대좌표 → Scan 원본 픽셀 (u, v).  반올림하지 않는다."""
    w, h = size_wh
    return (w / 2.0 + (defect_xy[0] - center_xy[0]) / pixel_xy[0],
            h / 2.0 + (defect_xy[1] - center_xy[1]) / pixel_xy[1])


def crop_box(uv, pixel_xy, side_um: float = CROP_SIDE_UM) -> tuple[int, int, int, int]:
    """(u, v) 중심 ``side_um`` 정사각 범위 → 정수 (left, top, w, h).

    폭·높이는 축별 픽셀 크기로 따로 구한다(X≠Y 면 픽셀 수가 달라도 물리 범위는 같다).
    중심 오차는 반올림으로 최대 약 1px.  가장자리에서 안쪽으로 밀지 않는다."""
    cw = max(1, round(side_um / pixel_xy[0]))
    ch = max(1, round(side_um / pixel_xy[1]))
    return (math.floor(uv[0] - cw / 2.0 + 0.5), math.floor(uv[1] - ch / 2.0 + 0.5),
            cw, ch)


def _coverage(box, size_wh) -> float:
    left, top, cw, ch = box
    ix = max(0, min(left + cw, size_wh[0]) - max(left, 0))
    iy = max(0, min(top + ch, size_wh[1]) - max(top, 0))
    return (ix * iy) / float(cw * ch)


@dataclass(frozen=True)
class Candidate:
    path: Path
    center_xy: tuple[float, float]
    size_wh: tuple[int, int]
    uv: tuple[float, float]
    box: tuple[int, int, int, int]
    coverage: float


def rank_candidates(defect_xy, entries, pixel_xy,
                    side_um: float = CROP_SIDE_UM) -> list[Candidate]:
    """결함을 원본 안에 담은 Scan 들을 Crop 에 알맞은 순서로.

    ``entries`` = [(경로, 중심XY, (W, H))] 또는 [(…, 픽셀XY)] — 넷째 값이 있으면 그 Scan 은
    ``pixel_xy`` 대신 그 값으로 계산한다(레시피별 픽셀 크기).  규칙: coverage(요청 Crop 중 원본과 겹치는
    비율) 내림차순 → 정규화 중심 거리 오름차순 → 파일명(casefold, 원래 이름) 순.
    manifest 중심이 가장 가까운 것을 고르지 않는다 — Scan 은 가로·세로 범위가 달라
    가까운 중심이 결함을 더 잘 담는다는 보장이 없다."""
    out: list[tuple[tuple, Candidate]] = []
    for path, center, (w, h), *own in entries:
        px = own[0] if own else pixel_xy
        if w <= 0 or h <= 0 or px is None:
            continue
        u, v = scan_uv(defect_xy, center, (w, h), px)
        if not (0.0 <= u < w and 0.0 <= v < h):
            continue
        box = crop_box((u, v), px, side_um)
        cov = _coverage(box, (w, h))
        dist = ((u - w / 2) / (w / 2)) ** 2 + ((v - h / 2) / (h / 2)) ** 2
        name = Path(path).name
        out.append(((-cov, dist, name.casefold(), name),
                    Candidate(Path(path), center, (w, h), (u, v), box, cov)))
    out.sort(key=lambda t: t[0])
    return [c for _k, c in out]


# ---------------------------------------------------------------------------
# 결과
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ScanMatch:
    """한 Color 사진의 Scan 연결 결과.  ``status != "ok"`` 면 candidates 는 비어 있다."""
    status: str
    color_path: Path
    defect_xy: Optional[tuple[float, float]] = None
    pixel_xy: Optional[tuple[float, float]] = None
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def best(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None


# ---------------------------------------------------------------------------
# 폴더 단위 읽기 (캐시 키에 mtime 을 넣어 파일이 바뀌면 다시 읽는다)
# ---------------------------------------------------------------------------
def _mtime(p: Path) -> int:
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return -1


# Scan 머리(W, H)를 동시에 몇 개까지 읽을까 — NAS 는 한 번 열 때마다 왕복 지연이 있어
# 수백 장을 차례로 열면 수 초가 걸린다.  지연이 대부분이라 나눠 열면 그만큼 줄어든다.
_SIZE_WORKERS = 48
_index_lock = threading.Lock()


def _index(folder: str, list_mtime: int, dir_mtime: int):
    """(상태, [(실제 경로, 중심XY, (W, H))]) — 폴더당 한 번만 만든다.

    목록 + 폴더 열거 한 번(대소문자 매핑) + Scan 머리 읽기를 **폴더 단위로** 캐시한다.
    예전에는 사진을 열 때마다 목록의 Scan 전부를 하나씩 stat 해(NAS 왕복 × 수백) 캐시가
    있어도 매번 1초 가까이 걸렸다.  폴더나 목록 파일이 바뀌면(mtime) 다시 만든다.
    엑셀 저장은 여러 스레드가 같은 폴더를 동시에 처음 부르므로, 잠가서 한 번만 만든다."""
    with _index_lock:
        return _index_cached(folder, list_mtime, dir_mtime)


@lru_cache(maxsize=64)
def _index_cached(folder: str, list_mtime: int, dir_mtime: int):
    if list_mtime < 0:
        return NO_MANIFEST, ()
    try:
        text = decode_ini_bytes((Path(folder) / SCAN_LIST_NAME).read_bytes())
    except OSError:
        return NO_MANIFEST, ()
    status, rows = parse_manifest(text)
    if status != OK:
        return status, ()
    try:
        with os.scandir(folder) as it:
            real = {e.name.casefold(): e.path for e in it}
    except OSError:
        return NO_MANIFEST, ()
    found = []
    for name, x, y in rows:
        p = real.get(name.casefold())
        if p is not None:
            found.append((Path(p), (x, y)))
    if not found:
        return OK, ()
    with ThreadPoolExecutor(max_workers=min(_SIZE_WORKERS, len(found))) as pool:
        sizes = list(pool.map(lambda r: _image_size(str(r[0])), found))
    return OK, tuple((p, c, wh) for (p, c), wh in zip(found, sizes) if wh is not None)


@lru_cache(maxsize=64)
def _abs_map(folder: str, ini_mtime: int) -> dict:
    return camtek_ini.load_abs_folder(Path(folder)) if ini_mtime >= 0 else {}


def _image_size(path: str) -> Optional[tuple[int, int]]:
    """JPEG 머리만 읽어 (W, H).  전체 디코드하지 않는다."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        _LOG.warning("Scan 이미지 머리를 읽지 못해 후보에서 뺍니다: %s", path, exc_info=True)
        return None


def _defect_xy(color: Path) -> Optional[tuple[float, float]]:
    folder = color.parent
    ini = camtek_ini._find_ini(folder)
    xy = _abs_map(str(folder), _mtime(ini) if ini else -1).get(color.stem.lower())
    return xy if xy is not None else abs_coord.dotted_xy(color.name)


def resolve(color_path, side_um: float = CROP_SIDE_UM) -> ScanMatch:
    """Color 사진 → :class:`ScanMatch`.  파일 I/O 가 있으니 UI 스레드에서 부르지 않는다.

    예외를 밖으로 내지 않는다 — Scan 실패가 화면·엑셀의 기존 기능을 막으면 안 된다."""
    color = Path(color_path)
    return _resolve(color.parent, color, lambda: _defect_xy(color), side_um)


def resolve_xy(folder, xy, side_um: float = CROP_SIDE_UM) -> ScanMatch:
    """Color 사진이 없는 결함 — 웨이퍼 폴더와 결함 좌표(µm, ``ColorImageGrabingInfo.ini`` X/Y 와 같은 틀)로
    :class:`ScanMatch`.  Overkill 튜닝이 Surface.flt Defect(ActualX/Y)에 쓴다.  ``color_path`` 자리에는 폴더."""
    folder = Path(folder)
    return _resolve(folder, folder, lambda: xy, side_um)


def resolve_xy_many(folder, xys, side_um: float = CROP_SIDE_UM) -> list:
    """:func:`resolve_xy` 를 한 폴더의 결함 여럿에 — 목록·폴더 mtime stat 과 픽셀 크기 조회를 **한 번만** 한다.
    결과는 ``xys`` 와 같은 순서·같은 값(하나씩 부른 것과 같다).  NAS 에서는 결함마다 stat 2번이 왕복이라
    120개만 해도 1초 넘게 걸렸다(측정)."""
    folder = Path(folder)
    prep = _prepare(folder, folder)
    return [_rank(prep, folder, folder, lambda xy=xy: xy, side_um) for xy in xys]


def _resolve(folder: Path, color: Path, get_xy, side_um: float) -> ScanMatch:
    return _rank(_prepare(folder, color), folder, color, get_xy, side_um)


def tile_recipe(name: str) -> Optional[int]:
    """Scan 파일명 ``X.Y.t.N.jpeg`` 의 레시피 번호 N.  ``X.Y.t.jpeg``(단일 레시피)면 None."""
    toks = Path(name).stem.split(".")
    if len(toks) >= 2 and toks[-2].lower() == "t" and toks[-1].isdigit():
        return int(toks[-1])
    return None


def _with_recipe_px(folder: Path, rows) -> tuple:
    """행마다 그 Scan 레시피의 픽셀 크기를 붙인다 — 못 읽은 레시피의 행은 None(후보에서 빠진다)."""
    cache: dict = {}
    out = []
    for path, center, wh in rows:
        n = tile_recipe(Path(path).name)
        if n not in cache:
            cache[n] = pixel_size.scan_pixel_size_xy_recipe(folder, n)
        out.append((path, center, wh, cache[n]))
    return tuple(out)


def _prepare(folder: Path, color: Path):
    """폴더 단위 준비물 — ``(상태, 행들(+레시피별 픽셀 크기), 폴더 픽셀 크기)``.  못 읽으면 상태에 이유가 있다."""
    try:
        status, rows = _index(str(folder), _mtime(folder / SCAN_LIST_NAME),
                              _mtime(folder))
        if status != OK:
            return status, rows, None, None
        rows = _with_recipe_px(folder, rows)
        base = pixel_size.scan_pixel_size_xy(folder)
        return status, rows, (base or next((r[3] for r in rows if r[3]), None)), None
    except Exception as e:
        _LOG.exception("Scan 연결 실패: %s", color)
        return UNREADABLE, (), None, e


def _rank(prep, folder: Path, color: Path, get_xy, side_um: float) -> ScanMatch:
    status, rows, px, err = prep
    try:
        if err is not None:
            return ScanMatch(UNREADABLE, color)
        if status != OK:
            return ScanMatch(status, color)
        xy = get_xy()
        if xy is None:
            return ScanMatch(NO_COORDINATES, color)
        if px is None:
            return ScanMatch(NO_PIXEL_SIZE, color, xy)
        cands = rank_candidates(xy, rows, px, side_um)
        if not cands:
            return ScanMatch(NO_CANDIDATE, color, xy, px)
        return ScanMatch(OK, color, xy, px, tuple(cands))
    except Exception:
        # 예상 못 한 예외는 '후보 없음' 이 아니라 '읽다가 실패' 다 — 화면·엑셀이 구분해 알린다.
        _LOG.exception("Scan 연결 실패: %s", color)
        return ScanMatch(UNREADABLE, color)


# ---------------------------------------------------------------------------
# Crop
# ---------------------------------------------------------------------------
# 최근 디코드한 원본만 조금 들고 있는다(같은 Scan 에 결함이 여럿 있는 경우가 흔하다).
_DECODED_MAX_ITEMS = 4
_DECODED_MAX_BYTES = 64 * 1024 * 1024
_decoded: "OrderedDict[tuple, object]" = OrderedDict()
_decoded_lock = threading.Lock()


def _decode(path: Path):
    from PIL import Image
    key = (str(path), _mtime(path))
    with _decoded_lock:
        im = _decoded.get(key)
        if im is not None:
            _decoded.move_to_end(key)
            return im
    with Image.open(path) as src:
        im = src.copy()          # EXIF 회전을 적용하지 않는다(raw 규약)
    im.load()
    with _decoded_lock:
        _decoded[key] = im
        while (len(_decoded) > _DECODED_MAX_ITEMS
               or sum(len(v.mode) * v.width * v.height for v in _decoded.values())
               > _DECODED_MAX_BYTES) and len(_decoded) > 1:
            _decoded.popitem(last=False)
    return im


def cut(image, box):
    """원본에서 ``box`` 를 잘라낸다.  원본 밖은 :data:`PAD_GRAY` 로 채운다."""
    from PIL import Image
    left, top, cw, ch = box
    fill = PAD_GRAY if image.mode in ("L", "P") else (PAD_GRAY,) * len(image.mode)
    mode = "L" if image.mode == "P" else image.mode
    canvas = Image.new(mode, (cw, ch), fill)
    src = (max(left, 0), max(top, 0),
           min(left + cw, image.width), min(top + ch, image.height))
    if src[2] > src[0] and src[3] > src[1]:
        part = image.crop(src)
        if part.mode != mode:
            part = part.convert(mode)
        canvas.paste(part, (src[0] - left, src[1] - top))
    return canvas


def load_crop(match: ScanMatch):
    """``match`` 의 후보를 순서대로 디코드해 첫 성공의 (Crop, Candidate).  전부 실패면 None."""
    for cand in match.candidates:
        try:
            return cut(_decode(cand.path), cand.box), cand
        except Exception:
            _LOG.warning("Scan 원본을 읽지 못함: %s", cand.path)
    return None


_SENTINEL = 1          # 맨 아래 줄이 통째로 이 값이면 '거기까지 안 풀렸다' 로 보고 전체 디코드로 돌아간다


def _decode_top(path, bottom: int):
    """JPEG 위쪽 ``bottom`` 줄만 푼 이미지(나머지 줄은 쓰지 않는다) — 못 하면 None(호출부가 전체 디코드).

    JPEG 는 위에서부터 한 줄씩 풀리므로 Crop 아래쪽은 풀 필요도, 읽을 필요도 없다(측정: 위 200줄 3 ms ·
    전체 15 ms, NAS 에서는 읽는 바이트도 같은 비율로 준다).  **푼 줄은 전체 디코드와 같은 값이다**(테스트로 확인).
    일부러 일찍 멈추면 디코더가 '깨짐' 코드를 돌려줘 진짜 깨진 파일과 구분이 안 되므로, 버퍼를 한 값으로 채워 두고
    맨 아래 줄이 그대로면(= 거기까지 못 풀었다) None 을 돌려 전체 디코드가 판정하게 한다."""
    from PIL import Image
    with Image.open(path) as im:
        if (im.format != "JPEG" or im.mode not in ("L", "RGB") or len(im.tile) != 1
                or im.tile[0].codec_name != "jpeg" or bottom >= im.height):
            return None
        im.load_prepare()
        im.im = Image.core.fill(im.mode, im.size, _SENTINEL if im.mode == "L" else (_SENTINEL,) * 3)
        codec, _ext, offset, args = im.tile[0]
        dec = Image._getdecoder(im.mode, codec, args, im.decoderconfig)
        dec.setimage(im.im, (0, 0, im.width, bottom))
        im.fp.seek(offset)
        rest = b""
        while True:
            fresh = im.fp.read(65536)
            if not fresh and not rest:
                return None                     # 파일이 줄 수를 못 채우고 끝남 — 전체 디코드가 오류로 알린다
            rest += fresh
            n, _err = dec.decode(rest)
            if n < 0:
                break
            if n == 0 and not fresh:
                return None                     # 더 줄 데이터가 없는데 디코더도 못 나아감
            rest = rest[n:]                      # 디코더가 덜 쓴 뒷부분은 다음 덩어리 앞에 붙인다(ImageFile.load 와 같다)
        im.tile = []                             # crop() 이 다시 풀려 하지 않게
        ext = im.crop((0, bottom - 1, im.width, bottom)).getextrema()
        if all(b == (_SENTINEL, _SENTINEL) for b in (ext if im.mode == "RGB" else (ext,))):
            return None
        return im.copy()


def crops_from(path, boxes) -> Optional[list]:
    """한 Scan 원본을 **한 번만** 디코드해 ``boxes`` 를 전부 자른다(공유 디코드 캐시를 거치지 않는다 —
    스레드 여럿이 서로 다른 원본을 동시에 열어도 캐시를 밀어내지 않는다).  못 읽으면 None.

    Crop 이 닿는 맨 아래 줄까지만 푼다(:func:`_decode_top`) — 그것이 안 되면 전체 디코드."""
    from PIL import Image
    try:
        im = None
        if boxes:
            need = max(min(b[1] + b[3], 1 << 30) for b in boxes)
            if need >= 1:
                try:
                    im = _decode_top(path, need)
                except Exception:
                    im = None                    # 부분 디코드 실패 — 아래 전체 디코드가 같은 파일을 판정한다
        if im is None:
            with Image.open(path) as src:
                im = src.copy()  # EXIF 회전을 적용하지 않는다(raw 규약 — _decode 와 같다)
            im.load()
        return [cut(im, b) for b in boxes]
    except Exception:
        _LOG.warning("Scan 원본을 읽지 못함: %s", path)
        return None


def load_full(cand: Candidate):
    """전체 Scan 원본(raw).  못 읽으면 None."""
    try:
        return _decode(cand.path)
    except Exception:
        return None


def clear_caches() -> None:
    _index_cached.cache_clear()
    _abs_map.cache_clear()
    with _decoded_lock:
        _decoded.clear()
