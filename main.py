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


def _apply_app_icon(app) -> None:
    """앱 아이콘 — 모든 창(메인/시트/작업표시줄) 이 이 아이콘을 물려받는다."""
    from PyQt6.QtGui import QIcon
    from aoi_verification.app.utils import paths

    app.setWindowIcon(QIcon(str(paths.logo_path("logo.ico"))))
    # Windows 작업표시줄은 프로세스의 AppUserModelID 로 아이콘을 고른다 — 지정하지
    # 않으면 파이썬 실행 파일 아이콘이 뜬다(개발 실행·포터블 런처 모두).
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "AOI.Verification.App"
            )
        except Exception:
            pass


def _load_stylesheet(app) -> None:
    from aoi_verification.app.utils import paths, prefs as _prefs
    from aoi_verification.app.ui import theme
    # 저장된 색 모드·모션 설정을 스타일시트 적용 전에 확정.
    try:
        _p = _prefs.load()
        theme.set_color_mode(getattr(_p, "color_mode", theme.DEFAULT_COLOR_MODE))
    except Exception:
        pass
    qss_path = paths.resource_path("aoi_verification/app/ui/style.qss")
    try:
        text = Path(qss_path).read_text(encoding="utf-8")
        # style.qss 는 $token 템플릿 — 테마 토큰으로 치환해 적용 (단일 출처).
        app.setStyleSheet(theme.render_qss(text))
    except Exception:
        pass


def _show_splash(app):
    """로고 스플래시를 **가장 먼저** 띄운다 — 무거운 것을 불러오기 전에."""
    from PyQt6.QtGui import QPixmap
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.widgets.startup_splash import StartupSplash
    from aoi_verification.app.utils import paths

    splash = StartupSplash(QPixmap(str(paths.logo_path("logo_big.png"))))
    splash.show()
    # 총량을 모르는 구간 → busy(무한 진행).  0 에 멈춰 있으면 안 된다(CLAUDE.md).
    splash.set_progress(0, 0, i18n.KO.SPLASH_MODULES)
    app.processEvents()
    return splash


def _preload_heavy_module():
    """화면 모듈(torch/cv2/openvino 를 끌고 온다) 을 백그라운드에서 import.

    시작 시간의 대부분이 여기다.  메인 스레드에서 하면 스플래시가 그대로 얼어붙으니
    (CLAUDE.md 로딩 규칙) 스레드로 돌리고 메인 스레드는 이벤트 루프를 계속 굴린다.
    실패는 여기서 삼킨다 — 이어서 메인 스레드가 같은 import 를 하며 원래 오류를 낸다."""
    import importlib
    import threading

    done = threading.Event()

    def _work() -> None:
        try:
            importlib.import_module("aoi_verification.app.ui.main_window")
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=_work, name="preload", daemon=True).start()
    return done


def _open_main_window(splash):
    """메인 창 생성(페이지 진행을 스플래시에 보고) → 표시 → 스플래시 걷기."""
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.main_window import MainWindow

    window = MainWindow(progress=splash.set_progress)
    window.show()
    splash.set_progress(1, 1, i18n.KO.SPLASH_READY)
    splash.finish(window)
    return window


def _use_bundled_torch_home() -> None:
    """동봉된 모델 가중치를 쓰도록 ``TORCH_HOME`` 을 돌린다(torch import 전에 해야 한다).

    사내망은 ``download.pytorch.org`` 가 막혀 있을 수 있어, exe 배포본은 가중치를 미리
    받아 동봉한다.  동봉본이 없으면 아무것도 하지 않는다(기존 동작 유지)."""
    import os

    from aoi_verification.app.utils import paths

    d = paths.bundled_torch_home()
    if d is not None:
        os.environ.setdefault("TORCH_HOME", str(d))


def main() -> int:
    _ensure_package_on_path()
    try:
        _use_bundled_torch_home()
    except Exception:
        pass                      # 가중치 경로 문제로 앱이 안 뜨는 일은 없게 한다

    from PyQt6.QtCore import Qt, QTimer
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
    app.setFont(QFont("Pretendard, Noto Sans KR, Malgun Gothic, Segoe UI"))
    _apply_app_icon(app)
    _load_stylesheet(app)

    # 스플래시(로고) → 로딩 → 윈도우 -------------------------------------
    splash = _show_splash(app)

    # 좀비 윈도우 방지를 위해 함수 로컬에 둔다(콜백 경로도 여기 담아 살려 둔다).
    window_ref = []

    # 외부에서 만든 app 을 재사용한 경우엔 우리가 루프를 굴리지 않으므로
    # 스레드 완료를 기다릴 방법이 없다 — 그때는 동기로 연다(스플래시는 정지).
    if not created_here:
        window_ref.append(_open_main_window(splash))
        return 0

    loaded = _preload_heavy_module()

    def _tick() -> None:
        if not loaded.is_set():
            return
        timer.stop()
        window_ref.append(_open_main_window(splash))

    timer = QTimer()
    timer.setInterval(30)
    timer.timeout.connect(_tick)
    timer.start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
