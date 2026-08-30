"""사용자 UI 환경설정의 영속 저장소.

- 슬라이더 위치 / 임계치 / 사진 크기 / 모델 카드 펼침 여부 등.
- ``~/.aoi_verification_cache/ui_prefs.json`` 1 개 파일에 통합.
- 읽기 실패는 묵묵히 기본값으로 fallback (검증 흐름을 절대 막지 않는다).
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .. import config as _config


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
    splitter_state_match_h: str = ""
    # 사용 방법 패널 펼침 상태 (기본 접힘)
    howto_expanded: bool = False
    # 썸네일 빠른 모드 (사용자가 강제로 가장 낮은 품질 티어 사용)
    # 자동화 수준 — 사용자 개입 정도 (#3 올인원 모드)
    #   "user_select" : Stage 1 만 직접, Stage 2 자동 매치 + 검토.  (기본)
    #   "auto_all"    : Stage 1 건너뜀 (모든 ref 사용), Stage 2 자동 매치 + 검토.
    automation_level: str = "user_select"
    # OpenVINO (Intel GPU 가속) 자동 설치 안내를 거절한 경우 — 다시 묻지
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
    persist_scores: bool = False             # 유사도 점수 디스크 캐시 (#5B)
    # 고효율 모드 동시 추론 수(in-flight).  높일수록 GPU
    # 메모리·throughput↑ (계산 결과 불변).  setup_page 슬라이더로 조절.
    accel_concurrency: int = 32
    # 고효율 모드 장치 사용 토글 + 정적 배치 B (테스트용).
    use_cpu: bool = True
    use_gpu: bool = True
    embed_batch: int = 1
    # 좌표 기반 매칭(v2) 허용 오차 — µm 단위.  두 좌표가 이 거리 이내면 매칭.
    coord_tolerance: float = _config.DEFAULT_COORD_TOLERANCE
    # 저장 파일의 세대.  기본값을 바꿨을 때 **이미 저장된 파일**을 한 번만 손보기 위한
    # 표식이다 — 아래 `migrate` 참조.  0 = 이 표식이 생기기 전에 저장된 파일.
    prefs_version: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "UiPrefs":
        valid = {k: d[k] for k in d if k in cls.__dataclass_fields__}
        return cls(**valid)


# ---------------------------------------------------------------------------
PREFS_VERSION = 1
# 세대 1 이전의 좌표 허용 오차 기본값.  이 값을 그대로 쓰던 파일만 새 기본값으로 옮긴다.
_TOL_DEFAULT_BEFORE_V1 = 500.0


def migrate(p: "UiPrefs") -> "UiPrefs":
    """저장된 설정을 현재 세대로 한 번만 옮긴다.  **순수 함수** — 파일을 건드리지 않는다.

    ★ 왜 필요한가: 기본값만 바꾸면 **기존 사용자에게는 아무 일도 일어나지 않는다.**
    `patch()` 는 dataclass 전체를 저장하고, 셋업 화면은 [검증 시작] 마다
    `coord_tolerance` 를 patch 한다 — 한 번이라도 검증을 돌린 사람의 파일에는 옛 기본값
    500 이 박혀 있다.  `engine_mode` 가 `resolve_legacy_state` 를 필요로 했던 것과 같은
    구조다.

    ★ 한계(알고 하는 것): 숫자만으로는 '기본값에 떠밀린 500' 과 '직접 고른 500' 을
    구분할 수 없다.  그래서 정확히 옛 기본값이던 파일만 옮기고, 다른 값(예: 350)은
    사용자의 의도로 보아 **손대지 않는다.**
    """
    if p.prefs_version >= PREFS_VERSION:
        return p
    if p.coord_tolerance == _TOL_DEFAULT_BEFORE_V1:
        p.coord_tolerance = _config.DEFAULT_COORD_TOLERANCE
    p.prefs_version = PREFS_VERSION
    return p


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


# 파싱 결과 메모 — (경로, mtime_ns, 크기) → dict.
# ★ 시작 한 번에 이 함수가 7번 불린다(메인·창 기하·설정 화면 3곳·선별·매칭).
#   회사 환경에서 prefs 가 네트워크 홈에 있으면 그 왕복이 눈에 띈다.  파일이
#   바뀌면 키가 달라져 저절로 무효화되고, `save` 는 mtime 을 바꾸므로 같은
#   프로세스가 쓴 변경도 다음 load 에서 그대로 보인다.
_cached: tuple | None = None


def load() -> UiPrefs:
    """저장된 설정.  **매번 새 인스턴스**를 돌려준다(호출부가 자유롭게 고친다).

    ★ 메모한 dict 는 깊은 복사로 주고받는다 — ``extra`` 가 가변이라 얕게 넘기면
    호출부가 그것을 고칠 때 캐시가 함께 오염된다."""
    global _cached
    p = _file()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return UiPrefs()                 # 파일 없음 — 기본값
    if _cached is not None and _cached[0] == key:
        return migrate(UiPrefs.from_dict(deepcopy(_cached[1])))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        prefs = migrate(UiPrefs.from_dict(data))
    except Exception:
        return UiPrefs()
    _cached = (key, deepcopy(data))
    return prefs


def save(prefs: UiPrefs) -> None:
    global _cached
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
    finally:
        # mtime 만 믿지 않는다 — 같은 나노초에 두 번 쓰는 경우까지 확실히 끊는다.
        _cached = None


def patch(**kwargs: Any) -> UiPrefs:
    """원하는 필드만 갱신. 결과 UiPrefs 반환."""
    cur = load()
    for k, v in kwargs.items():
        if k in UiPrefs.__dataclass_fields__:
            setattr(cur, k, v)
    save(cur)
    return cur
