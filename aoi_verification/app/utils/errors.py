"""공통 예외 계층 + '침묵 실패' 가시화 헬퍼.

이 앱은 옵션 의존성(torch/openvino/hnswlib)·네트워크·디스크 폴백을 위해
``except Exception`` 으로 실패를 폭넓게 흡수하는 곳이 많다.  그 자체는 의도된
견고성이지만, **진짜 버그까지 조용히 묻혀** 원인 추적이 어려워진다.

여기서는 *동작을 바꾸지 않고* (= 예외를 다시 던지지 않고) 실패를 **로그로
가시화**하는 헬퍼만 제공한다.  호출부는 기존 ``except Exception`` 을 유지한 채
``log_silent(...)`` 한 줄만 추가하면 되어, 폴백 흐름과 진단 가능성을 모두 얻는다.

레벨 가이드:
- ``logging.DEBUG``   : 옵션 의존성 부재 등 '정상적인' 폴백 (예: torch 미설치).
- ``logging.WARNING`` : 복구 가능한 I/O·네트워크 실패 (재시도/폴백으로 진행).
- ``logging.ERROR``   : 예기치 못한 실패 (트레이스백 포함, 버그 의심).
"""

from __future__ import annotations

import logging
from typing import Optional

# 앱 전역 로거 — 하위 로거(aoi.openvino, aoi.match 등)의 부모.
logger = logging.getLogger("aoi")


# ---------------------------------------------------------------------------
# 예외 계층 (점진 도입용 — 신규 코드에서 의미를 명확히 하고 싶을 때 사용)
# ---------------------------------------------------------------------------
class AoiError(Exception):
    """앱 도메인 예외의 베이스."""


class OptionalDependencyMissing(AoiError):
    """torch/openvino/hnswlib 등 선택 의존성이 없을 때 (정상 폴백)."""


class RecoverableError(AoiError):
    """복구 가능한 I/O·네트워크 실패 — 폴백/재시도로 진행 가능."""


# ---------------------------------------------------------------------------
# 침묵 실패 로깅 헬퍼
# ---------------------------------------------------------------------------
def log_silent(context: str,
               exc: Optional[BaseException] = None,
               *,
               level: int = logging.DEBUG) -> None:
    """삼켜진 예외를 로그로만 남긴다 (절대 다시 던지지 않음).

    ``except Exception`` 블록 안에서 호출해 동작은 그대로 두고 가시성만 높인다.
    ``exc`` 가 주어지면 트레이스백을 함께 기록(ERROR/WARNING 권장).
    로깅 자체가 실패해도 호출부 흐름을 막지 않는다.
    """
    try:
        if exc is not None and level >= logging.WARNING:
            logger.log(level, "%s: %s", context, exc, exc_info=exc)
        elif exc is not None:
            logger.log(level, "%s: %s", context, exc)
        else:
            logger.log(level, "%s", context)
    except Exception:
        # 로깅 실패는 무시 — 진단 보조 기능이 본 흐름을 깨면 안 된다.
        pass
