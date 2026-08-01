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


def _splash_logo(target_px: int):
    """스플래시 로고를 **필요한 크기로 디코드**해 돌려준다.

    원본은 2397×1338(4.3MB) 이라 통째로 디코드하면 그 비용이 '사용자가 처음 보는
    화면' 앞에 그대로 얹힌다.  ``QImageReader.setScaledSize`` 는 디코드 단계에서
    줄이므로 전체 픽셀을 만들지 않는다."""
    from PyQt6.QtCore import QSize
    from PyQt6.QtGui import QImageReader, QPixmap
    from aoi_verification.app.utils import paths

    path = str(paths.logo_path("logo_big.png"))
    try:
        reader = QImageReader(path)
        size = reader.size()
        if size.isValid() and size.width() > target_px:
            h = max(1, round(size.height() * target_px / size.width()))
            reader.setScaledSize(QSize(target_px, h))
        img = reader.read()
        if not img.isNull():
            return QPixmap.fromImage(img)
    except Exception:
        pass
    return QPixmap(path)


def _show_splash(app):
    """로고 스플래시를 **가장 먼저** 띄운다 — 무거운 것을 불러오기 전에."""
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.widgets.startup_splash import StartupSplash

    dpr = 1.0
    try:
        scr = app.primaryScreen()
        if scr is not None:
            dpr = scr.devicePixelRatio() or 1.0
    except Exception:
        pass
    splash = StartupSplash(_splash_logo(int(StartupSplash.LOGO_W * dpr)))
    splash.show()
    # 총량을 모르는 구간 → busy(무한 진행).  0 에 멈춰 있으면 안 된다(CLAUDE.md).
    splash.set_progress(0, 0, i18n.KO.SPLASH_MODULES)
    app.processEvents()
    return splash


def _open_main_window(splash):
    """메인 창 생성(페이지 진행을 스플래시에 보고) → 표시 → 스플래시 걷기."""
    from aoi_verification.app import i18n
    from aoi_verification.app.ui.main_window import MainWindow

    window = MainWindow(progress=splash.set_progress)
    window.show()
    splash.set_progress(1, 1, i18n.KO.SPLASH_READY)
    splash.finish(window)
    return window


def _ensure_deps_installed() -> bool:
    """lite 배포의 **첫 실행**: 라이브러리가 아직 없으면 pip 로 설치한다.  True=계속 진행.

    빌드가 라이브러리를 일부러 넣지 않고 표식(``.deps_installed``)도 남기지 않으므로,
    표식이 없다는 것이 곧 '설치하라' 는 신호다(``build.py exe-lite``).  전체 배포본은
    빌드가 표식을 남겨 두므로 이 함수는 **아무것도 하지 않고 지나간다.**

    ★ PyQt6 를 import 하기 **전에** 불러야 한다 — 없는 상태로 import 하면 그 자리에서
      죽어서, 설치할 기회 자체가 사라진다.  그래서 GUI 가 아니라 콘솔로 안내한다
      (런처가 첫 실행에 한해 ``python.exe`` 로 띄워 이 출력이 보이게 한다)."""
    import subprocess

    from aoi_verification.app.utils import bootstrap, paths

    root = paths.install_root()
    if root is None:
        return True                    # 개발/포터블 — 사용자가 직접 관리한다
    return bootstrap.ensure_deps(
        root, sys.executable, root / "app" / "requirements.txt",
        run=lambda cmd: subprocess.call(cmd),
        log=lambda m: print("[AOI]", m, flush=True),
    )


def main() -> int:
    _ensure_package_on_path()
    try:
        deps_ok = _ensure_deps_installed()
    except Exception:
        deps_ok = True   # 의존성 판단이 잘못돼도 앱 실행 자체를 막지는 않는다
    if not deps_ok:
        print("\n[AOI] 설치가 끝나지 않아 앱을 시작할 수 없습니다. "
              "이 창의 오류 메시지를 알려주세요.", flush=True)
        try:
            input("계속하려면 Enter 를 누르세요...")   # 창이 즉시 닫혀 못 읽는 것 방지
        except Exception:
            pass         # 콘솔이 없는 실행 경로(pythonw) — 대기할 수 없을 뿐이다
        return 4

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
    app.setFont(QFont("Pretendard, Noto Sans KR, Malgun Gothic, Segoe UI"))
    _apply_app_icon(app)
    _load_stylesheet(app)

    # 스플래시(로고) → 윈도우 ---------------------------------------------
    # ★ 여기서 무거운 모듈을 미리 불러오지 않는다.  `main_window` 는 이제 가볍고
    #   (cv2·OpenVINO·numpy·PIL 을 끌고 오지 않는다), 창은 첫 화면만으로 곧바로
    #   뜬다.  나머지는 창이 뜬 뒤 `MainWindow` 가 백그라운드에서 불러오고, 그동안
    #   '검증 시작' 버튼이 '준비 중' 으로 잠긴다.
    splash = _show_splash(app)

    # 좀비 윈도우 방지를 위해 함수 로컬에 둔다.
    window_ref = [_open_main_window(splash)]

    # 외부에서 만든 app 을 재사용한 경우엔 우리가 루프를 굴리지 않는다(IDE 콘솔).
    if not created_here:
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
