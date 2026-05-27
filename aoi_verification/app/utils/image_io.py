"""이미지 입출력/리사이즈 헬퍼.

Pillow 기반으로 안전한 리사이즈를 수행하고, 캐시가 있으면 재사용한다.
모든 함수는 동기지만 _작업 스레드_ 에서 호출하는 것을 전제로 한다.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

from .. import config
from . import cache


# ---------------------------------------------------------------------------
# Pillow open helper — robust against truncated images
# ---------------------------------------------------------------------------
Image.MAX_IMAGE_PIXELS = None  # 매우 큰 AOI 이미지를 허용


def _open(src: Path, *, draft_long_edge: Optional[int] = None) -> Image.Image:
    """이미지 로드 + EXIF 회전 보정.

    ``draft_long_edge`` 가 주어지면 JPEG 일 때 libjpeg 의 빠른 다운스케일
    디코드를 사용한다 (전체 해상도로 디코드 후 리사이즈하는 것보다
    3~5× 빠름). 다른 포맷에는 영향이 없다.
    """
    img = Image.open(str(src))
    if draft_long_edge is not None:
        try:
            # libjpeg 가 1/2, 1/4, 1/8 스케일로 다운스케일 디코드.
            img.draft("RGB", (draft_long_edge, draft_long_edge))
        except Exception:
            pass
    img.load()
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    return img


def _fit_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= long_edge:
        return img.copy()
    if w >= h:
        new_w = long_edge
        new_h = int(round(h * long_edge / w))
    else:
        new_h = long_edge
        new_w = int(round(w * long_edge / h))
    return img.resize((new_w, new_h), Image.LANCZOS)


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGB",):
        return img
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


# ---------------------------------------------------------------------------
# Public — cached thumbnail / mid generation
# ---------------------------------------------------------------------------
def get_thumb_path(src: Path, *,
                   tier: Optional["config.SizingTier"] = None) -> Path:
    """썸네일 캐시 파일 경로를 보장 (없으면 생성).

    ``tier`` 가 주어지면 해당 화질 티어로 생성/캐시.  주어지지 않았고 세션
    티어 (``set_active_tier`` 로 등록) 가 있으면 그것을 사용 — 그래야 백그라
    운드 풀이 pre-warm 한 캐시와 UI 가 같은 파일을 가리킨다.  둘 다 없으면
    기본 200px/Q80 으로 생성.
    """
    if tier is None:
        tier = _active_tier
    if tier is None:
        return _ensure_resized(src, size_option="thumb",
                               long_edge=config.Sizing.THUMB_PX,
                               jpeg_q=config.Sizing.THUMB_JPEG_Q,
                               extra="")
    return _ensure_resized(src, size_option="thumb",
                           long_edge=int(tier.thumb_px),
                           jpeg_q=int(tier.thumb_q),
                           extra=f"t{tier.thumb_px}q{tier.thumb_q}")


def get_mid_path(src: Path, *,
                 tier: Optional["config.SizingTier"] = None) -> Path:
    """중간 이미지 캐시 파일 경로를 보장 (없으면 생성).

    ``tier`` 미지정 시 세션 티어 (``set_active_tier``) → 기본 800px/Q85 순으로
    fallback.  UI 와 백그라운드 풀이 같은 캐시 파일을 보도록 일치시킨다.
    """
    if tier is None:
        tier = _active_tier
    if tier is None:
        return _ensure_resized(src, size_option="mid",
                               long_edge=config.Sizing.MID_PX,
                               jpeg_q=config.Sizing.MID_JPEG_Q,
                               extra="")
    return _ensure_resized(src, size_option="mid",
                           long_edge=int(tier.mid_px),
                           jpeg_q=int(tier.mid_q),
                           extra=f"t{tier.mid_px}q{tier.mid_q}")


# ---------------------------------------------------------------------------
# 세션 단위 티어 — 스캔 직후 MainWindow 가 등록하면 그 이후의 무인자
# get_thumb_path / get_mid_path 가 같은 캐시 키를 사용 (백그라운드 pre-warm
# 과 UI 가 같은 파일을 보도록).  세션 종료 시 None 으로 클리어 권장.
# ---------------------------------------------------------------------------
_active_tier: Optional["config.SizingTier"] = None


def set_active_tier(tier: Optional["config.SizingTier"]) -> None:
    """세션 시작 시 활성 티어 등록 → 캐시 키 정합 보장."""
    global _active_tier
    _active_tier = tier


def get_active_tier() -> Optional["config.SizingTier"]:
    return _active_tier


def _ensure_resized(src: Path, *, size_option: str,
                    long_edge: int, jpeg_q: int,
                    extra: str = "") -> Path:
    out = cache.cache_path(src, size_option, extra=extra)  # type: ignore[arg-type]
    try:                                  # exists()+stat() 2회 → stat() 1회로
        if out.stat().st_size > 0:
            return out
    except OSError:
        pass
    try:
        # JPEG 빠른 디코드를 위해 draft 힌트를 함께 전달.
        img = _open(src, draft_long_edge=long_edge)
    except Exception as exc:  # pragma: no cover — handled by caller
        raise RuntimeError(f"이미지 로드 실패: {src} ({exc})") from exc
    img = _to_rgb(_fit_long_edge(img, long_edge))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out), format="JPEG", quality=jpeg_q, optimize=True)
    return out


def load_bytes(path: Path) -> bytes:
    return Path(path).read_bytes()


# ---------------------------------------------------------------------------
# Cropping for similarity ROI (centered) + numpy gray conversion
# ---------------------------------------------------------------------------
def center_roi_gray(src: Path,
                    roi_ratio: Optional[float] = None,
                    long_edge: Optional[int] = None,
                    cfg=None,
                    side=None) -> np.ndarray:
    """중심 ROI 를 잘라낸 후 그레이스케일 NumPy 배열로 돌려준다.

    유사도 파이프라인(pHash·SSIM·ORB) 모두가 공유하는 1차 전처리.

    ``cfg`` (SimilarityConfig) 가 주어지고 전처리 토글이 켜져 있으면 KLA/중앙
    변환을 **계산 전용**으로 적용 — 화면 표시 이미지는 영향 없음.  ``side``
    ('ref'/'val') 에 따라 중앙 30% crop 을 선택 적용한다.  cfg=None 또는 모든
    토글 OFF 면 현행과 동일 동작 (기본 모드 불변).
    """
    if roi_ratio is None:
        roi_ratio = config.Sizing.ROI_RATIO
    if long_edge is None:
        long_edge = config.Sizing.SIMILARITY_PX
    # 중앙 영역만 사용 옵션 (#7/#2) — side(ref/val) 에 적용.  비율은 30%.
    if cfg is not None and getattr(cfg, "_center_crop_for", None) is not None:
        if cfg._center_crop_for(side):
            roi_ratio = 0.3

    img = _open(src)
    img = _to_rgb(img)
    w, h = img.size
    rw = int(round(w * roi_ratio))
    rh = int(round(h * roi_ratio))
    x0 = (w - rw) // 2
    y0 = (h - rh) // 2
    img = img.crop((x0, y0, x0 + rw, y0 + rh))
    img = _fit_long_edge(img, long_edge)
    gray = np.asarray(img.convert("L"), dtype=np.uint8)
    return gray


def preprocessed_roi_gray(src: Path,
                          roi_ratio: Optional[float] = None,
                          long_edge: Optional[int] = None,
                          cfg=None,
                          side=None) -> np.ndarray:
    """CLAHE + Gaussian blur 까지 적용된 중심 ROI gray (CNN/유사도 공유).

    pHash·SSIM·ORB·CNN 모두가 같은 도메인 전처리 위에 동작하도록 일원화하기
    위해 만들어진 헬퍼. CV2 가 있으면 CLAHE 를 적용하고, 없으면 단순 ROI gray.
    """
    gray = center_roi_gray(src, roi_ratio=roi_ratio, long_edge=long_edge,
                           cfg=cfg, side=side)
    try:
        import cv2
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
    except Exception:
        pass
    return gray


def to_pil_thumb(src: Path) -> Image.Image:
    """캐시된 썸네일을 Pillow Image 로 즉시 로드."""
    return Image.open(str(get_thumb_path(src)))


def encode_jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    _to_rgb(img).save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Qt 픽스맵 공용 로더 — 위젯에서 매번 직접 작성하던 try/scale/fallback 패턴을 통합.
# Qt 가용 환경에서만 의미 있음. QPixmap 은 import 시점에서만 만들 수 있어 함수
# 내부에서 lazy import.
# ---------------------------------------------------------------------------
def load_thumb_qpixmap(path: "Path", size: int, *,
                       kind: str = "thumb"):
    """캐시된 썸네일/중간 이미지를 ``size`` x ``size`` 박스에 맞춰 스케일된
    ``QPixmap`` 으로 돌려준다. 캐시 미스/파일 오류 시 어두운 회색 fallback.

    ``kind`` 는 ``"thumb"`` 또는 ``"mid"``.  각각 ``get_thumb_path`` /
    ``get_mid_path`` 를 사용.

    GUI 스레드에서만 호출해야 한다 (QPixmap 은 main-thread only).
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPixmap

    fallback = QPixmap(size, size)
    fallback.fill(QColor(20, 28, 40))
    try:
        cache_path_fn = get_mid_path if kind == "mid" else get_thumb_path
        tp = cache_path_fn(Path(path))
        pix = QPixmap(str(tp))
        if pix.isNull():
            return fallback
        return pix.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return fallback
