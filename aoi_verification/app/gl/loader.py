"""사진 파일 → GL 2차원 배열(원본 비트심도 그대로).

- BMP·PNG·JPG·TIFF 는 OpenCV 로 읽는다(8/16 bit 회색은 그대로).  경로에 한글이 있어도
  되도록 ``np.fromfile`` → ``imdecode`` 로 읽는다.  OpenCV 가 못 읽는 것만 Pillow 로 다시 시도한다.
- 컬러 사진은 밝기(0.299R+0.587G+0.114B)로 바꾸되 ``color=True`` 로 알린다 — 화면이 Scan
  사진으로 갈아탈지 정한다.  R=G=B 인 '회색을 RGB 로 저장한' 사진은 컬러로 치지 않는다.
- RAW(.raw/.bin/.img)는 크기·비트·바이트 순서·헤더를 받아 읽는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RAW_EXTS = (".raw", ".bin", ".img")
HUGE_PIXELS = 100_000_000          # 이보다 크면 경고(느릴 수 있다)

# 경고 코드 — 문구는 화면(ko.py)이 정한다.
W_COLOR, W_JPEG, W_HUGE = "color", "jpeg", "huge"


class GlLoadError(Exception):
    """읽지 못했다 — 메시지는 사용자에게 그대로 보여도 되는 한국어다."""


@dataclass
class GlImage:
    data: np.ndarray                 # H×W, uint8 또는 uint16
    bits: int                        # 8 또는 16 (사용자가 10/12/14 로 바꿀 수 있다)
    color: bool = False
    path: str = ""
    warnings: list = field(default_factory=list)

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape


def is_raw_path(path) -> bool:
    return Path(path).suffix.lower() in RAW_EXTS


def _is_color(a: np.ndarray) -> bool:
    """세 채널이 실제로 다른가.  전체를 훑지 않고 성긴 표본으로 판정한다."""
    step = max(1, int(np.sqrt(a.shape[0] * a.shape[1] / 40_000)))
    s = a[::step, ::step, :3].astype(np.int32)
    return bool((np.abs(s[..., 0] - s[..., 1]).max() > 0)
                or (np.abs(s[..., 1] - s[..., 2]).max() > 0))


def _to_gray(a: np.ndarray) -> np.ndarray:
    """BGR(A) → 밝기.  원래 dtype 으로 되돌린다."""
    if a.ndim == 2:
        return a
    b, g, r = (a[..., i].astype(np.float32) for i in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return np.rint(y).astype(a.dtype)


def _decode_cv2(path: Path):
    import cv2
    buf = np.fromfile(str(path), dtype=np.uint8)
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)


def _decode_pil(path: Path):
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        if im.mode in ("I;16", "I;16L", "I;16B", "I"):
            a = np.array(im)
            return a.astype(np.uint16) if a.dtype != np.uint16 else a
        if im.mode in ("L", "P", "1"):
            return np.array(im.convert("L"))
        rgb = np.array(im.convert("RGB"))
        return rgb[..., ::-1].copy()          # → BGR (cv2 와 같은 순서)


def load_image(path) -> GlImage:
    """파일을 읽는다.  RAW 는 :func:`load_raw` 를 써야 한다(크기를 모른다)."""
    p = Path(path)
    if not p.is_file():
        raise GlLoadError("파일을 찾을 수 없습니다.")
    a = None
    try:
        a = _decode_cv2(p)
    except Exception:
        a = None
    if a is None:
        try:
            a = _decode_pil(p)
        except Exception as exc:
            raise GlLoadError(f"사진을 읽지 못했습니다: {exc}") from exc
    if a is None or a.size == 0 or a.ndim not in (2, 3):
        raise GlLoadError("사진을 읽지 못했습니다.")
    if a.dtype not in (np.uint8, np.uint16):
        # float·int32 등은 지원하지 않는다 — 조용히 잘라 쓰지 않고 알린다.
        raise GlLoadError(f"지원하지 않는 픽셀 형식입니다({a.dtype}).")
    warnings: list = []
    color = a.ndim == 3 and a.shape[2] >= 3 and _is_color(a)
    if a.ndim == 3:
        if a.shape[2] == 2:                 # 회색 + 알파
            a = a[..., 0]
        else:
            a = _to_gray(a)
    if color:
        warnings.append(W_COLOR)
    if p.suffix.lower() in (".jpg", ".jpeg"):
        warnings.append(W_JPEG)
    if a.shape[0] * a.shape[1] > HUGE_PIXELS:
        warnings.append(W_HUGE)
    return GlImage(np.ascontiguousarray(a), 16 if a.dtype == np.uint16 else 8,
                   color, str(p), warnings)


def guess_raw_side(nbytes: int, bits: int, header: int = 0) -> int:
    """파일 크기로 정사각형 한 변을 추정한다.  딱 맞지 않으면 0."""
    px = (nbytes - header) // (2 if bits > 8 else 1)
    s = int(round(px ** 0.5))
    return s if s * s == px else 0


def load_raw(path, width: int, height: int, bits: int = 16,
             little_endian: bool = True, header: int = 0) -> GlImage:
    """RAW 를 읽는다.  파일이 모자라면 오류로 알린다(남는 분량은 무시)."""
    if width <= 0 or height <= 0 or bits not in (8, 16) or header < 0:
        raise GlLoadError("RAW 설정이 올바르지 않습니다.")
    p = Path(path)
    bpp = 2 if bits == 16 else 1
    need = width * height * bpp
    try:
        size = os.path.getsize(p)
    except OSError as exc:
        raise GlLoadError("파일을 찾을 수 없습니다.") from exc
    if size - header < need:
        raise GlLoadError("파일이 설정한 크기보다 작습니다.")
    dt = np.dtype("<u2" if little_endian else ">u2") if bits == 16 else np.dtype("u1")
    a = np.fromfile(str(p), dtype=dt, count=width * height, offset=header)
    a = a.reshape(height, width).astype(np.uint16 if bits == 16 else np.uint8)
    return GlImage(np.ascontiguousarray(a), bits, False, str(p), [])


@dataclass
class ViewLoad:
    """뷰어가 사진 하나를 열 때의 결과 — 컬러 결함 사진이면 Scan 사진으로 갈아탄다."""
    image: GlImage
    scan_name: str = ""                       # 갈아탄 Scan 사진 이름(없으면 "")
    target_box: tuple | None = None           # Scan 안의 결함 300㎛ 상자 (left, top, w, h)
    scan_missing: bool = False                # 컬러인데 Scan 을 못 찾았다
    source_path: str = ""                     # 사용자가 연 원래 파일


def load_for_view(path) -> ViewLoad:
    """사진을 열되, **컬러 결함 사진이면 같은 폴더의 Scan 사진에서 결함 영역을 찾아** 그 Scan 을
    분석 대상으로 삼는다(사용자 요청: 컬러를 넣어도 Scan 의 해당 결함 영역을 자동으로 확대).

    Scan 을 못 찾으면 컬러 사진의 밝기로 분석하고 ``scan_missing`` 으로 알린다.  Scan 해석
    실패가 사진 열기를 막지 않는다(예외를 밖으로 내지 않는다)."""
    g = load_image(path)
    if not g.color:
        return ViewLoad(g, source_path=str(path))
    try:
        from ..coords import scan_image
        match = scan_image.resolve(path)
        if match.ok:
            for cand in match.candidates:
                full = scan_image.load_full(cand)
                if full is None:
                    continue
                gray = np.array(full.convert("L"))
                scan = GlImage(np.ascontiguousarray(gray), 8, False, str(cand.path), [])
                return ViewLoad(scan, cand.path.name, tuple(cand.box), False, str(path))
    except Exception:
        pass                                   # 아래에서 컬러 밝기로 되돌아간다
    return ViewLoad(g, scan_missing=True, source_path=str(path))
