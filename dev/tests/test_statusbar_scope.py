"""상태 바는 **사용자가 행동을 바꿀 수 있는 정보만** 담는다.

한때 상태바에 (a) 'Intel GPU 가속 (…)' 디바이스 표시와 (b) 'CPU 37%   GPU 가동'
사용량 표시가 있었다.  둘 다 사용자가 그 값을 보고 할 수 있는 일이 없었다 —
가속 장치는 세션 중 바뀌지 않고, GPU 가동 여부는 관찰용 숫자다.

메모리는 다르다: 압박 토스트가 '슬롯을 나눠 돌리라'는 행동으로 이어진다.  그래서
메모리만 남겼다.  진단용 함수 자체(embedder.device_label 등)는 개발자 벤치마크가
쓰므로 살아 있다 — **상태바가 그것을 상시 표시하지 않는다**는 것이 이 테스트의 계약이다.
"""

from __future__ import annotations

import inspect

from aoi_verification.app import i18n
from aoi_verification.app.ui import main_window as mw


def test_statusbar_keeps_memory_only():
    src = inspect.getsource(mw.MainWindow)
    assert "_mem_label" in src and "_update_memory_label" in src
    for gone in ("_usage_label", "_device_label", "_accel_present",
                 "_accel_tip_base", "_update_usage_label"):
        assert gone not in src, f"상태바에 {gone} 가 되살아났다"


def test_usage_strings_are_gone():
    """닿지 않는 문구를 남기지 않는다(집안 규칙)."""
    for gone in ("USAGE_CPU_FMT", "USAGE_GPU_FMT", "USAGE_STATE_BUSY",
                 "USAGE_STATE_IDLE", "USAGE_STATE_NONE", "USAGE_SEP"):
        assert not hasattr(i18n.KO, gone), f"i18n.KO.{gone} 잔존"
    # 남아야 하는 것.
    assert hasattr(i18n.KO, "MEMORY_USAGE_FMT")
    assert hasattr(i18n.KO, "MEMORY_PRESSURE_TOAST")


def test_diagnostic_helpers_still_exist():
    """상태바에서 떼는 것과 함수를 지우는 것은 다르다 — 진단 경로는 보존한다."""
    from aoi_verification.app.learning import embedder, embedder_openvino
    assert callable(embedder.device_label)
    for name in ("accelerator_presence", "unit_busy", "compile_diagnostics"):
        assert callable(getattr(embedder_openvino, name)), name
