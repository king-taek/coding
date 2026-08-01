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


@dataclass
class Feature:
    """이미지 1장에서 추출된 모든 특징을 묶은 객체."""
    path: Path
    phash: np.ndarray                       # uint8 vector
    orb_kp: int
    orb_desc: Optional[np.ndarray]          # (N, 32) uint8 or None
    roi_gray: np.ndarray                    # SSIM 비교용 ROI
    orb_xy: Optional[np.ndarray] = None     # (N, 2) float32 ORB 키포인트 좌표 — 중앙가중용

    # 디스크 직렬화 ----------------------------------------------------------
    def save(self, dst: Path) -> None:
        payload: dict[str, np.ndarray] = {
            "phash": self.phash,
            "roi_gray": self.roi_gray,
            "orb_kp": np.array([self.orb_kp], dtype=np.int32),
        }
        if self.orb_desc is not None:
            payload["orb_desc"] = self.orb_desc
        if self.orb_xy is not None:
            payload["orb_xy"] = self.orb_xy
        dst.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(dst), **payload)

    @classmethod
    def load(cls, src: Path, path: Path) -> "Feature":
        # 옛 캐시에 남은 키(cnn·cnn_model)는 읽지 않으므로 그냥 무시된다 —
        # npz 는 요청한 항목만 꺼내므로 파일을 다시 만들 필요가 없다.
        data = np.load(str(src), allow_pickle=True)
        return cls(
            path=path,
            phash=data["phash"],
            roi_gray=data["roi_gray"],
            orb_kp=int(data["orb_kp"][0]),
            orb_desc=data["orb_desc"] if "orb_desc" in data.files else None,
            orb_xy=data["orb_xy"] if "orb_xy" in data.files else None,   # 구 캐시엔 없음→None
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract(src: Path, *, cfg=None,
            side=None, need_orb: bool = True) -> Feature:
    """디스크 캐시를 거쳐 한 이미지의 Feature 를 반환.

    ``cfg`` (SimilarityConfig) 전처리 토글이 켜져 있으면 강화/KLA 변환을
    계산 전용으로 적용하고, 캐시 키에 ``cfg.cache_extra(side)`` 를 섞어 기본
    특징과 분리 저장한다.  ``side`` ('ref'/'val') 는 중앙 30% crop 의 side 별
    적용을 위해 전달.  cfg=None / 토글 OFF → 현행과 동일 (extra="").

    ``need_orb=False`` 면 ORB(키포인트 검출/디스크립터) 계산을 생략한다 — 고전
    재채점에서 ORB 를 안 쓰는 고속 변형(개발자 벤치마크)이 추출 비용을 줄이려고
    사용한다.  부분 특징이 캐시에 섞이지 않도록 이 경우 캐시 읽기/쓰기를 끈다.
    """
    extra = cfg.cache_extra(side) if cfg is not None else ""
    cache_file = cache.cache_path(src, "feature", extra=extra)
    path = Path(src)
    no_cache = not need_orb          # 부분(ORB 생략) 특징은 캐시와 분리.
    if not no_cache and cache_file.exists() and cache_file.stat().st_size > 0:
        try:
            return Feature.load(cache_file, path)
        except Exception:
            # corrupted cache → recompute
            try:
                cache_file.unlink()
            except OSError:
                pass

    roi_gray = image_io.center_roi_gray(path, cfg=cfg, side=side)
    # CLAHE + 가벼운 블러 (cv2 사용)
    roi_gray = _preprocess(roi_gray)

    ph = _phash.compute_phash(roi_gray)
    orb_nf = int(getattr(cfg, "orb_nfeatures", 0) or 0) if cfg is not None else 0
    od = (_orb.compute_orb(roi_gray, nfeatures=orb_nf) if need_orb
          else _orb.OrbDescriptor(0, None))

    feat = Feature(
        path=path,
        phash=ph,
        orb_kp=od.keypoints,
        orb_desc=od.descriptors,
        roi_gray=roi_gray,
        orb_xy=od.coords,                # 중앙-가중 채점용 키포인트 좌표
    )
    try:
        if not no_cache:
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
          weights: Optional[config.SimilarityWeights] = None,
          *, components: Optional[set] = None, center_strength: float = 0.0) -> float:
    """두 Feature 사이의 최종 가중 평균 유사도 (0.0 ~ 1.0).

    ``components`` (예: ``{"phash","ssim"}``) 가 주어지면 그 항들만 사용하고 가중치를
    그 부분집합으로 재정규화한다.  비싼 ORB(디스크립터 정합)·SSIM 을 빼서 CPU 재채점
    속도를 올리는 고속 변형(개발자 벤치마크)이 사용한다.  None=현행(전체)."""
    base = (weights or config.CONFIG.similarity)
    w = base.normalized()

    use = components if components is not None else {"phash", "orb", "ssim"}

    s_phash = _phash.phash_similarity(a.phash, b.phash) if "phash" in use else 0.0

    if "orb" in use:
        orb_a = _orb.OrbDescriptor(a.orb_kp, a.orb_desc, getattr(a, "orb_xy", None),
                                   tuple(a.roi_gray.shape[:2]))
        orb_b = _orb.OrbDescriptor(b.orb_kp, b.orb_desc, getattr(b, "orb_xy", None),
                                   tuple(b.roi_gray.shape[:2]))
        s_orb = _orb.orb_score(orb_a, orb_b, center_strength=center_strength)
    else:
        s_orb = 0.0

    s_ssim = _ssim.ssim_score(a.roi_gray, b.roi_gray) if "ssim" in use else 0.0

    # 사용 컴포넌트의 가중치만 모아 재정규화 — 부분집합도 [0,1] 스케일을 유지해
    # 임계치/융합이 그대로 동작하게 한다(전체일 때는 현행과 동일).
    parts = []
    if "phash" in use:
        parts.append((w.phash, s_phash))
    if "orb" in use:
        parts.append((w.orb, s_orb))
    if "ssim" in use:
        parts.append((w.ssim, s_ssim))
    wsum = sum(wt for wt, _ in parts)
    if wsum <= 0:
        return 0.0
    total = sum(wt * sv for wt, sv in parts) / wsum
    return max(0.0, min(1.0, float(total)))

