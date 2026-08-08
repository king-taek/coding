"""테마 색상·폰트·크기 및 유사도 가중치 같은 전역 설정 모음."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Font stacks. The fallback list MUST include a Korean-capable font so 한글
# is rendered cleanly on every platform (특히 Windows).
# ---------------------------------------------------------------------------
class Fonts:
    """UI 서체.

    ★ 1순위 ``NanumSquare`` 는 **저장소에 동봉**돼 있고(``ui/assets/font/``) 앱이
    시작할 때 Qt 에 등록한다(``ui/theme.load_app_fonts``).  OS 에 설치돼 있지
    않아도 모든 PC 에서 같은 글꼴로 보인다 — 뒤의 이름들은 등록이 실패했을 때를
    위한 폴백일 뿐이다.  이 문자열을 바꾸면 등록하는 파일 목록
    (``theme._FONT_FILES``)도 함께 맞춰야 한다.
    """

    #: 동봉 폰트의 패밀리명 — 등록/QSS/QFont 가 모두 이 이름을 본다.
    FAMILY = "NanumSquare"

    TITLE = (
        '"NanumSquare", "Pretendard", "Noto Sans KR", "Malgun Gothic", '
        '"Segoe UI", sans-serif'
    )
    BODY = (
        '"NanumSquare", "Pretendard", "Noto Sans KR", "Malgun Gothic", '
        '"Segoe UI", sans-serif'
    )
    MONO = '"JetBrains Mono", "Consolas", monospace'


# ---------------------------------------------------------------------------
# Image / thumbnail sizing
# ---------------------------------------------------------------------------
class Sizing:
    # 좌/우/하 패널 그리드용 작은 썸네일.  실제 노출되는 타일은 120~420 px 사이
    # (BulkSelectDialog=180, UnmatchedReviewDialog ref=420 등) 라 캐시 thumb 가
    # 작으면 업스케일 시 흐릿해진다.  240 px / Q90 으로 한 단계 키워 화질 향상.
    THUMB_PX = 240
    MID_PX = 800            # zoom-view + Excel embed
    SIMILARITY_PX = 384     # cropped ROI longest-edge for similarity
    ROI_RATIO = 0.55        # 중심 영역 비율 (0.5~0.6)
    THUMB_JPEG_Q = 90
    MID_JPEG_Q = 88
    # 다이얼로그/패널별 타일 기본 크기 — 흩어져 있던 매직 넘버를 한 곳에 모음(D2).
    # (값은 기존과 동일 — 동작 불변.  슬라이더가 있는 화면은 이를 시작값으로 사용.)
    SIDE_TILE_PX = 120      # Stage1 좌/우 사이드 패널 타일 (= THUMB_PX // 2)
    BULK_TILE_PX = 180      # '선택 모드' 다이얼로그 기본 타일
    REVIEW_THUMB_PX = 240   # 매칭 결과 검토 썸네일
    DIALOG_REF_PX = 420     # 미매칭 검토 좌측 기준 사진
    DIALOG_CAND_PX = 260    # 미매칭 검토 우측 후보 / 매치 확대 타일


# 좌표 기반 매칭(v2) 허용 오차 기본값 — µm.
#
# ★ **여기가 단일 출처다.**  예전에는 이 값이 리터럴 `500.0` 으로 20곳 넘게 흩어져 있었다
#   (진짜 기본값 3곳 + '값이 없을 때의 폴백' 12곳+).  그러면 기본값을 바꿔도 폴백 경로에서
#   옛 값이 조용히 되살아난다.  새 값이 필요하면 **이 상수만** 고친다.
#
# ★ 200 인 이유: die 한 변이 40 mm 안팎일 때 약 0.5 % 다.  더 크게 잡으면 이웃 결함과
#   짝지어질 위험이 커진다.  die 가 작은 자재는 화면에 표시되는 감지된 die 크기를 보고
#   더 낮춘다.
DEFAULT_COORD_TOLERANCE: float = 200.0


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

# 후보 선별(Stage 1): 측당 총 사진이 이 수 이상이면 좌/우 패널에 '현재 슬롯' 하나만
# 표시해 위젯 수를 최소화한다(#렉).  미만이면 모든 슬롯 표시.
SELECT_SINGLE_SLOT_THRESHOLD = 300
# 메모리 압박 토스트 임계치 — 캐시 한도 + 1 GB 워킹셋.
MEMORY_PRESSURE_BYTES = PIXMAP_CACHE_MAX_BYTES + 1024 * 1024 * 1024


# ---------------------------------------------------------------------------
# Similarity engine/preprocess config — 모든 유사도 경로에 단일 객체로 전달.
# engine=basic + 모든 토글 OFF = 현행과 byte 단위 동일 (기본 모드 불변).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SimilarityConfig:
    engine: str = "basic"          # "basic" | "efficiency"
    center_crop: bool = False      # 사진 중앙 30% 영역만 사용 (기준·검증 모두)
    persist_scores: bool = True    # (ref,val) 점수 디스크 영속 캐시 — 항상 기본 적용
    # 고효율 모드 동시 추론 수(in-flight).  높일수록 GPU 메모리·throughput↑
    # (계산 결과는 불변).  사용자 조절 노브.
    accel_concurrency: int = 32
    # 고효율 모드 장치 사용 토글(테스트용).  끄면 해당 유닛을 안 띄움 — 단,
    # 전부 꺼지면 CPU 로 폴백(유닛 0개 방지).
    use_cpu: bool = True
    use_gpu: bool = True
    # 정적 배치 B 재컴파일(테스트용).  1=끔(현행), >1=요청당 B장 추론.
    embed_batch: int = 1
    # 좌표 기반 매칭(v2) 허용 오차 — µm 단위.  두 좌표의 유클리드 거리가 이 이내면 매칭.
    coord_tolerance: float = DEFAULT_COORD_TOLERANCE
    # CPU 재채점 고속화 노브 — ORB 검출 특징 수(0=기본 500).  ORB(디스크립터 검출/
    # 정합)는 고전 채점에서 가장 비싼 항이라, 특징 수를 줄이면 CPU 매치 단계가 빨라진다
    # (정확도는 검증 필요).  개발자 벤치마크 전용으로만 0 이 아닌 값을 쓴다.
    orb_nfeatures: int = 0
    # 중앙-가중 ORB — defect 이 정중앙인 특성 활용.  0=끔(현행).  >0 이면 ORB 매치를
    # 중앙 근접도로 가중해(단일 패스·추가 추출 없음) defect 판별력을 높인다.  추출이
    # 아니라 '채점' 단계 파라미터라 특징 캐시 키는 그대로(좌표는 항상 저장).
    orb_center_weight: float = 0.0
    # 재채점 항 선택 — None=전체(pHash+ORB+SSIM, 기본 모드).  부분집합(예: {"orb"})이면
    # 그 항만으로 재채점한다.  고효율 모드는 실측 최적인 rr_orb_center50(ORB 단독+중앙가중)
    # 을 위해 {"orb"} 를 쓴다.  ``efficiency_matcher`` 가 이 값으로 ``components`` 를 넘긴다.
    rerank_components: Optional[frozenset] = None
    # 중앙-인식(center-aware) 채점 노브 — center_crop 이 켜졌을 때 사용할 중앙 ROI
    # 비율(0=기본 0.3).  반도체 AOI 이미지는 defect 이 정중앙에 있으므로, 작은 중앙
    # crop(예: 0.25)은 'defect 신호'를, 풀 ROI 는 '주변 패턴'을 본다.  벤치마크의
    # region-fusion/cascade 가 이 값으로 중앙 변형 cfg 를 만든다.
    center_ratio: float = 0.0

    def _center_crop_ratio(self) -> float:
        """center_crop 적용 시 실제 ROI 비율(0=레거시 0.3)."""
        r = float(self.center_ratio or 0.0)
        return r if r > 0.0 else 0.3

    def _center_crop_for(self, side) -> bool:
        """이 side(ref/val)에 중앙 영역 crop 을 적용할지."""
        if side in ("ref", "val"):
            return self.center_crop
        return False               # side 미지정 → crop 안 함 (캐시 키와 일관)

    def cache_extra(self, side=None) -> str:
        """캐시 키 판별자.  전처리 OFF 면 빈 문자열 → 기본 캐시와 동일 키.

        중앙 30% crop 은 side(ref/val)에 적용되므로 side 별로 키를 분리한다
        (교차검증에서 동일 파일이 ref/val 양쪽으로 쓰일 때 캐시 충돌 방지)."""
        parts = []
        if self._center_crop_for(side):
            # 중앙 crop 비율을 키에 반영 — 비율이 다르면 캐시 분리(c30/c25/…).
            parts.append(f"c{int(round(self._center_crop_ratio() * 100))}")
        if self.orb_nfeatures:
            parts.append(f"orb{int(self.orb_nfeatures)}")   # 특징 수 다르면 캐시 분리
        return "-".join(parts)


# 기본 cfg 싱글턴 — engine=basic, 전처리 전부 OFF (현행 동작).
DEFAULT_SIM_CONFIG = SimilarityConfig()


# ---------------------------------------------------------------------------
# Similarity pipeline weights — tunable from a YAML/JSON config later.
# ---------------------------------------------------------------------------
@dataclass
class SimilarityWeights:
    # ※ 한때 네 번째 항으로 CNN 임베딩 가중치가 있었으나, 그 경로를 켤 수단
    #   (학습된 .pt · 모델 레지스트리)이 모두 제거돼 영구 비활성이라 함께 지웠다.
    phash: float = 0.2
    orb: float = 0.3
    ssim: float = 0.2

    def normalized(self) -> "SimilarityWeights":
        """세 항의 합이 1 이 되도록 정규화."""
        total = self.phash + self.orb + self.ssim
        if total <= 0:
            return self
        return SimilarityWeights(
            phash=self.phash / total,
            orb=self.orb / total,
            ssim=self.ssim / total,
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
