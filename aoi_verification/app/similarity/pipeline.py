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
    cnn: Optional[np.ndarray] = None        # 옵션 (특정 모델의 임베딩)
    cnn_model: str = ""                     # cnn 을 만든 모델 이름 ("basic" 일 경우 빈문자열)

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
            if self.cnn_model:
                payload["cnn_model"] = np.array([self.cnn_model], dtype=object)
        dst.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(dst), **payload)

    @classmethod
    def load(cls, src: Path, path: Path) -> "Feature":
        data = np.load(str(src), allow_pickle=True)
        cnn_model = ""
        if "cnn_model" in data.files:
            try:
                cnn_model = str(data["cnn_model"][0])
            except Exception:
                cnn_model = ""
        return cls(
            path=path,
            phash=data["phash"],
            roi_gray=data["roi_gray"],
            orb_kp=int(data["orb_kp"][0]),
            orb_desc=data["orb_desc"] if "orb_desc" in data.files else None,
            cnn=data["cnn"] if "cnn" in data.files else None,
            cnn_model=cnn_model,
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
    """두 Feature 사이의 최종 가중 평균 유사도 (0.0 ~ 1.0).

    active 모델이 ``basic`` 이 아니고 torch 가 사용 가능하면 CNN 항이 자동으로
    활성화된다 (config 가 명시적으로 비활성화한 경우는 그대로 따름).
    """
    base = (weights or config.CONFIG.similarity)
    w = _resolve_weights(base).normalized()

    s_phash = _phash.phash_similarity(a.phash, b.phash)

    orb_a = _orb.OrbDescriptor(a.orb_kp, a.orb_desc)
    orb_b = _orb.OrbDescriptor(b.orb_kp, b.orb_desc)
    s_orb = _orb.orb_score(orb_a, orb_b)

    s_ssim = _ssim.ssim_score(a.roi_gray, b.roi_gray)

    s_cnn = 0.0
    if w.use_cnn:
        # 활성 모델과 캐시된 임베딩의 모델이 다르면 재계산 (차원/공간 충돌 방지).
        active = _active_model_name()
        a_emb = a.cnn if (a.cnn is not None and a.cnn_model == active) else _cnn.compute_embedding(a.path)
        b_emb = b.cnn if (b.cnn is not None and b.cnn_model == active) else _cnn.compute_embedding(b.path)
        # 즉시 재계산된 결과를 메모리상 Feature 에도 반영 (다음 비교에서 재사용).
        if a_emb is not None and (a.cnn is None or a.cnn_model != active):
            a.cnn = a_emb
            a.cnn_model = active
        if b_emb is not None and (b.cnn is None or b.cnn_model != active):
            b.cnn = b_emb
            b.cnn_model = active
        s_cnn = _cnn.cosine_similarity(a_emb, b_emb)

    total = (
        w.phash * s_phash
        + w.orb * s_orb
        + w.ssim * s_ssim
        + (w.cnn * s_cnn if w.use_cnn else 0.0)
    )
    return max(0.0, min(1.0, float(total)))


def _active_model_name() -> str:
    """현재 active 모델 이름 — 학습 패키지가 없거나 basic 이면 ``""``."""
    try:
        from ..learning import embedder as _emb
        m = _emb.get_active_mode()
        return "" if m == "basic" else m
    except Exception:
        return ""


def _resolve_weights(base: config.SimilarityWeights) -> config.SimilarityWeights:
    """active 모델이 학습 모델이면 use_cnn 을 자동 활성, basic 이면 비활성.

    가속기 (NPU/GPU) 가 있어도 basic 모드에서는 CNN 활성하지 않음 —
    이전 동작으로 롤백 (가중치 변경 없음).
    """
    from ..learning import embedder as _emb
    try:
        active = _emb.get_active_mode()
    except Exception:
        return base

    if base.use_cnn:
        return base

    if active != "basic" and _emb.is_available():
        return config.SimilarityWeights(
            phash=base.phash, orb=base.orb, ssim=base.ssim,
            cnn=base.cnn, use_cnn=True,
        )
    return base


def invalidate_cnn_cache(model_name: str = "") -> None:
    """디스크 feature 캐시의 .npz 들에서 cnn 필드만 제거(또는 파일 삭제).

    모델 학습/삭제/모드 전환 후 호출. 가장 단순한 구현은 npz 전체 삭제.
    """
    from ..utils import paths as _paths
    for npz in _paths.feature_cache_dir().glob("*.npz"):
        try:
            npz.unlink()
        except OSError:
            pass
