"""강화 모드 / KLA crop 전처리 — **유사도 계산 전용** 변환 모음.

여기서 만든 변환은 pHash·ORB·SSIM·CNN 입력에만 적용되고, 화면에 보이는
썸네일/원본에는 절대 적용하지 않는다 (요청서 #10).  각 변환은 독립 토글이며
``SimilarityConfig`` 의 플래그로 on/off 한다.

- KLA crop  : KLA 사진 상/하단 텍스트 정보 영역을 잘라냄 (#7).
- 배경 제거 : OpenCV GrabCut 기본 (cv2 는 이미 의존), rembg 가 있으면 lazy 사용.
- 흑백+고감도: 강한 CLAHE + 히스토그램 스트레치.
- 고대비    : 선형 스트레치(상/하위 퍼센타일 클리핑).

image_io 가 이 모듈을 lazy import 한다 (순환 import 방지 — 이 모듈은
image_io 를 import 하지 않는다).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:                                   # pragma: no cover
    from PIL import Image as _PILImage
    from ..config import SimilarityConfig


# ---------------------------------------------------------------------------
# RGB 단계 변환 (중심 ROI crop 이전에 적용) — KLA crop, 배경 제거
# ---------------------------------------------------------------------------
def kla_crop_rgb(img: "_PILImage.Image", top: float, bottom: float) -> "_PILImage.Image":
    """이미지 상단 ``top``, 하단 ``bottom`` 비율을 잘라낸다 (KLA 정보영역 제거)."""
    top = max(0.0, min(0.45, float(top)))
    bottom = max(0.0, min(0.45, float(bottom)))
    if top <= 0 and bottom <= 0:
        return img
    w, h = img.size
    y0 = int(round(h * top))
    y1 = h - int(round(h * bottom))
    if y1 - y0 < 8:                                 # 너무 많이 자르면 무시
        return img
    return img.crop((0, y0, w, y1))


def remove_background_rgb(img: "_PILImage.Image") -> "_PILImage.Image":
    """배경을 제거(누끼)하고 배경 픽셀을 검정으로 채운 RGB 이미지 반환.

    rembg 가 설치돼 있으면 그것을 우선 사용(품질 우수), 없으면 OpenCV GrabCut
    로 폴백.  둘 다 실패하면 원본을 그대로 반환 (안전).
    """
    # 1) rembg (옵션) — lazy import.
    try:
        from rembg import remove as _rembg_remove   # type: ignore
        from PIL import Image as _Image
        cut = _rembg_remove(img)
        if cut.mode in ("RGBA", "LA"):
            bg = _Image.new("RGB", cut.size, (0, 0, 0))
            bg.paste(cut, mask=cut.split()[-1])
            return bg
        return cut.convert("RGB")
    except Exception:
        pass
    # 2) OpenCV GrabCut 폴백.
    try:
        import cv2
        from PIL import Image as _Image
        arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
        h, w = arr.shape[:2]
        if h < 16 or w < 16:
            return img
        mask = np.zeros((h, w), np.uint8)
        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        # 가장자리 6% 를 배경으로 가정한 사각형 init.
        mx, my = int(w * 0.06), int(h * 0.06)
        rect = (mx, my, max(1, w - 2 * mx), max(1, h - 2 * my))
        cv2.grabCut(arr, mask, rect, bgd, fgd, 3, cv2.GC_INIT_WITH_RECT)
        fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)
        out = arr * fg[:, :, None]
        return _Image.fromarray(out, mode="RGB")
    except Exception:
        return img


def apply_rgb_chain(img: "_PILImage.Image", cfg: "SimilarityConfig") -> "_PILImage.Image":
    """중심 ROI crop 이전 단계의 RGB 변환 적용 (KLA crop → 배경 제거)."""
    if cfg is None:
        return img
    if cfg.kla_crop:
        img = kla_crop_rgb(img, cfg.kla_top, cfg.kla_bottom)
    if cfg.bg_removal:
        img = remove_background_rgb(img)
    return img


# ---------------------------------------------------------------------------
# Gray 단계 변환 (grayscale 변환 이후) — 흑백+고감도, 고대비
# ---------------------------------------------------------------------------
def grayscale_highsens(gray: np.ndarray) -> np.ndarray:
    """강한 CLAHE + 풀 히스토그램 스트레치 — 미세 결함 대비를 키운다."""
    try:
        import cv2
        clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    except Exception:
        pass
    return _stretch(gray, 1.0, 99.0)


def high_contrast(gray: np.ndarray) -> np.ndarray:
    """상/하위 2 퍼센타일 클리핑 후 0~255 선형 스트레치."""
    return _stretch(gray, 2.0, 98.0)


def _stretch(gray: np.ndarray, lo_pct: float, hi_pct: float) -> np.ndarray:
    g = gray.astype(np.float32)
    lo = float(np.percentile(g, lo_pct))
    hi = float(np.percentile(g, hi_pct))
    if hi - lo < 1e-3:
        return gray
    g = (g - lo) * (255.0 / (hi - lo))
    return np.clip(g, 0, 255).astype(np.uint8)


def apply_gray_chain(gray: np.ndarray, cfg: "SimilarityConfig") -> np.ndarray:
    """grayscale 단계 변환 적용 (흑백+고감도 → 고대비)."""
    if cfg is None:
        return gray
    if cfg.grayscale:
        gray = grayscale_highsens(gray)
    if cfg.contrast:
        gray = high_contrast(gray)
    return gray
