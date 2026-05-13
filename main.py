"""AOI 검증 — 애플리케이션 진입점.

Spyder IPython 콘솔 / 일반 Python 실행 모두를 지원한다.
- Spyder 에서는 이미 QApplication 이 존재할 수 있어 재사용한다.
- 일반 인터프리터에서는 새로 만들고 `exec()` 를 호출한다.
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
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication

    # Spyder/IPython 호환: 기존 인스턴스가 있으면 재사용
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

    # Spyder IPython (Qt 백엔드) 에서는 자체 이벤트 루프가 돌고 있으므로
    # exec() 를 호출하지 않는다.  일반 Python 인터프리터에서만 exec().
    if created_here:
        return app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
