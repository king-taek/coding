"""사용자 UI 환경설정의 영속 저장소.

- 슬라이더 위치 / 임계치 / 사진 크기 / 모델 카드 펼침 여부 등.
- ``~/.aoi_verification_cache/ui_prefs.json`` 1 개 파일에 통합.
- 읽기 실패는 묵묵히 기본값으로 fallback (검증 흐름을 절대 막지 않는다).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths


_PREFS_FILE = "ui_prefs.json"


# 자동화 수준 상수 (#3 올인원 모드) — 코드 전반에서 raw string 대신 이걸 사용.
# ‘수동’은 제거되어 사진 직접 선택 / 모두 자동 두 가지만 남는다(둘 다 자동 매치).
class AutomationLevel:
    USER_SELECT = "user_select"
    AUTO_ALL = "auto_all"

    AUTO_MODES = frozenset({USER_SELECT, AUTO_ALL})

    @classmethod
    def is_auto(cls, level: str) -> bool:
        return level in cls.AUTO_MODES


# 유사도 엔진 모드 — 기본(현행) vs 고효율(CPU+GPU fusion) vs 좌표 기반(v2).
class EngineMode:
    BASIC = "basic"            # 기존 파이프라인, 변경 없음 (기본값)
    EFFICIENCY = "efficiency"  # CPU(고전)+GPU(MobileNetV3) fusion-zscore
    COORDINATE = "coordinate"  # 좌표 직접 매칭 (v2) — 유사도 계산 없음

    ALL = frozenset({BASIC, EFFICIENCY, COORDINATE})

    @classmethod
    def is_efficiency(cls, mode: str) -> bool:
        return mode == cls.EFFICIENCY

    @classmethod
    def is_coordinate(cls, mode: str) -> bool:
        return mode == cls.COORDINATE


@dataclass
class UiPrefs:
    """다음 실행에도 이어갈 UI 상태."""

    threshold: float = 0.55                  # 0.0 ~ 1.0 (교차 호기 친화적 기본)
    image_long_edge_select: int = 400        # Stage 1 사진 크기 (px) — 250~700
    image_long_edge_match: int = 400         # Stage 2 사진 크기 (px) — 250~700
    last_ref_root: str = ""
    last_val_root: str = ""
    last_ref_machine: str = ""
    last_val_machine: str = ""
    last_mode: str = "single"
    # 창 크기 — 사용자가 마지막으로 드래그/리사이즈 한 값을 자동 저장.
    # 0 = 미설정 (첫 실행에서는 모니터 영역의 90% 로 시작).
    window_width: int = 0
    window_height: int = 0
    window_maximized: bool = False
    # QSplitter 상태 (Select/Match 페이지). base64 인코딩 문자열.
    splitter_state_select_h: str = ""
    splitter_state_select_v: str = ""
    splitter_state_match_h: str = ""
    # 사용 방법 패널 펼침 상태 (기본 접힘)
    howto_expanded: bool = False
    # 썸네일 빠른 모드 (사용자가 강제로 가장 낮은 품질 티어 사용)
    speed_mode: bool = False
    # 자동화 수준 — 사용자 개입 정도 (#3 올인원 모드)
    #   "user_select" : Stage 1 만 직접, Stage 2 자동 매치 + 검토.  (기본)
    #   "auto_all"    : Stage 1 건너뜀 (모든 ref 사용), Stage 2 자동 매치 + 검토.
    automation_level: str = "user_select"
    # OpenVINO (Intel GPU/NPU 가속) 자동 설치 안내를 거절한 경우 — 다시 묻지
    # 않음.  사용자가 ‘다시 보지 않기’ 를 선택했거나 설치 시도 후 실패하면 True.
    openvino_install_declined: bool = False
    # 유사도 엔진 모드 — 마지막으로 '실제 실행된' 모드(파생값).
    # 기본값은 좌표 매칭: 신규 사용자가 구형(유사도) 모드로 시작하던 버그를 고친다
    # (셋업 화면 안내 문구도 좌표 매칭이 기본이라고 말한다).
    engine_mode: str = EngineMode.COORDINATE
    # 구형(유사도) 모드 명시 스위치. None = 아직 설정된 적 없음(스위치 도입 전 prefs)
    # → resolve_legacy_state() 가 1회 유도한다.  이 값을 직접 읽지 말고 리졸버를 쓸 것.
    legacy_enabled: bool | None = None
    # 구형 모드 하위 선택 기억 — engine_mode 가 coordinate 로 바뀌어도 잃지 않게 분리.
    legacy_engine: str = ""                  # "" = 미설정
    # 화면 색 모드 — 수동 토글(라이트/다크). OS 자동 감지는 하지 않는다.
    color_mode: str = "light"                # "light" | "dark"
    # 셋업 화면 배치안 — 비교용(상단 스위처).  안이 확정되면 스위처와 함께 제거한다.
    setup_layout: str = "a"                  # "a" | "b" | "c"
    persist_scores: bool = False             # 유사도 점수 디스크 캐시 (#5B)
    # 고효율 모드 동시 추론 수(in-flight, NPU 기준; GPU 절반).  높일수록 NPU/GPU
    # 메모리·throughput↑ (계산 결과 불변).  setup_page 슬라이더로 조절.
    accel_concurrency: int = 32
    # 고효율 모드 장치 사용 토글 + 정적 배치 B (테스트용).
    use_cpu: bool = True
    use_gpu: bool = True
    use_npu: bool = False        # 효율 모드는 CPU+GPU fusion. NPU 비활성(코드만 보존).
    embed_batch: int = 1
    # 개발자 모드 — 켜면 셋업 화면에 ‘개발자 벤치마크’ 진입 버튼이 보인다.
    # (환경변수 AOI_DEV_MODE 로도 켤 수 있다.)  일반 사용자에겐 영향 없음.
    dev_mode: bool = False
    # 좌표 기반 매칭(v2) 허용 오차 — µm 단위.  두 좌표가 이 거리 이내면 매칭.
    coord_tolerance: float = 500.0
    # 모션 줄이기 — 화면 전환·스크롤 애니메이션을 즉시 적용(끔).
    reduce_motion: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UiPrefs":
        valid = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**valid)


# ---------------------------------------------------------------------------
def resolve_legacy_state(p: "UiPrefs") -> tuple[bool, str]:
    """(구형 모드 on?, 구형 하위 엔진) — 명시 스위치 도입 전 prefs 도 안전하게 해석.

    순수 함수(PyQt 불필요)라 헤드리스로 테스트한다.

    왜 유도가 필요한가: 스위치 도입 전에는 '접이식 섹션이 펼쳐졌는가'가 곧 모드였고,
    ``engine_mode`` 의 옛 기본값이 ``"basic"`` 이었다.  게다가 ``patch()`` 는 전체
    dataclass 를 저장하므로, 슬라이더만 만진 사용자 파일에도 ``"basic"`` 이 박혀 있다.
    즉 ``"basic"`` 만으로는 '직접 골랐다'와 '기본값에 떠밀렸다'를 구분할 수 없다.

    판정: ``"efficiency"`` 는 라디오를 직접 골라야 저장되므로 **의도적 선택**으로 인정해
    구형을 유지한다.  ``"basic"`` 은 옛 기본값과 구분 불가라 문서·안내 문구가 약속한
    좌표 매칭으로 되돌린다(하위 선택은 기억해 두므로 다시 켜는 건 클릭 1회).
    """
    legacy = (EngineMode.BASIC, EngineMode.EFFICIENCY)
    sub = p.legacy_engine if p.legacy_engine in legacy else ""
    if not sub:
        sub = p.engine_mode if p.engine_mode in legacy else EngineMode.BASIC
    if p.legacy_enabled is None:                  # 스위치 도입 전 prefs → 1회 유도
        return (p.engine_mode == EngineMode.EFFICIENCY, sub)
    return (bool(p.legacy_enabled), sub)


# ---------------------------------------------------------------------------
def _file() -> Path:
    return paths.cache_root() / _PREFS_FILE


def load() -> UiPrefs:
    p = _file()
    if not p.exists():
        return UiPrefs()
    try:
        return UiPrefs.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return UiPrefs()


def save(prefs: UiPrefs) -> None:
    p = _file()
    try:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(prefs.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(p)
    except OSError:
        pass


def patch(**kwargs: Any) -> UiPrefs:
    """원하는 필드만 갱신. 결과 UiPrefs 반환."""
    cur = load()
    for k, v in kwargs.items():
        if k in UiPrefs.__dataclass_fields__:
            setattr(cur, k, v)
    save(cur)
    return cur
