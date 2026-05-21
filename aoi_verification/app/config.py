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
    # 좌/우/하 패널 그리드용 작은 썸네일.  실제 노출되는 타일은 120~240 px 사이
    # (BulkSelectDialog=180, UnmatchedReviewDialog ref=380 등) 라 캐시 thumb 가
    # 작으면 업스케일 시 흐릿해진다.  240 px / Q90 으로 한 단계 키워 화질 향상.
    THUMB_PX = 240
    MID_PX = 800            # zoom-view + Excel embed
    SIMILARITY_PX = 384     # cropped ROI longest-edge for similarity
    ROI_RATIO = 0.55        # 중심 영역 비율 (0.5~0.6)
    THUMB_JPEG_Q = 90
    MID_JPEG_Q = 88


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
    # 작은~중간 세션은 시각 품질을 우선해 한 단계 키운다 (썸네일 표시 크기와
    # 캐시 크기를 맞춰 업스케일 블러를 피함).
    SizingTier(threshold=200,        thumb_px=240, thumb_q=90, mid_px=800, mid_q=88),
    SizingTier(threshold=500,        thumb_px=200, thumb_q=85, mid_px=720, mid_q=85),
    # 대규모 세션은 처리 속도/메모리 우선이라 기존 값 유지.
    SizingTier(threshold=1000,       thumb_px=160, thumb_q=72, mid_px=640, mid_q=80),
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
# Similarity engine/preprocess config — 모든 유사도 경로에 단일 객체로 전달.
# engine=basic + 모든 토글 OFF = 현행과 byte 단위 동일 (기본 모드 불변).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SimilarityConfig:
    engine: str = "basic"          # "basic" | "fast"
    center20_ref: bool = False     # 기준 사진 중앙 30% 영역만 사용
    center20_val: bool = False     # 검증 사진 중앙 30% 영역만 사용
    grayscale: bool = False        # 강화: 흑백 + 고감도
    contrast: bool = False         # 강화: 고대비
    kla_crop: bool = False         # KLA 상/하단 정보영역 crop
    kla_top: float = 0.08          # 상단 잘라낼 비율
    kla_bottom: float = 0.08       # 하단 잘라낼 비율
    top_k: int = 50                # ANN 재정렬 깊이 (고속 모드)
    persist_scores: bool = False   # (ref,val) 점수 디스크 영속 캐시 (basic 엔진)

    def _center20_for(self, side) -> bool:
        """이 side(ref/val)에 중앙 영역 crop(30%)을 적용할지."""
        if side == "ref":
            return self.center20_ref
        if side == "val":
            return self.center20_val
        return False               # side 미지정 → crop 안 함 (캐시 키와 일관)

    @property
    def has_preprocess(self) -> bool:
        """전처리가 하나라도 켜져 있으면 True — 캐시 키 분기/적용 판단용."""
        return bool(self.center20_ref or self.center20_val or self.grayscale
                    or self.contrast or self.kla_crop)

    def cache_extra(self, side=None) -> str:
        """캐시 키 판별자.  전처리 OFF 면 빈 문자열 → 기본 캐시와 동일 키.

        중앙 20% crop 은 side(ref/val)에 따라 실제 적용 여부가 달라지므로
        side 별로 키를 분리한다 (교차검증에서 동일 파일이 ref/val 양쪽으로
        쓰일 때 캐시 충돌 방지)."""
        parts = []
        if self._center20_for(side):
            parts.append("c30")          # 중앙 30% — 키 변경으로 이전 캐시와 분리
        if self.grayscale:
            parts.append("g")
        if self.contrast:
            parts.append("ct")
        if self.kla_crop:
            parts.append(f"k{self.kla_top:.2f}-{self.kla_bottom:.2f}")
        return "-".join(parts)


# 기본 cfg 싱글턴 — engine=basic, 전처리 전부 OFF (현행 동작).
DEFAULT_SIM_CONFIG = SimilarityConfig()


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
