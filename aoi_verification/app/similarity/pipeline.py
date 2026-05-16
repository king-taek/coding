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


# 중앙 가중치 ROI 비율 + 합산 가중치.  사용자 요청: ‘중앙 20% 부분에 40% 가중치’.
# 결함이 사진 중심부에 잡혀 있는 AOI 데이터 특성상 중심부 일치도가 매치 판단에
# 결정적이라 추가 ROI 비교를 두고 점수에 합산한다.
CENTER_ROI_RATIO = 0.20
CENTER_WEIGHT = 0.40
FULL_WEIGHT = 1.0 - CENTER_WEIGHT


@dataclass
class Feature:
    """이미지 1장에서 추출된 모든 특징을 묶은 객체."""
    path: Path
    phash: np.ndarray                       # uint8 vector
    orb_kp: int
    orb_desc: Optional[np.ndarray]          # (N, 32) uint8 or None
    roi_gray: np.ndarray                    # SSIM 비교용 ROI (전체 ROI: 0.55)
    cnn: Optional[np.ndarray] = None        # 옵션 (특정 모델의 임베딩)
    cnn_model: str = ""                     # cnn 을 만든 모델 이름 ("basic" 일 경우 빈문자열)
    # ---- 중앙 20% ROI 의 보조 features (가중치 합산용) ----
    # 옛 캐시에는 None — score() 가 자동으로 full only fallback 한다.
    phash_c: Optional[np.ndarray] = None
    orb_kp_c: int = 0
    orb_desc_c: Optional[np.ndarray] = None
    roi_gray_c: Optional[np.ndarray] = None

    def has_center(self) -> bool:
        """중앙 ROI 보조 features 가 모두 채워졌는지."""
        return (self.roi_gray_c is not None
                and self.phash_c is not None
                and self.phash_c.size > 0)

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
        # 중앙 ROI 보조 features (있을 때만 저장).
        if self.phash_c is not None:
            payload["phash_c"] = self.phash_c
        if self.roi_gray_c is not None:
            payload["roi_gray_c"] = self.roi_gray_c
        if self.orb_desc_c is not None:
            payload["orb_desc_c"] = self.orb_desc_c
        payload["orb_kp_c"] = np.array([self.orb_kp_c], dtype=np.int32)
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
        # 중앙 ROI 가 없는 옛 캐시는 의도적으로 invalid 로 처리해서
        # extract() 가 recompute 하도록 만든다 — 사용자가 별도로 캐시를
        # 비우지 않아도 다음 검증부터 자동으로 중앙 가중치가 적용된다.
        if "phash_c" not in data.files or "roi_gray_c" not in data.files:
            raise ValueError("legacy feature cache without center ROI")
        return cls(
            path=path,
            phash=data["phash"],
            roi_gray=data["roi_gray"],
            orb_kp=int(data["orb_kp"][0]),
            orb_desc=data["orb_desc"] if "orb_desc" in data.files else None,
            cnn=data["cnn"] if "cnn" in data.files else None,
            cnn_model=cnn_model,
            phash_c=data["phash_c"],
            orb_kp_c=(int(data["orb_kp_c"][0])
                      if "orb_kp_c" in data.files else 0),
            orb_desc_c=(data["orb_desc_c"]
                        if "orb_desc_c" in data.files else None),
            roi_gray_c=data["roi_gray_c"],
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

    # 전체 ROI (config.Sizing.ROI_RATIO, 기본 0.55) — 기존과 동일.
    roi_gray = _preprocess(image_io.center_roi_gray(path))
    ph = _phash.compute_phash(roi_gray)
    od = _orb.compute_orb(roi_gray)

    # 중앙 20% 보조 ROI — 같은 전처리 (CLAHE + blur) 후 pHash/ORB 도 계산.
    roi_gray_c = _preprocess(
        image_io.center_roi_gray(path, roi_ratio=CENTER_ROI_RATIO)
    )
    ph_c = _phash.compute_phash(roi_gray_c)
    od_c = _orb.compute_orb(roi_gray_c)

    use_cnn = (config.CONFIG.similarity.use_cnn if use_cnn is None else use_cnn)
    cnn_vec = _cnn.compute_embedding(path) if (use_cnn and _cnn.is_available()) else None

    feat = Feature(
        path=path,
        phash=ph,
        orb_kp=od.keypoints,
        orb_desc=od.descriptors,
        roi_gray=roi_gray,
        cnn=cnn_vec,
        phash_c=ph_c,
        orb_kp_c=od_c.keypoints,
        orb_desc_c=od_c.descriptors,
        roi_gray_c=roi_gray_c,
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


def _weighted_avg(s_phash: float, s_orb: float, s_ssim: float,
                  s_cnn: float, w: config.SimilarityWeights) -> float:
    """한 ROI 의 pHash + ORB + SSIM (+ CNN) 가중 평균."""
    return (
        w.phash * s_phash
        + w.orb * s_orb
        + w.ssim * s_ssim
        + (w.cnn * s_cnn if w.use_cnn else 0.0)
    )


def score(a: Feature, b: Feature,
          weights: Optional[config.SimilarityWeights] = None) -> float:
    """두 Feature 사이의 최종 유사도 (0.0 ~ 1.0).

    전체 ROI 점수 (60%) + 중앙 20% ROI 점수 (40%) 의 가중 합 — 사용자 요청:
    AOI 결함이 사진 중심부에 있는 경우가 많으니 중앙 일치도에 더 무게.
    옛 캐시 (중앙 ROI features 없음) 는 자동으로 full only fallback.

    active 모델이 ``basic`` 이 아니고 torch 가 사용 가능하면 CNN 항이 자동
    활성화 (CNN 은 한 번만 — 글로벌 임베딩이라 두 ROI 모두에 동일 적용).
    """
    base = (weights or config.CONFIG.similarity)
    w = _resolve_weights(base).normalized()

    # ---- 전체 ROI (기본 55%) 점수 ----
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
        if a_emb is not None and (a.cnn is None or a.cnn_model != active):
            a.cnn = a_emb
            a.cnn_model = active
        if b_emb is not None and (b.cnn is None or b.cnn_model != active):
            b.cnn = b_emb
            b.cnn_model = active
        s_cnn = _cnn.cosine_similarity(a_emb, b_emb)

    full = _weighted_avg(s_phash, s_orb, s_ssim, s_cnn, w)

    # ---- 중앙 20% ROI 점수 (있을 때만 합산) ----
    if a.has_center() and b.has_center():
        s_phash_c = _phash.phash_similarity(a.phash_c, b.phash_c)
        orb_a_c = _orb.OrbDescriptor(a.orb_kp_c, a.orb_desc_c)
        orb_b_c = _orb.OrbDescriptor(b.orb_kp_c, b.orb_desc_c)
        s_orb_c = _orb.orb_score(orb_a_c, orb_b_c)
        s_ssim_c = _ssim.ssim_score(a.roi_gray_c, b.roi_gray_c)
        # CNN 점수는 글로벌 임베딩이라 중앙 ROI 도 동일 값 사용.
        center = _weighted_avg(s_phash_c, s_orb_c, s_ssim_c, s_cnn, w)
        total = FULL_WEIGHT * full + CENTER_WEIGHT * center
    else:
        total = full

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
    """active 모델이 학습 모델이면 use_cnn 을 자동 활성, basic 이면 비활성."""
    from ..learning import embedder as _emb
    try:
        active = _emb.get_active_mode()
    except Exception:
        return base

    # config 가 명시적으로 use_cnn=True 라면 사용자 의도를 우선 존중
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
