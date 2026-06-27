"""상태바 GPU/NPU '가동/대기' 표시의 기반 — 가속 유닛 활동 추적 단위 테스트.

Intel GPU/NPU 의 실제 점유율(%)은 이식성 있게 얻을 수 없어, OpenVINO 추론이
발생할 때 디바이스별 timestamp 를 찍고 최근 활동 여부로 '가동/대기'를 표시한다.
"""

from __future__ import annotations

import time

from aoi_verification.app.learning import embedder_openvino as ov


def test_unit_busy_after_mark():
    ov.mark_unit_active("GPU")
    assert ov.unit_busy("GPU") is True


def test_unit_idle_when_never_marked():
    # NPU 를 한 번도 마킹하지 않으면 대기.
    assert ov.unit_busy("NPU_NEVER_USED_TAG") is False


def test_device_tag_normalized():
    """'GPU.1' 같은 인덱스 디바이스도 'GPU' 로 정규화."""
    ov.mark_unit_active("GPU.1")
    assert ov.unit_busy("GPU") is True
    ov.mark_unit_active("NPU.0")
    assert ov.unit_busy("NPU") is True


def test_activity_window_expires():
    ov.mark_unit_active("GPU")
    # window=0 이면 즉시 만료된 것으로 간주.
    assert ov.unit_busy("GPU", window=0.0) is False
    # 충분히 큰 window 면 여전히 가동.
    assert ov.unit_busy("GPU", window=60.0) is True


def test_window_boundary():
    ov.mark_unit_active("NPU")
    time.sleep(0.05)
    assert ov.unit_busy("NPU", window=1.0) is True
    assert ov.unit_busy("NPU", window=0.01) is False
