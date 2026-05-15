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
    # 좌/우/하 패널 그리드용 작은 썸네일. 측면 패널의 실제 폭(stretch 2:4:2
    # 기준 1280→~300px)에 2 col 그리드가 들어가도록 120 으로 잡는다. AOI
    # 결함 식별은 중앙 이미지(슬라이더 300~700px)로 하고, 측면은 ‘이미 결정
    # 한 사진들’ 의 시각적 참조 용도.
    THUMB_PX = 120
    MID_PX = 800            # zoom-view + Excel embed
    SIMILARITY_PX = 384     # cropped ROI longest-edge for similarity
    ROI_RATIO = 0.55        # 중심 영역 비율 (0.5~0.6)
    THUMB_JPEG_Q = 80
    MID_JPEG_Q = 85


@dataclass(frozen=True)
class SizingTier:
    """이미지 수에 따라 자동 선택되는 화질 단계.

    한 슬롯 폴더가 수백~수천 장인 경우 모든 사진을 200px/Q80 으로 만드는 데
    시간이 많이 걸린다. 다음 표처럼 단계를 두어 일정 수 이상이면 자동으로
    화질을 낮춰 처리 시간을 줄인다.

    +-----------------------+----------+--------+---------+--------+
    | 총 이미지(측당)       | 썸 px    | 썸 Q   | 중 px   | 중 Q   |
    +-----------------------+----------+--------+---------+--------+
    | ≤ 200                 | 200      | 80     | 800     | 85     |
    | 201–500               | 180      | 75     | 720     | 82     |
    | 501–1000              | 160      | 70     | 640     | 78     |
    | > 1000                | 140      | 65     | 560     | 75     |
    +-----------------------+----------+--------+---------+--------+
    """

    threshold: int            # 이 수 이하면 이 티어 선택 (오름차순으로 평가)
    thumb_px: int
    thumb_q: int
    mid_px: int
    mid_q: int


# 평가 순서: 적은 쪽부터. 마지막 티어의 threshold 는 충분히 큰 값.
SIZING_TIERS: tuple[SizingTier, ...] = (
    SizingTier(threshold=200,        thumb_px=200, thumb_q=80, mid_px=800, mid_q=85),
    SizingTier(threshold=500,        thumb_px=180, thumb_q=75, mid_px=720, mid_q=82),
    SizingTier(threshold=1000,       thumb_px=160, thumb_q=70, mid_px=640, mid_q=78),
    SizingTier(threshold=10 ** 9,    thumb_px=140, thumb_q=65, mid_px=560, mid_q=75),
)


def pick_tier(total_images: int, *, speed_mode: bool = False) -> SizingTier:
    """이미지 수(또는 사용자 강제 빠른 모드) 에 따라 티어를 선택."""
    if speed_mode:
        return SIZING_TIERS[-1]
    for tier in SIZING_TIERS:
        if total_images <= tier.threshold:
            return tier
    return SIZING_TIERS[-1]


# ---------------------------------------------------------------------------
# 메모리 / 캐시 한도
# ---------------------------------------------------------------------------
# in-memory LRU 픽스맵 캐시 기본 한도 — 512 MB.
PIXMAP_CACHE_MAX_BYTES = 512 * 1024 * 1024
# 메모리 압박 토스트 임계치 — 캐시 한도 + 1 GB 워킹셋.
MEMORY_PRESSURE_BYTES = PIXMAP_CACHE_MAX_BYTES + 1024 * 1024 * 1024


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
    max_thumbs_per_row: int = 8           # 8장까지 보여주고 9번째 자리에 +N
    show_n_threshold: int = 9             # 9장 이상이면 +N 처리 (그 미만은 전부 표시)
    match_top_visible: int = 8            # Stage 2 우측 9장 이상이면 8 + +N

    def is_image(self, filename: str) -> bool:
        return filename.lower().endswith(self.image_extensions)


CONFIG = AppConfig()
