"""AOI 검증 — 애플리케이션 진입점.

VS Code (또는 일반 Python) 에서 ``python main.py`` 또는 F5 로 실행한다.

- 보통 새 ``QApplication`` 을 만들고 ``exec()`` 로 이벤트 루프 진입.
- 만약 외부에서 이미 ``QApplication`` 을 만들어 둔 환경(예: 일부 IDE 의
  내장 콘솔) 에서 import 형태로 호출되면 기존 인스턴스를 재사용하고
  ``exec()`` 를 생략한다.  좀비 윈도우 / 두 번째 실행 실패 방지.
- PyInstaller --onefile 빌드 시에도 동일하게 동작한다.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_package_on_path() -> None:
    """이 파일을 ‘파일 단독 실행’ 했을 때도 import 가 통하도록 보강."""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


def _load_stylesheet(app) -> None:
    from aoi_verification.app.utils import paths
    qss_path = paths.resource_path("aoi_verification/app/ui/style.qss")
    try:
        text = Path(qss_path).read_text(encoding="utf-8")
        app.setStyleSheet(text)
    except Exception:
        pass


def main() -> int:
    _ensure_package_on_path()

    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QFont, QGuiApplication
    from PyQt6.QtWidgets import QApplication

    # High-DPI 모니터에서 흐릿함 방지. QApplication 생성 전에 적용해야 한다.
    # 일부 PyQt6 빌드에서는 AA_EnableHighDpiScaling 이 deprecated 이므로 try.
    try:
        QApplication.setAttribute(
            Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True
        )
    except (AttributeError, TypeError):
        pass
    try:
        QApplication.setAttribute(
            Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True
        )
    except (AttributeError, TypeError):
        pass
    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, TypeError):
        pass

    # 기존 QApplication 이 있으면 재사용 (IDE 내장 콘솔 호환), 아니면 새로 생성
    app = QApplication.instance()
    created_here = False
    if app is None:
        app = QApplication(sys.argv)
        created_here = True

    # 기본 폰트 — 한글 폴백 우선
    app.setFont(QFont("Rajdhani, Pretendard, Noto Sans KR, Malgun Gothic"))
    _load_stylesheet(app)

    # 윈도우 생성 ---------------------------------------------------------
    from aoi_verification.app.ui.main_window import MainWindow

    # 좀비 윈도우 방지를 위해 함수 로컬에 둔다.
    window = MainWindow()
    window.show()

    # 우리가 QApplication 을 만든 경우에만 이벤트 루프 진입.
    # 외부에서 만든 app 을 재사용한 경우엔 그쪽이 루프를 굴리므로 생략.
    if created_here:
        return app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
