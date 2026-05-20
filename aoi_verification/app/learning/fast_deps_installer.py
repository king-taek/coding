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
    """고속 모드가 실제로 동작 가능한 상태인지.

    ANN 검색은 NumPy 브루트포스 폴백이 있어 hnswlib 가 없어도 되므로, 임베딩을
    만들 torch 만 있으면 고속 모드가 동작한다.  hnswlib 는 대용량 가속용 옵션."""
    return is_torch_installed()


def missing_packages(*, recommend_openvino: bool = True) -> List[str]:
    """고속 모드를 위해 설치가 필요/권장되는 pip 패키지 목록.

    - ``torch`` : 임베딩 추출에 필수 (보통 requirements 로 이미 설치됨).
    - ``openvino``: Intel CPU 인데 없으면 GPU/NPU 가속 권장 (선택).
    hnswlib 는 네이티브 빌드가 필요해 제한된 환경에서 설치 불가할 수 있고,
    NumPy 폴백으로 대체되므로 목록에 넣지 않는다 (강제하지 않음)."""
    pkgs: List[str] = []
    if not is_torch_installed():
        pkgs.append("torch")
    if recommend_openvino:
        try:
            from . import openvino_installer as _ovi
            if _ovi.is_intel_cpu() and not _ovi.is_openvino_installed():
                pkgs.append("openvino")
        except Exception:
            pass
    return pkgs


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
