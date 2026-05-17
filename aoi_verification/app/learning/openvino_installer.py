"""OpenVINO 자동 감지 + 백그라운드 설치 도우미.

목적: Intel CPU 사용자가 NPU / Iris Xe GPU 가속을 ‘별도 설명 없이도’
받을 수 있게, ``openvino`` 패키지가 없으면 첫 실행 시 한 번 안내해서
바로 설치할 수 있게 한다.

흐름:
1. ``should_offer_install()`` — Intel 하드웨어이고, openvino 가 없고,
   사용자가 이전에 거절하지 않았을 때 True.
2. ``InstallWorker(QThread)`` — 백그라운드에서 ``pip install openvino``
   를 실행하고 stdout 진행 라인을 시그널로 전달.
3. 설치 성공 시 ``embedder.invalidate_caches()`` + 안내 ‘다음 실행 시
   가속이 적용됩니다’ (런타임 hot-reload 는 위험해서 시도하지 않음).
"""

from __future__ import annotations

import platform
import subprocess
import sys
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ---------------------------------------------------------------------------
# 감지 헬퍼
# ---------------------------------------------------------------------------
def is_openvino_installed() -> bool:
    """``openvino`` import 가능 여부 — 가장 신뢰성 있는 판정."""
    try:
        import openvino  # noqa: F401
        return True
    except Exception:
        return False


def is_intel_cpu() -> bool:
    """Intel CPU 여부 — OpenVINO 설치 권유 대상.

    Intel CPU 가 아니면 OpenVINO 의 NPU/GPU 가속도 의미 없으므로 권유하지
    않는다.  ``platform.processor()`` 가 비어있을 수 있어 OS 별 보강.
    """
    info = ""
    try:
        info = (platform.processor() or "").lower()
    except Exception:
        pass
    if "intel" in info:
        return True
    # Linux: /proc/cpuinfo 의 vendor_id.
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("vendor_id"):
                    return "intel" in line.lower()
                if line.lower().startswith("model name"):
                    if "intel" in line.lower():
                        return True
    except Exception:
        pass
    # Windows: WMIC 가 무겁고 deprecated 라 platform 결과 외엔 신뢰 못 함.
    # 대부분 platform.processor() 가 'Intel64 Family ...' 등을 반환해 위 분기에
    # 걸린다.  여기까지 떨어지면 ‘판단 불가’ → False 로 보수적 처리.
    return False


def should_offer_install(declined: bool) -> bool:
    """설치 권유 다이얼로그를 띄울지 결정.

    - 이미 설치돼 있으면 권유 불필요.
    - Intel CPU 가 아니면 NPU/Intel GPU 가속이 무의미 → 권유 안 함.
    - 사용자가 한 번 거절한 적 있으면 안 함 (다시 묻지 않기).
    """
    if declined:
        return False
    if is_openvino_installed():
        return False
    return is_intel_cpu()


# ---------------------------------------------------------------------------
# 설치 워커
# ---------------------------------------------------------------------------
class _InstallSignals(QObject):
    progress = pyqtSignal(str)        # stdout 한 줄
    finished = pyqtSignal(bool, str)  # (ok, message)


class OpenVinoInstallWorker(QThread):
    """``pip install openvino`` 를 백그라운드에서 실행.

    UI 가 멈추지 않도록 별도 스레드. stdout 을 라인 단위로 forward 해서
    LoadingOverlay 등에 진행 상황을 표시할 수 있다.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.signals = _InstallSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", "openvino"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except Exception as exc:
            self.signals.finished.emit(False, f"pip 실행 실패: {exc}")
            return
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._stop:
                    proc.terminate()
                    self.signals.finished.emit(False, "사용자가 취소함")
                    return
                line = line.rstrip("\n")
                if line:
                    self.signals.progress.emit(line)
            rc = proc.wait()
        except Exception as exc:
            self.signals.finished.emit(False, f"설치 중 오류: {exc}")
            return
        if rc == 0:
            # 설치 성공 시 embedder 캐시 무효화 (다음 호출에서 재감지).
            try:
                from . import embedder as _emb
                _emb.invalidate_caches()
            except Exception:
                pass
            self.signals.finished.emit(True, "설치 완료")
        else:
            self.signals.finished.emit(False, f"pip 종료 코드 {rc}")
