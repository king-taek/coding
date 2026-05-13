"""테마 색상·폰트·크기 및 유사도 가중치 같은 전역 설정 모음."""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Color palette (neon-dark sci-fi)
# ---------------------------------------------------------------------------
class Colors:
    BG = "#0A0E1A"
    BG_DEEP = "#000000"
    CARD = "#111827"
    CARD_ALT = "#0E1424"
    NEON_CYAN = "#00D4FF"
    NEON_BLUE = "#0099FF"
    NEON_MAGENTA = "#FF00AA"
    NEON_RED = "#FF2D55"
    NEON_GREEN = "#00FFA3"
    NEON_YELLOW = "#FFD600"
    DISABLED = "#2A2F3A"
    TEXT_PRIMARY = "#E5F4FF"
    TEXT_SECONDARY = "#7FB3D5"
    TEXT_MUTED = "#586378"
    BORDER = "#1F2A3F"


# ---------------------------------------------------------------------------
# Font stacks. The fallback list MUST include a Korean-capable font so 한글
# is rendered cleanly on every platform (특히 Windows).
# ---------------------------------------------------------------------------
class Fonts:
    TITLE = (
        '"Orbitron", "Pretendard", "Noto Sans KR", "Malgun Gothic", '
        '"Segoe UI", sans-serif'
    )
    BODY = (
        '"Rajdhani", "Pretendard", "Noto Sans KR", "Malgun Gothic", '
        '"Segoe UI", sans-serif'
    )
    MONO = '"JetBrains Mono", "Consolas", monospace'


# ---------------------------------------------------------------------------
# Image / thumbnail sizing
# ---------------------------------------------------------------------------
class Sizing:
    THUMB_PX = 200          # left/right/bottom grid thumbnails
    MID_PX = 800            # zoom-view + Excel embed
    SIMILARITY_PX = 384     # cropped ROI longest-edge for similarity
    ROI_RATIO = 0.55        # 중심 영역 비율 (0.5~0.6)
    THUMB_JPEG_Q = 80
    MID_JPEG_Q = 85


# ---------------------------------------------------------------------------
# Similarity pipeline weights — tunable from a YAML/JSON config later.
# ---------------------------------------------------------------------------
@dataclass
class SimilarityWeights:
    phash: float = 0.2
    orb: float = 0.3
    ssim: float = 0.2
    cnn: float = 0.3
    use_cnn: bool = False  # CNN 임베딩은 옵션 (torch 필요)

    def normalized(self) -> "SimilarityWeights":
        """If CNN is disabled, redistribute its weight to the others."""
        if self.use_cnn:
            return self
        total = self.phash + self.orb + self.ssim
        if total <= 0:
            return self
        return SimilarityWeights(
            phash=self.phash / total,
            orb=self.orb / total,
            ssim=self.ssim / total,
            cnn=0.0,
            use_cnn=False,
        )


# ---------------------------------------------------------------------------
# Defaults aggregated for convenience
# ---------------------------------------------------------------------------
@dataclass
class AppConfig:
    similarity: SimilarityWeights = field(default_factory=SimilarityWeights)
    # 교차 호기(다른 contrast/exposure) 데이터에서도 같은 슬롯 매칭이 잘 잡히도록
    # 0.55 로 보수적으로 설정. 같은 호기끼리는 보통 0.7 이상이라 같이 잡힘.
    default_threshold: float = 0.55       # 0.0 ~ 1.0
    autosave_interval_s: int = 30
    image_extensions: tuple[str, ...] = (".jpeg", ".jpg", ".png", ".bmp")
    max_thumbs_per_row: int = 4           # +N 시작 임계치 (4장 표시 → 5번째 +N)
    show_n_threshold: int = 5             # 5장 이상이면 +N 처리
    match_top_visible: int = 8            # Stage 2 우측 9장 이상이면 8 + +N

    def is_image(self, filename: str) -> bool:
        return filename.lower().endswith(self.image_extensions)


CONFIG = AppConfig()
