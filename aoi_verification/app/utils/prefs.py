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
class AutomationLevel:
    MANUAL = "manual"
    USER_SELECT = "user_select"
    AUTO_ALL = "auto_all"

    AUTO_MODES = frozenset({USER_SELECT, AUTO_ALL})

    @classmethod
    def is_auto(cls, level: str) -> bool:
        return level in cls.AUTO_MODES


# 유사도 엔진 모드 — 기본(현행) vs 고속(임베딩+ANN).  raw string 대신 사용.
class EngineMode:
    BASIC = "basic"        # 기존 파이프라인, 변경 없음 (기본값)
    FAST = "fast"          # 임베딩 + hnswlib ANN, 상위 K 재정렬

    ALL = frozenset({BASIC, FAST})

    @classmethod
    def is_fast(cls, mode: str) -> bool:
        return mode == cls.FAST


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
    #   "manual"      : 기존 흐름. Stage 1 (검증/제외) + Stage 2 (수동 매치).
    #   "user_select" : Stage 1 만 직접, Stage 2 자동 매치 + 검토.
    #   "auto_all"    : Stage 1 건너뜀 (모든 ref 사용 + 그룹 대표만 큐에),
    #                   Stage 2 자동 매치 + 그룹/매치 검토.
    automation_level: str = "manual"
    # OpenVINO (Intel GPU/NPU 가속) 자동 설치 안내를 거절한 경우 — 다시 묻지
    # 않음.  사용자가 ‘다시 보지 않기’ 를 선택했거나 설치 시도 후 실패하면 True.
    openvino_install_declined: bool = False
    # 유사도 엔진 모드 + 강화 전처리 토글 (계산 전용, 화면 표시는 원본 유지).
    engine_mode: str = "basic"               # EngineMode.{BASIC,FAST}
    pre_grayscale: bool = False              # 강화: 흑백 + 고감도
    pre_contrast: bool = False               # 강화: 고대비
    pre_bg_removal: bool = False             # 강화: 배경 제거(누끼)
    kla_crop: bool = False                   # KLA 상/하단 정보영역 crop
    kla_crop_top: float = 0.08               # 상단 잘라낼 비율 (0~0.4)
    kla_crop_bottom: float = 0.08            # 하단 잘라낼 비율 (0~0.4)
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UiPrefs":
        valid = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**valid)


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
