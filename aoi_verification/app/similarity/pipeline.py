"""특징 추출 + 가중 평균 점수 산출 파이프라인.

- 입력 이미지에 대해 한 번만 특징을 추출 (Feature) 한 뒤
- 두 Feature 객체 사이의 점수를 weighted average 로 계산한다.
- Feature 객체는 디스크 캐시에 저장되어 재실행 시 즉시 로드된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .. import config
from ..utils import cache, image_io
from . import phash as _phash
from . import orb as _orb
from . import ssim as _ssim
from . import cnn_embed as _cnn


@dataclass
class Feature:
    """이미지 1장에서 추출된 모든 특징을 묶은 객체."""
    path: Path
    phash: np.ndarray                       # uint8 vector
    orb_kp: int
    orb_desc: Optional[np.ndarray]          # (N, 32) uint8 or None
    roi_gray: np.ndarray                    # SSIM 비교용 ROI
    cnn: Optional[np.ndarray] = None        # 옵션

    # 디스크 직렬화 ----------------------------------------------------------
    def save(self, dst: Path) -> None:
        payload: dict[str, np.ndarray] = {
            "phash": self.phash,
            "roi_gray": self.roi_gray,
            "orb_kp": np.array([self.orb_kp], dtype=np.int32),
        }
        if self.orb_desc is not None:
            payload["orb_desc"] = self.orb_desc
        if self.cnn is not None:
            payload["cnn"] = self.cnn
        dst.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(dst), **payload)

    @classmethod
    def load(cls, src: Path, path: Path) -> "Feature":
        data = np.load(str(src))
        return cls(
            path=path,
            phash=data["phash"],
            roi_gray=data["roi_gray"],
            orb_kp=int(data["orb_kp"][0]),
            orb_desc=data["orb_desc"] if "orb_desc" in data.files else None,
            cnn=data["cnn"] if "cnn" in data.files else None,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract(src: Path, *, use_cnn: Optional[bool] = None) -> Feature:
    """디스크 캐시를 거쳐 한 이미지의 Feature 를 반환."""
    cache_file = cache.cache_path(src, "feature")
    path = Path(src)
    if cache_file.exists() and cache_file.stat().st_size > 0:
        try:
            return Feature.load(cache_file, path)
        except Exception:
            # corrupted cache → recompute
            try:
                cache_file.unlink()
            except OSError:
                pass

    roi_gray = image_io.center_roi_gray(path)
    # CLAHE + 가벼운 블러 (cv2 사용)
    roi_gray = _preprocess(roi_gray)

    ph = _phash.compute_phash(roi_gray)
    od = _orb.compute_orb(roi_gray)

    use_cnn = (config.CONFIG.similarity.use_cnn if use_cnn is None else use_cnn)
    cnn_vec = _cnn.compute_embedding(path) if (use_cnn and _cnn.is_available()) else None

    feat = Feature(
        path=path,
        phash=ph,
        orb_kp=od.keypoints,
        orb_desc=od.descriptors,
        roi_gray=roi_gray,
        cnn=cnn_vec,
    )
    try:
        feat.save(cache_file)
    except Exception:
        pass
    return feat


def _preprocess(gray: np.ndarray) -> np.ndarray:
    """CLAHE 로 호기 간 contrast 차이 보정 + 약한 Gaussian blur."""
    import cv2
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g = clahe.apply(gray)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return g


def score(a: Feature, b: Feature,
          weights: Optional[config.SimilarityWeights] = None) -> float:
    """두 Feature 사이의 최종 가중 평균 유사도 (0.0 ~ 1.0)."""
    w = (weights or config.CONFIG.similarity).normalized()

    s_phash = _phash.phash_similarity(a.phash, b.phash)

    orb_a = _orb.OrbDescriptor(a.orb_kp, a.orb_desc)
    orb_b = _orb.OrbDescriptor(b.orb_kp, b.orb_desc)
    s_orb = _orb.orb_score(orb_a, orb_b)

    s_ssim = _ssim.ssim_score(a.roi_gray, b.roi_gray)

    s_cnn = 0.0
    if w.use_cnn:
        s_cnn = _cnn.cosine_similarity(a.cnn, b.cnn)

    total = (
        w.phash * s_phash
        + w.orb * s_orb
        + w.ssim * s_ssim
        + (w.cnn * s_cnn if w.use_cnn else 0.0)
    )
    return max(0.0, min(1.0, float(total)))
