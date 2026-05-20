"""고속 모드 의존성 감지 + 백그라운드 설치 도우미.

고속 모드(임베딩 + ANN)는 ``hnswlib`` (필수) 와, Intel 하드웨어라면
``openvino`` (선택 — Intel GPU/NPU 가속) 가 있어야 제 성능을 낸다.  둘 중
하나라도 없으면 고속 모드가 조용히 기본 모드로 폴백해 "속도 차이가 없다"는
혼란을 준다.  이 모듈은 무엇이 빠졌는지 판정하고, 사용자가 한 번의 클릭으로
``pip install`` 하도록 돕는다 (openvino_installer 와 동일 패턴).
"""

from __future__ import annotations

import subprocess
import sys
from typing import List, Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal


# ---------------------------------------------------------------------------
# 감지
# ---------------------------------------------------------------------------
def is_hnswlib_installed() -> bool:
    try:
        import hnswlib  # noqa: F401
        return True
    except Exception:
        return False


def is_torch_installed() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def fast_ready() -> bool:
    """고속 모드 동작 가능 여부.

    고속 모드는 경량 디스크립터(NumPy/OpenCV/Pillow — 핵심 의존성)만 사용하므로
    별도 설치 없이 항상 동작한다.  torch/hnswlib/모델 다운로드 모두 불필요."""
    return True


def missing_packages(*, recommend_openvino: bool = True) -> List[str]:
    """고속 모드를 위해 추가 설치가 필요한 pip 패키지 — 이제 없음.

    고속 모드가 핵심 의존성만으로 동작하므로 빈 목록을 반환한다.  (과거에는
    hnswlib/torch 를 요구했으나 경량 디스크립터로 대체됨.)"""
    return []


# ---------------------------------------------------------------------------
# 설치 워커 — pip install <packages...>
# ---------------------------------------------------------------------------
class _InstallSignals(QObject):
    progress = pyqtSignal(str)        # stdout 한 줄
    finished = pyqtSignal(bool, str)  # (ok, message)


class FastDepsInstallWorker(QThread):
    """``pip install <packages>`` 를 백그라운드에서 실행 (UI 비차단)."""

    def __init__(self, packages: List[str],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._packages = [p for p in packages if p]
        self.signals = _InstallSignals()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:        # type: ignore[override]
        if not self._packages:
            self.signals.finished.emit(True, "설치할 패키지가 없습니다")
            return
        cmd = [sys.executable, "-m", "pip", "install",
               "--disable-pip-version-check", *self._packages]
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
            try:
                from . import embedder as _emb
                _emb.invalidate_caches()
            except Exception:
                pass
            self.signals.finished.emit(True, "설치 완료")
        else:
            self.signals.finished.emit(False, f"pip 종료 코드 {rc}")
