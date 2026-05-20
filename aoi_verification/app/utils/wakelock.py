"""OS 절전/화면보호기 억제 (#14) — 긴 검증 세션 중 화면이 꺼지지 않게.

세션 시작 시 ``acquire()``, 종료/중단 시 ``release()``.  플랫폼별 best-effort:
- Windows : ``SetThreadExecutionState`` (CONTINUOUS | SYSTEM | DISPLAY).
- macOS   : ``caffeinate -dimsu`` 자식 프로세스.
- Linux   : ``systemd-inhibit`` (있으면), 없으면 무동작.

모든 실패는 조용히 무시 — 절전 억제 실패가 검증 흐름을 막아선 안 된다.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Optional

_active = False
_proc: Optional[subprocess.Popen] = None


def acquire() -> None:
    """절전/디스플레이 슬립 억제 시작 (idempotent)."""
    global _active, _proc
    if _active:
        return
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            )
            _active = True
        elif sys.platform == "darwin":
            _proc = subprocess.Popen(
                ["caffeinate", "-dimsu"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            _active = True
        else:
            # Linux — systemd-inhibit 가 있으면 sleep 막기.
            try:
                _proc = subprocess.Popen(
                    ["systemd-inhibit", "--what=idle:sleep",
                     "--why=AOI verification running",
                     "--mode=block", "sleep", "infinity"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _active = True
            except FileNotFoundError:
                _active = False
    except Exception:
        _active = False


def release() -> None:
    """절전 억제 해제 (idempotent)."""
    global _active, _proc
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        if _proc is not None:
            try:
                _proc.terminate()
            except Exception:
                pass
            _proc = None
    except Exception:
        pass
    finally:
        _active = False


def is_active() -> bool:
    return _active
